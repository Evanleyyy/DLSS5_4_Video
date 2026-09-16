#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — offline depth + optical-flow + DLSS + video export engine for the
test3 DLSS tool. Reuses Depth Anything V2 (Large) and RAFT (Large) that are
already downloaded under dlss5standalone. Depth/flow are cached to disk so a
re-run skips the two slow model passes and goes straight to DLSS/export.

Output layout, given a video at <video_dir>/<name>.mp4:
    <video_dir>/<name>_depth/<name>.depth/000000.png ...  16-bit depth (0=far..65535=near)
    <video_dir>/<name>_flow /<name>.flow /000000.flo  ...  Middlebury float32 HxWx2
    <video_dir>/<name>_dlss /000000.png ...               DLSS5 Feature18 output (needs SDK)

CLI:  python3 pipeline.py <video> [--depth] [--flow] [--dlss] [--export] [--frames N]
"""
import argparse
import os
import shutil
import subprocess
import task_control
import sys
import time
import json
import tempfile

import cv2
import numpy as np
from cache_manager import uses_video_cache

# ---- model / asset paths (all bundled relative to this folder; portable) ----
if getattr(sys, 'frozen', False):
    BASE = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TORCH_HOME", os.path.join(BASE, "torch_home"))
DAV2_DIR = os.path.join(BASE, "models")                    # contains depth_anything_v2/
DAV2_CKPT = os.path.join(BASE, "models", "checkpoints", "depth_anything_v2_vitl.pth")
HOST_DLL = os.path.join(BASE, "dlssnr_host.dll")
DLSSNR_DLL = os.path.join(BASE, "nvngx_dlssnr.dll")
RAFT_PTH = os.path.join(BASE, "torch_home", "hub", "checkpoints", "raft_large_C_T_SKHT_V2-ff5fadd5.pth")

DEVICE = "cpu"                 # placeholder; resolved lazily when torch is present
_TORCH_OK = None

def _torch_available():
    """Return True if torch is importable on this machine (the exe may ship without it)."""
    global _TORCH_OK, DEVICE
    if _TORCH_OK is None:
        try:
            import torch
            DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
            _TORCH_OK = True
        except Exception:
            _TORCH_OK = False
    return _TORCH_OK

def _require_torch(what):
    if not _torch_available():
        raise RuntimeError(f"{what} 需要 torch/深度光流模型，但当前程序未内嵌（请用完整版或外置模型）")

from contextlib import contextmanager

@contextmanager
def amp():
    """fp16 mixed-precision autocast on GPU; no-op on CPU / when torch is absent."""
    if DEVICE == "cuda" and _torch_available():
        import torch
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            yield
    else:
        yield


# ----------------------------- helpers -----------------------------
def imread(path, flags=cv2.IMREAD_COLOR):
    """OpenCV file APIs do not reliably support Windows Unicode paths."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, flags) if data.size else None
    except OSError:
        return None


def imwrite(path, image):
    extension = os.path.splitext(path)[1] or '.png'
    ok, data = cv2.imencode(extension, image)
    if not ok:
        raise OSError(f"图片编码失败: {path}")
    data.tofile(path)
    return True


def _cache_record(video, options):
    stat = os.stat(video)
    return {'source': os.path.abspath(video), 'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns, 'options': options, 'revision': 2}


def _begin_cache(directory, record):
    path = os.path.join(directory, 'cache.json')
    try:
        with open(path, encoding='utf-8') as handle:
            old = json.load(handle)
    except (OSError, ValueError):
        old = None
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'complete': False, 'record': record}, handle)
    return old == {'complete': True, 'record': record}


def _finish_cache(directory, record):
    with open(os.path.join(directory, 'cache.json'), 'w', encoding='utf-8') as handle:
        json.dump({'complete': True, 'record': record}, handle)


