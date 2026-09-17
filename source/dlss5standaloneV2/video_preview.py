"""Render one video frame without publishing an incomplete full-video cache."""
import json
from pathlib import Path

import cv2
import numpy as np
import dlss_layers
import model_sessions
import pipeline
import sr_backend
import task_control
from cache_manager import video_cache_guard


class VideoPreview:
    def __init__(self):
        self.key = None
        self.frame = self.previous = self.depth = self.flow = None

    def _cached_guidance(self, video, directory, index, kind):
        try:
            state = json.loads((Path(directory) / 'cache.json').read_text(encoding='utf-8'))
            record = state['record']
            if (not state['complete'] or record['options']['kind'] != kind
                    or record['options']['frames'] <= index
                    or record != pipeline._cache_record(video, record['options'])):
                return None
            if kind == 'depth':
                path = pipeline.find_depth(directory, index)
                return pipeline.read_depth(path) if path else None
            return pipeline.read_flo(str(Path(directory) / f'{index:06d}.flo'))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def render(self, video, index, settings, progress=None):
        with video_cache_guard(video):
            return self._render(video, index, settings, progress or (lambda *args: None))

    def _render(self, video, index, settings, progress):
        task_control.checkpoint()
        settings = dlss_layers.normalize_settings(settings)
        if pipeline.dlss_cache_matches(video, settings, index):
            path = Path(pipeline.out_dirs(video)[2]) / f'{index:06d}.png'
            cached = pipeline.imread(str(path))
            if cached is not None:
                return cached
        stat = Path(video).stat()
        key = (str(Path(video).resolve()), stat.st_size, stat.st_mtime_ns, index)
        if key != self.key:
            capture = cv2.VideoCapture(video)
            try:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, index - 1))
                ok, previous = capture.read()
                if not ok:
                    raise ValueError('无法读取视频预览帧')
                ok, frame = capture.read() if index else (True, previous)
                if not ok:
                    raise ValueError('无法读取当前视频帧')
            finally:
                capture.release()
            self.key, self.frame, self.previous = key, frame, previous
            self.depth = self.flow = None
        if settings['super_resolution']['engine'] != 'dlss':
            if settings['super_resolution']['engine'] != 'seedvr2':
                raise ValueError('视频请选择 DLSS 或 SeedVR2；PiSA 和 VOSR 仅支持图片。')
            # A still preview intentionally has no cross-frame diffusion context.
            return sr_backend.process_image(self.frame, settings, progress)
        height, width = self.frame.shape[:2]
        need_depth, need_flow = dlss_layers.guidance_needs(settings)
        depth_dir, flow_dir, _ = pipeline.out_dirs(video)
        if need_depth and self.depth is None:
            progress(0, 1, '生成当前帧深度预览')
            self.depth = self._cached_guidance(video, depth_dir, index, 'depth')
            if self.depth is None or self.depth.shape != (height, width):
                self.depth = pipeline.infer_depth_frame(self.frame)
        if need_flow and self.flow is None:
            progress(0, 1, '生成当前帧光流预览')
            self.flow = self._cached_guidance(video, flow_dir, index, 'flow')
            if self.flow is None or self.flow.shape != (height, width, 2):
                self.flow = (pipeline.infer_flow_pair(self.previous, self.frame) if index
                             else np.zeros((height, width, 2), np.float32))
        depth = self.depth if need_depth else np.zeros((height, width), np.float32)
        flow = self.flow if need_flow else np.zeros((height, width, 2), np.float32)
        rgba = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGBA)
        padded_width, padded_height = (width + 7) // 8 * 8, (height + 7) // 8 * 8
        if (padded_width, padded_height) != (width, height):
            def pad(array):
                return cv2.copyMakeBorder(array, 0, padded_height - height, 0,
                                         padded_width - width, cv2.BORDER_REPLICATE)
            rgba, depth, flow = pad(rgba), pad(depth), pad(flow)
        progress(0, 1, '渲染当前帧预览')
        live = model_sessions.acquire_dlss(padded_width, padded_height, settings)
        try:
            output = live.process(rgba, flow, depth, reset=True)
        finally:
            live.close()
        progress(1, 1, '当前帧预览完成')
        return cv2.cvtColor(output[:height, :width], cv2.COLOR_RGBA2BGR)
