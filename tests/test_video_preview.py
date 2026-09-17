"""A preview must not generate/export a whole video or mix its cache manifest."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import pipeline
from video_preview import VideoPreview


class VideoPreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='preview-', dir=ROOT / 'logs'))
        self.video = str(self.folder / 'input.avi')
        writer = cv2.VideoWriter(self.video, cv2.VideoWriter_fourcc(*'MJPG'), 6, (34, 26))
        for value in (20, 90, 160):
            writer.write(np.full((26, 34, 3), value, np.uint8))
        writer.release()
        self.live = Mock()
        self.live.process.side_effect = lambda rgba, *args, **kwargs: rgba.copy()
        self.acquire = patch('video_preview.model_sessions.acquire_dlss', return_value=self.live)
        self.acquire.start()
        self.addCleanup(self.acquire.stop)

    def test_seeking_one_frame_never_generates_a_video_cache(self):
        with patch('pipeline.generate_dlss', side_effect=AssertionError('整段重算')), \
             patch('pipeline.generate_depth', side_effect=AssertionError('整段深度')), \
             patch('pipeline.generate_flow', side_effect=AssertionError('整段光流')):
            output = VideoPreview().render(self.video, 2, {})
        self.assertEqual(output.shape, (26, 34, 3))
        self.assertGreater(float(output.mean()), 150)
        self.assertFalse(Path(pipeline.out_dirs(self.video)[2]).exists())
        self.assertEqual(self.live.process.call_args.args[0].shape, (32, 40, 4))
        self.assertTrue(self.live.process.call_args.kwargs['reset'])

    def test_same_frame_parameter_edits_reuse_guidance_and_need_no_unused_models(self):
        preview = VideoPreview()
        with patch('pipeline.infer_depth_frame', return_value=np.zeros((26, 34), np.float32)) as depth, \
             patch('pipeline.infer_flow_pair', return_value=np.zeros((26, 34, 2), np.float32)) as flow:
            preview.render(self.video, 1, {'guidance_mode': 3})
            preview.render(self.video, 1, {'guidance_mode': 3, 'intensity': .4})
            depth.assert_called_once()
            flow.assert_called_once()
            preview.render(self.video, 2, {'guidance_mode': 0})
            depth.assert_called_once()
            flow.assert_called_once()

    def test_valid_full_guidance_cache_can_be_read_without_reloading_models(self):
        folder = Path(pipeline.out_dirs(self.video)[0])
        folder.mkdir()
        pipeline.imwrite(str(folder / '000001.png'), np.full((26, 34), 20000, np.uint16))
        pipeline._finish_cache(str(folder), pipeline._cache_record(self.video,
                              {'kind': 'depth', 'edge': 720, 'frames': 3}))
        with patch('pipeline.infer_depth_frame', side_effect=AssertionError('重复计算引导')):
            VideoPreview().render(self.video, 1, {'guidance_mode': 2})

    def test_seed_preview_processes_only_current_image(self):
        with patch('sr_backend.process_image', return_value=np.zeros((52, 68, 3), np.uint8)) as image, \
             patch('sr_backend.process_video', side_effect=AssertionError('整段超分')):
            output = VideoPreview().render(self.video, 1, {'super_resolution': {'engine': 'seedvr2'}})
        self.assertEqual(output.shape, (52, 68, 3))
        image.assert_called_once()
        self.assertEqual(image.call_args.args[0].shape, (26, 34, 3))

    def test_completed_video_cache_skips_preview_inference(self):
        frame = np.full((26, 34, 3), 200, np.uint8)
        with patch('pipeline.dlss_cache_matches', return_value=True), \
             patch('pipeline.imread', return_value=frame):
            output = VideoPreview().render(self.video, 1, {})
        np.testing.assert_array_equal(output, frame)
        self.live.process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