def video_info(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"无法打开视频: {path}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return n, fps, w, h


def iter_frames(path, limit=None):
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise ValueError(f"无法打开视频: {path}")
        i = 0
        while limit is None or i < limit:
            task_control.checkpoint()
            ok, f = cap.read()
            if not ok:
                break
            yield i, f
            i += 1
    finally:
        cap.release()


def write_flo(path, flow):
    h, w = flow.shape[:2]
    with open(path, "wb") as f:
        f.write(b"PIEH")
        f.write(np.array([w, h], dtype=np.int32).tobytes())
        f.write(flow.astype(np.float32).tobytes())


def read_flo(path):
    with open(path, "rb") as f:
        assert f.read(4) == b"PIEH"
        w, h = np.fromfile(f, dtype=np.int32, count=2)
        return np.fromfile(f, dtype=np.float32).reshape(h, w, 2)


# Depth cache: 8-bit JPEG (compact, fast) — 0=far..255=near. Accepts 16-bit PNG too.
DEPTH_EXT = ".jpg"
FLOW_ITERS = 6   # RAFT iterative refinement steps (default 12; 6 ~= 2x faster, fine for DLSS guidance)

def _round8(x):
    """Round to the nearest multiple of 8 (min 8) so RAFT accepts the dims."""
    return max(8, int(round(x / 8.0)) * 8)

def read_depth(path):
    """Decode a cached depth image to [0,1] float (0=far..1=near)."""
    img = imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    d = img.astype(np.float32)
    if img.dtype == np.uint16:
        d /= 65535.0
    elif img.dtype == np.uint8:
        d /= 255.0
    return d


def find_depth(dirpath, idx):
    """Locate the cached depth image for frame idx (JPEG first, then legacy PNG)."""
    for ext in (DEPTH_EXT, ".png"):
        p = os.path.join(dirpath, f"{idx:06d}" + ext)
        if os.path.exists(p):
            return p
    return None


def colorize_flow(flow):
    mag = np.linalg.norm(flow, axis=2)
    ang = (np.arctan2(flow[..., 1], flow[..., 0]) + np.pi) / (2 * np.pi)
    hsv = np.zeros((*flow.shape[:2], 3), dtype=np.uint8)
    hsv[..., 0] = (ang * 179).astype(np.uint8)
    hsv[..., 1] = 255
    hsv[..., 2] = (np.clip(mag / (np.percentile(mag, 98) + 1e-6), 0, 1) * 255).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def colorize_depth(d):
    d = d.astype(np.float32)
    if d.max() > 1.5:
        d = d / (65535.0 if d.max() > 255 else 255.0)
    g = (np.clip(d, 0, 1) * 255).astype(np.uint8)
    return cv2.applyColorMap(g, cv2.COLORMAP_INFERNO)


def out_dirs(video):
    """Return (depth_dir, flow_dir, dlss_dir) for a given video path."""
    base = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0]
    return (os.path.join(base, stem + "_depth"), os.path.join(base, stem + "_flow"),
            os.path.join(base, stem + "_dlss"))


# ----------------------------- depth -----------------------------
_depth_model = None
def get_depth_model():
    global _depth_model
    if _depth_model is None:
        _require_torch("深度生成")
        sys.path.insert(0, DAV2_DIR)
        from depth_anything_v2.dpt import DepthAnythingV2
        import torch
        cfg = {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]}
        m = DepthAnythingV2(**cfg)
        m.load_state_dict(torch.load(DAV2_CKPT, map_location="cpu", weights_only=True))
        m = m.to(DEVICE).eval()
        _depth_model = m
    return _depth_model


def _all_cached(dirpath, total, ext):
    for i in range(total):
        if not os.path.exists(os.path.join(dirpath, f"{i:06d}.{ext}")):
            return False
    return total > 0


def infer_depth_frame(frame, edge=720, model=None):
    """Return normalized full-resolution depth for one BGR image."""
    import torch
    model = get_depth_model() if model is None else model
    h, w = frame.shape[:2]
    short_edge = min(h, w)
    scale = min(1.0, edge / short_edge) if edge else 1.0
    fw = _round8(min(w, max(int(w * scale), 8)))
    fh = _round8(min(h, max(int(h * scale), 8)))
    bgr_small = cv2.resize(frame, (fw, fh)) if (fw, fh) != (w, h) else frame
    with torch.no_grad(), amp():
        depth = model.infer_image(bgr_small)
    d = depth.astype(np.float32)
    d = (d - d.min()) / max(d.max() - d.min(), 1e-6)
    if (fw, fh) != (w, h):
        d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)   # depth: no value scaling needed
    return d


