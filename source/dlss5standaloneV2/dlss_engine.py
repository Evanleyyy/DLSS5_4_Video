#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dlss_engine.py — ctypes wrapper around dlssnr_host.dll (Feature 18 / DLSS5 NR).

The bundled native host exposes one process-global session. This wrapper
enforces exclusive ownership, validates buffers, and reports native failures.
Native binaries are supplied by the upstream release and have no source here.
"""
import ctypes
import os
import sys
import threading
import numpy as np


def _res_dir():
    """Where the NGX runtime DLLs live: bundle dir when frozen (PyInstaller), else script dir."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


PACKAGE_DIR = _res_dir()
HOST_DLL = os.path.join(PACKAGE_DIR, "dlssnr_host.dll")
DLSSNR_DLL = os.path.join(PACKAGE_DIR, "nvngx_dlssnr.dll")
LOG_PATH = os.path.join(os.environ.get("DLSS5_LOG_DIR", PACKAGE_DIR), "dlssnr_run.log")

_lib = None
_w = _h = 0
_session_lock = threading.RLock()
_active_session = None


def _read_log():
    if not os.path.exists(LOG_PATH):
        return ""
    with open(LOG_PATH, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _load():
    global _lib
    if _lib is None:
        if not os.path.exists(HOST_DLL):
            raise FileNotFoundError(f"missing {HOST_DLL}")
        _lib = ctypes.CDLL(HOST_DLL)
        _lib.dlssnr_init.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_wchar_p]
        _lib.dlssnr_init.restype = ctypes.c_int
        _lib.dlssnr_create_feature.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        _lib.dlssnr_create_feature.restype = ctypes.c_int
        _lib.dlssnr_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        _lib.dlssnr_process.restype = ctypes.c_int
        _lib.dlssnr_set_options.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_float,
            ctypes.c_float, ctypes.c_float, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_float, ctypes.c_float]
        _lib.dlssnr_set_options.restype = None
        _lib.dlssnr_shutdown.argtypes = []
        _lib.dlssnr_shutdown.restype = None
    return _lib





def probe(w=640, h=360, preset=1):
    """init (gate bypass) — returns the host log tail for diagnostics."""
    with _session_lock:
        if _active_session is not None:
            raise RuntimeError("DLSS 正在处理，请等待当前任务完成")
        lib = _load()
        r = lib.dlssnr_init(w, h, preset, DLSSNR_DLL, LOG_PATH)
        if r:
            lib.dlssnr_shutdown()
        return r, _read_log()


def run_dlss(video, rgba_frames, motion, depth, preset=1, settings=None, reset=True, progress=None):
    """
    Create the Feature 18 and evaluate every frame. rgba_frames: HxWx4 uint8,
    motion: HxWx2 float32, depth: HxW float32. settings is a dict with optional
    keys: preset, style, intensity, local_tone/local_struct/skin_struct,
    use_auto_mask, ui_correction, guidance_mode, depth_convention,
    motion_scale_x/y. Returns HxWx4 uint8.
    """
    if not rgba_frames:
        raise ValueError("没有可处理的视频帧")
    if not (len(rgba_frames) == len(motion) == len(depth)):
        raise ValueError("图像、光流和深度帧数不一致")
    h, w = rgba_frames[0].shape[:2]
    out = []
    session = Live(w, h, {"preset": preset, **(settings or {})})
    try:
        for i, (rgba, mv, dp) in enumerate(zip(rgba_frames, motion, depth)):
            out.append(session.process(rgba, mv, dp, reset=reset and i == 0))
            if progress:
                progress(i + 1, len(rgba_frames), "ok")
    finally:
        session.close()
    return out


