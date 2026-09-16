import json
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_layers
import media_export
import pipeline
import sr_backend


class CacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='cache-export-', dir=ROOT / 'logs'))
        self.video = str(self.folder / 'source.mp4')
        self.frame = np.full((48, 64, 3), 40, np.uint8)
        media_export.encode_video([self.frame] * 3, 3, 12, self.video)
        self.cache = Path(pipeline.out_dirs(self.video)[2])
        self.cache.mkdir()
        self.settings = dlss_layers.normalize_settings({'guidance_mode': 0})

    def test_completed_but_mixed_cache_regenerated_before_export(self):
        for i in range(3):
            frame = cv2.resize(self.frame, (128, 96)) if i == 1 else self.frame
            pipeline.imwrite(str(self.cache / f'{i:06d}.png'), frame)
        record = pipeline._cache_record(self.video, {'kind': 'dlss', 'frames': 3, 'settings': self.settings})
        pipeline._finish_cache(str(self.cache), record)
        self.assertTrue(pipeline.dlss_cache_matches(self.video, self.settings, 2))
        def generate(video, settings=None, progress=None):
            for i in range(3):
                pipeline.imwrite(str(self.cache / f'{i:06d}.png'), self.frame)
            return 3
        request = dict(is_image=False, source=self.video, format='视频', scope='全部帧',
                       channels=['dlss'], directory=str(self.folder), fps=12, duration=1,
                       audio=False, crf=18, frame=0, frames=3)
        with patch.object(pipeline, 'generate_dlss', side_effect=generate) as regeneration:
            result = media_export.export_channels(request, self.settings)
            regeneration.assert_called_once()
        self.assertEqual(pipeline.video_info(result['outputs'][0]), (3, 12., 64, 48))

    def test_failed_worker_cannot_overwrite_published_video_frames(self):
        target = self.cache / '000000.png'
        pipeline.imwrite(str(target), self.frame)
        before = target.read_bytes()
        settings = dlss_layers.normalize_settings({'super_resolution': {'engine': 'seedvr2'}})
        def failing_worker(job, progress):
            self.assertNotEqual(Path(job['output']), self.cache)
            folder = Path(job['output'])
            folder.mkdir(parents=True, exist_ok=True)
            pipeline.imwrite(str(folder / '000000.png'), cv2.resize(self.frame, (128, 96)))
            raise RuntimeError('模拟旧任务中断')
        with patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.folder / 'data')), \
             patch.object(sr_backend, 'run_job', side_effect=failing_worker):
            with self.assertRaisesRegex(RuntimeError, '模拟旧任务中断'):
                sr_backend.process_video(self.video, str(self.cache), settings, 3)
        self.assertEqual(target.read_bytes(), before)

    def test_worker_all_frames_validated_before_publishing(self):
        target = self.cache / '000000.png'
        pipeline.imwrite(str(target), self.frame)
        before = target.read_bytes()
        settings = dlss_layers.normalize_settings({'super_resolution': {'engine': 'seedvr2'}})
        def mixed_worker(job, progress):
            folder = Path(job['output'])
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                frame = self.frame if i == 1 else cv2.resize(self.frame, (128, 96))
                pipeline.imwrite(str(folder / f'{i:06d}.png'), frame)
            return {'count': 3, 'outputs': [str(folder)]}
        with patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.folder / 'data')), \
             patch.object(sr_backend, 'run_job', side_effect=mixed_worker):
            with self.assertRaisesRegex(ValueError, '尺寸'):
                sr_backend.process_video(self.video, str(self.cache), settings, 3)
        self.assertEqual(target.read_bytes(), before)

    def test_complete_result_can_publish_across_drives(self):
        settings = dlss_layers.normalize_settings({'super_resolution': {'engine': 'seedvr2'}})
        staged = []
        def worker(job, progress):
            folder = Path(job['output'])
            folder.mkdir(parents=True, exist_ok=True)
            staged.append(folder)
            for i in range(3):
                pipeline.imwrite(str(folder / f'{i:06d}.png'), cv2.resize(self.frame, (128, 96)))
            return {'count': 3, 'outputs': [str(folder)]}
        replace = os.replace
        def across_drives(source, destination):
            if Path(source).parent.name == 'video-frames':
                raise OSError(errno.EXDEV, 'different drives')
            return replace(source, destination)
        with patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.folder / 'data')), \
             patch.object(sr_backend, 'run_job', side_effect=worker), \
             patch.object(sr_backend.os, 'replace', side_effect=across_drives):
            result = sr_backend.process_video(self.video, str(self.cache), settings, 3)
        self.assertEqual(result['outputs'], [str(self.cache)])
        self.assertFalse(staged[0].exists())
        self.assertFalse(list(self.cache.glob('.dlss-publish-*')))
        for i in range(3):
            self.assertEqual(pipeline.imread(str(self.cache / f'{i:06d}.png')).shape, (96, 128, 3))


if __name__ == '__main__':
    unittest.main()