@uses_video_cache
def generate_depth(video, frame_limit=None, progress=None, cancel=None, force=False, edge=720):
    """Cache depth/<i>.jpg (8-bit, 0=far..255=near) for every frame. Computed with the
    SHORT edge clamped to `edge` (720) regardless of aspect ratio, then upscaled back to
    the full frame size. A video whose short edge is already <= `edge` is never upscaled."""
    depth_dir, _, _ = out_dirs(video)
    os.makedirs(depth_dir, exist_ok=True)
    total = video_info(video)[0]
    n = frame_limit if frame_limit else total
    record = _cache_record(video, {'kind': 'depth', 'edge': edge, 'frames': n})
    force = not _begin_cache(depth_dir, record) or force
    if not force and _all_cached(depth_dir, n, DEPTH_EXT.lstrip(".")):
        if progress: progress(n, n, "cached")
        _finish_cache(depth_dir, record)
        return n
    task_control.checkpoint()
    model = get_depth_model()
    import torch
    done = 0
    for i, frame in iter_frames(video, frame_limit if frame_limit else None):
        p = os.path.join(depth_dir, f"{i:06d}" + DEPTH_EXT)
        if os.path.exists(p) and not force:
            if progress: progress(i, total, "cached")
            done += 1
            continue
        d = infer_depth_frame(frame, edge=edge, model=model)
        d8 = (np.clip(d, 0, 1) * 255.0).astype(np.uint8)
        imwrite(p, d8)
        done += 1
        if progress: progress(i, total, "ok")
    _finish_cache(depth_dir, record)
    return done


# ----------------------------- flow -----------------------------
_flow_model = None
def get_flow_model():
    global _flow_model
    if _flow_model is None:
        _require_torch("光流生成")
        import torch
        from torchvision.models.optical_flow import Raft_Large_Weights, raft_large
        w = Raft_Large_Weights.DEFAULT
        # Offline: construct the (identical) architecture then load the bundled
        # state dict directly, so torchvision never tries to download from the net.
        m = raft_large(weights=None).to(DEVICE).eval()
        m.load_state_dict(torch.load(RAFT_PTH, map_location="cpu", weights_only=True))
        _flow_model = (m, w.transforms())
    return _flow_model


@uses_video_cache
def generate_flow(video, frame_limit=None, progress=None, cancel=None, force=False, edge=720):
    """Cache flow/*.flo for every frame. Frame 0 = zero flow.
    The SHORT edge is clamped to `edge` (720) regardless of aspect ratio; flow is computed
    downscaled and then upscaled back to the full frame size (values scaled by 1/scale).
    This bounds RAFT's O((W*H)^2) correlation volume so high-res input doesn't blow up VRAM.
    A video whose short edge is already <= `edge` is never upscaled."""
    _, flow_dir, _ = out_dirs(video)
    os.makedirs(flow_dir, exist_ok=True)
    import torch
    total = video_info(video)[0]
    n = frame_limit if frame_limit else total
    record = _cache_record(video, {'kind': 'flow', 'edge': edge, 'frames': n})
    force = not _begin_cache(flow_dir, record) or force
    if not force and _all_cached(flow_dir, n, "flo"):
        if progress: progress(n, n, "cached")
        _finish_cache(flow_dir, record)
        return n
    task_control.checkpoint()
    (model, transforms) = get_flow_model()

    def fed_dims(img):
        h, w = img.shape[:2]
        short_edge = min(h, w)
        scale = min(1.0, edge / short_edge) if edge else 1.0
        fw = _round8(min(w, max(int(w * scale), 8)))
        fh = _round8(min(h, max(int(h * scale), 8)))
        return fw, fh

    def infer(prev_bgr, cur_bgr):
        h, w = cur_bgr.shape[:2]
        fw, fh = fed_dims(cur_bgr)
        if (fw, fh) != (w, h):
            p1 = cv2.resize(cur_bgr, (fw, fh)); p2 = cv2.resize(prev_bgr, (fw, fh))
        else:
            p1, p2 = cur_bgr, prev_bgr
        img1 = torch.from_numpy(p1[:, :, ::-1].copy()).permute(2, 0, 1).float().unsqueeze(0) / 255.0
        img2 = torch.from_numpy(p2[:, :, ::-1].copy()).permute(2, 0, 1).float().unsqueeze(0) / 255.0
        img1, img2 = transforms(img1, img2)
        with torch.no_grad(), amp():
            fl = model(img1.to(DEVICE), img2.to(DEVICE), num_flow_updates=FLOW_ITERS)[-1]
        fl = fl[0].permute(1, 2, 0).cpu().float().numpy()   # (fh, fw, 2)
        if (fw, fh) != (w, h):
            fl = cv2.resize(fl, (w, h), interpolation=cv2.INTER_LINEAR)
            fl[..., 0] *= w / fw                            # displacement in full-res px
            fl[..., 1] *= h / fh
        return fl  # Current -> previous flow, evaluated on the current frame grid.

    prev = None
    for i, cur in iter_frames(video, frame_limit if frame_limit else None):
        if prev is not None:
            p = os.path.join(flow_dir, f"{i:06d}.flo")
            if not (os.path.exists(p) and not force):
                write_flo(p, infer(prev, cur))
            if progress: progress(i, total, "ok")
        else:
            p = os.path.join(flow_dir, f"000000.flo")
            if force or not os.path.exists(p):
                write_flo(p, np.zeros((cur.shape[0], cur.shape[1], 2), dtype=np.float32))
            if progress: progress(0, total, "ok")
        prev = cur
    _finish_cache(flow_dir, record)
    return min(n, total)