class Live:
    """Persistent single-frame DLSS session for realtime preview. init+create FEATURE
    once; then process() per frame. Eval params (style/intensity/guidance/...) apply at
    the next process() for live tweaking; changing the preset re-creates the feature
    (it is a create-time param). Call close() when done (frees the D3D12 device)."""
    def __init__(self, w, h, settings=None):
        if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
            raise ValueError("DLSS 图像尺寸必须是正整数")
        self._w, self._h = w, h
        self.settings = dict(settings or {})
        self.runtime_path = DLSSNR_DLL
        if 'runtime_version' in self.settings:
            import dlss_runtime
            self.runtime_path = dlss_runtime.resolve(self.settings['runtime_version'])['path']
        self._lib = _load()
        self._initialized = False
        self._open()

    def _teardown(self):
        """Release the D3D12/NVNGX session ONLY if one is currently active.

        dlssnr_host.dll's dlssnr_shutdown is NOT safe to call with no active session:
        double-shutdown (e.g. close() then a fresh _open() that also shuts down, with no
        dlssnr_init in between) triggers a native access violation that a Python
        try/except cannot catch. Tracking the session state makes teardown idempotent.
        """
        global _active_session
        with _session_lock:
            if self._initialized and _active_session is self:
                self._lib.dlssnr_shutdown()
                self._initialized = False
                _active_session = None

    def _open(self):
        global _active_session
        with _session_lock:
            if _active_session is not None and _active_session is not self:
                raise RuntimeError("DLSS 正在处理，请等待当前任务完成")
            s = self.settings
            self._teardown()
            self._apply()
            if not self._lib.dlssnr_init(self._w, self._h, int(s.get('preset', 1)), self.runtime_path, LOG_PATH):
                raise RuntimeError("dlssnr_init failed (D3D12/gate). See dlssnr_run.log")
            self._initialized = True
            _active_session = self
            try:
                if not self._lib.dlssnr_create_feature(self._w, self._h, int(s.get('preset', 1))):
                    raise RuntimeError("Feature 18 create failed.\n" + _read_log()[-800:])
            except Exception:
                self._teardown()
                raise

    def _apply(self):
        s = self.settings
        self._lib.dlssnr_set_options(
            int(s.get('preset', 1)), int(s.get('style', 0)), float(s.get('intensity', 1.0)),
            float(s.get('local_tone', 1.0)), float(s.get('local_struct', 1.0)), float(s.get('skin_struct', 1.0)),
            int(s.get('use_auto_mask', 1)), int(s.get('ui_correction', 0)),
            int(s.get('guidance_mode', 3)), int(s.get('depth_convention', 2)),
            float(s.get('motion_scale_x', 1.0)), float(s.get('motion_scale_y', 1.0)))

    def update(self, settings):
        """Apply live-edit settings. Recreates the feature if the preset changed."""
        with _session_lock:
            if not self._initialized:
                raise RuntimeError("DLSS 会话已关闭")
            if settings.get('runtime_version', 'auto') != self.settings.get('runtime_version', 'auto'):
                raise RuntimeError('切换运行库需要新的隔离进程')
            old_preset = self.settings.get('preset')
            self.settings.update(settings)
            if self.settings.get('preset') != old_preset:
                self._open()

    def process(self, rgba, motion, depth, reset=False):
        arrays = []
        for name, array, shape, dtype in (
            ("图像", rgba, (self._h, self._w, 4), np.uint8),
            ("光流", motion, (self._h, self._w, 2), np.float32),
            ("深度", depth, (self._h, self._w), np.float32),
        ):
            if not isinstance(array, np.ndarray) or array.shape != shape or array.dtype != dtype:
                raise ValueError(f"{name}数据必须是 {shape} / {np.dtype(dtype)}")
            if dtype == np.float32 and not np.isfinite(array).all():
                raise ValueError(f"{name}数据包含无效数值")
            arrays.append(np.ascontiguousarray(array))
        rgba, motion, depth = arrays
        with _session_lock:
            if not self._initialized or _active_session is not self:
                raise RuntimeError("DLSS 会话已关闭")
            self._apply()
            o = np.zeros_like(rgba)
            ok = self._lib.dlssnr_process(
                rgba.ctypes.data_as(ctypes.c_void_p),
                motion.ctypes.data_as(ctypes.c_void_p),
                depth.ctypes.data_as(ctypes.c_void_p),
                o.ctypes.data_as(ctypes.c_void_p), 1 if reset else 0)
            if not ok:
                raise RuntimeError("DLSS 帧处理失败，请查看 dlssnr_run.log")
            return o

    def close(self):
        self._teardown()
