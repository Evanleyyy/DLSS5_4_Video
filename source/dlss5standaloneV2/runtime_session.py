"""Persistent, isolated native renderer. One runtime and temporal history per child."""
import multiprocessing
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
import dlss_engine
import dlss_runtime
from process_lifetime import _WindowsJob


def _worker(connection, width, height, settings, log_path):
    live = None
    try:
        # Parent assigns ownership before any native initialization.
        if connection.recv() != 'start':
            return
        dlss_engine.LOG_PATH = log_path
        live = dlss_engine.Live(width, height, settings)
        connection.send(('ready', None))
        while True:
            command, payload = connection.recv()
            if command == 'close':
                break
            if command != 'process':
                raise ValueError('未知的渲染命令')
            settings, rgba, flow, depth, reset = payload
            live.update(settings)
            output = live.process(rgba, flow, depth, reset)
            connection.send(('result', output))
    except EOFError:
        pass
    except Exception as error:
        try:
            connection.send(('error', str(error)))
        except (EOFError, OSError):
            pass
    finally:
        try:
            if live is not None:
                live.close()
        finally:
            connection.close()


class Live:
    def __init__(self, width, height, settings=None, timeout=300):
        self.width, self.height = width, height
        self.settings = dict(settings or {})
        self.timeout = timeout
        self._lock = threading.RLock()
        self._closed = False
        self._next_reset = True
        self._job = None
        self._process = None
        self._connection = None
        self._start()

    def _start(self):
        info = dlss_runtime.resolve(self.settings.get('runtime_version', 'auto'))
        import pipeline
        pipeline.release_guidance_models()
        self.runtime = info
        # Pin auto-selection for this worker's entire lifetime.
        self._worker_settings = {**self.settings, 'runtime_version': info['id']}
        log_dir = Path(os.environ.get('DLSS5_LOG_DIR', dlss_runtime.sr_settings.data_root() / 'logs'))
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = str(log_dir / f'dlssnr_{info["id"]}_{uuid.uuid4().hex[:10]}.log')
        context = multiprocessing.get_context('spawn')
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker,
            args=(child, self.width, self.height, self._worker_settings, self.log_path), daemon=True)
        try:
            self._process.start()
            child.close()
            if os.name == 'nt':
                self._job = _WindowsJob()
                # multiprocessing exposes its native process handle through the sentinel.
                class ProcessHandle:
                    _handle = self._process.sentinel
                self._job.assign(ProcessHandle())
            self._connection.send('start')
            self._receive('ready')
        except BaseException:
            child.close()
            self._stop(force=True)
            raise

    def _receive(self, expected):
        deadline = time.monotonic() + self.timeout
        while not self._connection.poll(.1):
            if not self._process.is_alive():
                raise RuntimeError(f'DLSS 渲染进程退出（{self._process.exitcode}），日志：{self.log_path}')
            if time.monotonic() >= deadline:
                raise RuntimeError(f'DLSS 单步处理超过 {self.timeout} 秒，请降低分辨率；日志：{self.log_path}')
        try:
            status, value = self._connection.recv()
        except (EOFError, OSError) as error:
            raise RuntimeError(f'DLSS 渲染连接中断，日志：{self.log_path}') from error
        if status != expected:
            raise RuntimeError(f'{value}\n日志：{self.log_path}')
        return value

    def update(self, settings):
        with self._lock:
            if self._closed:
                raise RuntimeError('DLSS 会话已关闭')
            updated = {**self.settings, **settings}
            if updated.get('runtime_version', 'auto') != self.settings.get('runtime_version', 'auto'):
                self._stop()
                self.settings = updated
                try:
                    self._start()
                except BaseException:
                    self._closed = True
                    raise
                self._next_reset = True
            else:
                self.settings = updated
                self._worker_settings = {**updated, 'runtime_version': self.runtime['id']}

    def process(self, rgba, motion, depth, reset=False):
        with self._lock:
            if self._closed:
                raise RuntimeError('DLSS 会话已关闭')
            for value, shape, dtype in ((rgba, (self.height, self.width, 4), np.uint8),
                                        (motion, (self.height, self.width, 2), np.float32),
                                        (depth, (self.height, self.width), np.float32)):
                if not isinstance(value, np.ndarray) or value.shape != shape or value.dtype != dtype:
                    raise ValueError('渲染输入尺寸或类型不正确')
            try:
                self._connection.send(('process', (self._worker_settings, rgba, motion, depth,
                                                   bool(reset or self._next_reset))))
                result = self._receive('result')
                if not isinstance(result, np.ndarray) or result.shape != rgba.shape or result.dtype != np.uint8:
                    raise RuntimeError('渲染进程返回了无效图像')
                self._next_reset = False
                return result
            except BaseException:
                self.close(force=True)
                raise

    def _stop(self, force=False):
        process = self._process
        try:
            if process is not None and process.pid is not None:
                if process.is_alive() and not force:
                    try:
                        self._connection.send(('close', None))
                    except (EOFError, OSError, BrokenPipeError):
                        pass
                    process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                process.close()
        finally:
            if self._job is not None:
                self._job.close()
                self._job = None
            if self._connection is not None:
                self._connection.close()
            self._process = self._connection = None

    def close(self, force=False):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._stop(force)


def selftest(version):
    """Measure actual output, never report only successful DLL loading as support."""
    start = time.monotonic()
    width, height = 320, 240
    rgba = np.empty((height, width, 4), np.uint8)
    rgba[..., 0] = np.arange(width, dtype=np.uint8)
    rgba[..., 1] = np.arange(height, dtype=np.uint8)[:, None]
    rgba[..., 2] = 80
    rgba[..., 3] = 255
    flow = np.zeros((height, width, 2), np.float32)
    depth = np.zeros((height, width), np.float32)
    live = Live(width, height, {'runtime_version': version, 'guidance_mode': 0})
    try:
        outputs = [live.process(rgba, flow, depth, reset=index == 0) for index in range(3)]
        change = float(np.abs(outputs[-1][..., :3].astype(float) - rgba[..., :3]).mean())
        if change <= .001 or not outputs[-1][..., :3].any():
            raise RuntimeError('自检未检测到有效的神经渲染输出')
        return {'gpu': dlss_runtime.gpu_info(), 'runtime': live.runtime,
                'frames': len(outputs), 'mean_pixel_change': change,
                'seconds': round(time.monotonic() - start, 3), 'log': live.log_path}
    finally:
        live.close()