# ----------------------------- export -----------------------------
def _bundle_ffmpeg():
    """Locate ffmpeg.exe — bundled next to the exe when frozen, else from PATH."""
    if getattr(sys, 'frozen', False):
        p = os.path.join(getattr(sys, '_MEIPASS', ''), 'ffmpeg.exe')
        if os.path.exists(p):
            return p
    local = os.path.abspath(os.path.join(BASE, '..', '..', 'runtime', 'ffmpeg.exe'))
    return local if os.path.isfile(local) else shutil.which("ffmpeg")


@uses_video_cache
def generate_dlss(video, settings=None, frame_limit=None, progress=None):
    """Process and save one frame at a time, with bounded host memory."""
    task_control.checkpoint()
    import dlss_layers
    settings = dlss_layers.normalize_settings(settings)
    need_depth, need_flow = dlss_layers.guidance_needs(settings)
    n, _, w, h = video_info(video)
    n = min(n, frame_limit) if frame_limit else n
    if n <= 0:
        raise ValueError("视频没有可处理的帧")
    dm, fm, directory = out_dirs(video)
    os.makedirs(directory, exist_ok=True)
    record = _cache_record(video, {'kind': 'dlss', 'settings': settings, 'frames': n})
    if settings['super_resolution']['engine'] != 'dlss':
        import sr_backend
        import sr_settings
        record['options']['model_assets'] = sr_settings.fingerprint(settings['super_resolution'])
        _begin_cache(directory, record)
        result = sr_backend.process_video(video, directory, settings, n, progress)
        _finish_cache(directory, record)
        return result['count']
    _begin_cache(directory, record)
    tw, th = (w + 7) // 8 * 8, (h + 7) // 8 * 8
    live = dlss_layers.LayeredLive(tw, th, settings)
    done = 0
    try:
        for i, frame in iter_frames(video, n):
            dp = find_depth(dm, i) if need_depth else None
            fp = os.path.join(fm, f'{i:06d}.flo')
            if need_depth and not dp:
                raise ValueError(f"缺少第 {i} 帧深度，请先生成深度")
            if need_flow and not os.path.isfile(fp):
                raise ValueError(f"缺少第 {i} 帧光流，请先生成光流")
            depth = read_depth(dp) if need_depth else np.zeros((h, w), np.float32)
            flow = read_flo(fp) if need_flow else np.zeros((h, w, 2), np.float32)
            if depth is None or depth.shape != (h, w) or flow.shape != (h, w, 2):
                raise ValueError("缓存尺寸与视频不一致，请重新生成深度和光流")
            rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            if (tw, th) != (w, h):
                rgba = cv2.copyMakeBorder(rgba, 0, th-h, 0, tw-w, cv2.BORDER_REPLICATE)
                depth = cv2.copyMakeBorder(depth, 0, th-h, 0, tw-w, cv2.BORDER_REPLICATE)
                flow = cv2.copyMakeBorder(flow, 0, th-h, 0, tw-w, cv2.BORDER_REPLICATE)
            result = live.process(rgba, flow, depth, reset=i == 0)
            imwrite(os.path.join(directory, f'{i:06d}.png'),
                    cv2.cvtColor(result[:h, :w], cv2.COLOR_RGBA2BGR))
            done += 1
            if progress:
                progress(done, n, 'ok')
        if done != n:
            raise RuntimeError(f"视频提前结束，预期 {n} 帧，实际 {done} 帧")
        from frame_sequence import validate_frames
        validate_frames(directory, n, (w, h))
        _finish_cache(directory, record)
        return done
    finally:
        live.close()


def validate_dlss_frames(video, settings, frames):
    from frame_sequence import validate_frames
    from sr_settings import normalize
    _, _, width, height = video_info(video)
    cfg = normalize(settings.get('super_resolution'))
    scale = 1 if cfg['engine'] == 'dlss' else cfg['scale']
    validate_frames(out_dirs(video)[2], frames, (width * scale, height * scale))


def dlss_cache_matches(video, settings, frame=0):
    """Never show an older one/two-pass result under a different parameter panel."""
    from dlss_layers import settings_key
    try:
        with open(os.path.join(out_dirs(video)[2], 'cache.json'), encoding='utf-8') as handle:
            cached = json.load(handle)
        record = cached['record']
        options = record['options']
        import sr_settings
        if settings.get('super_resolution', {}).get('engine', 'dlss') != 'dlss':
            assets = json.loads(json.dumps(sr_settings.fingerprint(settings['super_resolution'])))
            if options.get('model_assets') != assets:
                return False
        return (cached['complete'] is True and options['kind'] == 'dlss' and
                record == _cache_record(video, options) and options['frames'] > frame and
                settings_key(options['settings']) == settings_key(settings))
    except (OSError, ValueError, KeyError, TypeError):
        return False


@uses_video_cache
def export_video(video, kind, frames=None, fps=30.0, crf=18, with_audio=True):
    """Validate every frame and publish output only after the encoder succeeds."""
    if kind not in ('depth', 'flow', 'dlss'):
        raise ValueError(f"不支持的导出类型: {kind}")
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("帧率必须大于零")
    n = video_info(video)[0]
    frames = n if frames is None else min(frames, n)
    if frames <= 0:
        return None
    directory = out_dirs(video)[{'depth': 0, 'flow': 1, 'dlss': 2}[kind]]
    paths = []
    for i in range(frames):
        p = find_depth(directory, i) if kind == 'depth' else os.path.join(
            directory, f'{i:06d}' + ('.flo' if kind == 'flow' else '.png'))
        if not p or not os.path.isfile(p):
            raise ValueError(f"缺少第 {i} 帧，导出已中止，请先完成全部帧处理")
        paths.append(p)
    manifest = os.path.join(directory, 'cache.json')
    if os.path.isfile(manifest):
        with open(manifest, encoding='utf-8') as handle:
            state = json.load(handle)
        if not state.get('complete'):
            raise ValueError("上次处理未完成，请重新生成后导出")
        record = state['record']
        if record != _cache_record(video, record['options']):
            raise ValueError("原视频已变更，请重新生成后导出")

    def load(p):
        if kind == 'depth':
            d = read_depth(p)
            return colorize_depth(d) if d is not None else None
        return colorize_flow(read_flo(p)) if kind == 'flow' else imread(p)

    first = load(paths[0])
    if first is None:
        raise ValueError(f"无法读取输出帧: {paths[0]}")
    from media_export import encode_video
    def images():
        for index, path in enumerate(paths):
            image = first if index == 0 else load(path)
            if image is None or image.shape != first.shape:
                raise ValueError(f"帧损坏或尺寸不一致: {path}")
            yield image
    return encode_video(images(), frames, fps, os.path.splitext(video)[0] + '_' + kind + '.mp4',
                        audio_source=video if with_audio else None,
                        crf=18 if crf is None else crf, ffmpeg=_bundle_ffmpeg())


# ----------------------------- CLI -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--depth", action="store_true", help="run depth (cached)")
    ap.add_argument("--flow", action="store_true", help="run flow (cached)")
    ap.add_argument("--dlss", action="store_true", help="run streaming DLSS")
    ap.add_argument("--guidance", type=int, choices=[0, 1, 2, 3], default=0)
    ap.add_argument("--export", type=str, default=None, choices=["depth", "flow", "dlss"],
                    help="export a preview video")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--edge", type=int, default=720,
                    help="clamp SHORT edge for depth+flow compute (short edge=720p; default 720)")
    args = ap.parse_args()

    n, fps, w, h = video_info(args.video)
    print(f"[info] {args.video}: {n} frames @ {fps:.2f} fps, {w}x{h}, device={DEVICE}")

    if args.depth:
        t0 = time.time()
        d = generate_depth(args.video, args.frames, force=args.force, edge=args.edge)
        print(f"[depth] done {d} frames ({time.time()-t0:.1f}s) -> {out_dirs(args.video)[0]}")

    if args.flow:
        t0 = time.time()
        generate_flow(args.video, args.frames, force=args.force, edge=args.edge)
        print(f"[flow] done ({time.time()-t0:.1f}s) -> {out_dirs(args.video)[1]}")

    if args.dlss:
        generate_dlss(args.video, {'guidance_mode': args.guidance}, args.frames)

    if args.export:
        p = export_video(args.video, args.export, args.frames, fps)
        print(f"[export] {args.export} -> {p}")


if __name__ == "__main__":
    main()
