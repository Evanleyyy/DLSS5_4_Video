import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import pipeline
import dlss_engine


@contextmanager
def artifact_directory():
    # Retain test artifacts for review; do not recursively delete directories.
    yield tempfile.mkdtemp(prefix='regression-', dir=ROOT / 'tests')


class FakeNative:
    def __init__(self):
        self.process_calls = 0
        self.shutdown_calls = 0

    def dlssnr_set_options(self, *args): pass
    def dlssnr_init(self, *args): return 1
    def dlssnr_create_feature(self, *args): return 1
    def dlssnr_process(self, *args):
        self.process_calls += 1
        return 1
    def dlssnr_shutdown(self): self.shutdown_calls += 1


class NativeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.loader = patch.object(dlss_engine, '_load', return_value=self.native)
        self.loader.start()
        self.session = dlss_engine.Live(8, 8)
        self.rgba = np.zeros((8, 8, 4), np.uint8)
        self.flow = np.zeros((8, 8, 2), np.float32)
        self.depth = np.zeros((8, 8), np.float32)

    def tearDown(self):
        self.session.close()
        self.loader.stop()

    def test_bad_shape_and_dtype_rejected_before_native_call(self):
        for flow in (self.flow[:4], self.flow.astype(np.float64)):
            with self.assertRaises(ValueError):
                self.session.process(self.rgba, flow, self.depth)
        self.assertEqual(self.native.process_calls, 0)

    def test_concurrent_session_does_not_shutdown_current_session(self):
        with self.assertRaises(RuntimeError):
            dlss_engine.Live(16, 16)
        self.session.process(self.rgba, self.flow, self.depth)
        self.assertEqual(self.native.shutdown_calls, 0)

    def test_close_idempotent_and_use_after_close_rejected(self):
        self.session.close()
        self.session.close()
        self.assertEqual(self.native.shutdown_calls, 1)
        with self.assertRaises(RuntimeError):
            self.session.process(self.rgba, self.flow, self.depth)

    def test_native_failure_raises_instead_of_black_frame(self):
        with patch.object(self.native, 'dlssnr_process', return_value=0):
            with self.assertRaises(RuntimeError):
                self.session.process(self.rgba, self.flow, self.depth)


class ImageAndExportTests(unittest.TestCase):
    def test_unicode_io_and_low_range_depth_normalization(self):
        with artifact_directory() as temp:
            path = str(Path(temp) / '中文深度.png')
            pixels = np.ones((8, 8), np.uint16)
            pipeline.imwrite(path, pixels)
            self.assertAlmostEqual(float(pipeline.read_depth(path)[0, 0]), 1 / 65535)
            pipeline.imwrite(path, pixels.astype(np.uint8))
            self.assertAlmostEqual(float(pipeline.read_depth(path)[0, 0]), 1 / 255)

    def test_missing_frame_rejected_before_encoder_start(self):
        with artifact_directory() as temp:
            video = str(Path(temp) / 'clip.mp4')
            folder = Path(pipeline.out_dirs(video)[2])
            folder.mkdir()
            pipeline.imwrite(str(folder / '000000.png'), np.zeros((8, 8, 3), np.uint8))
            with patch.object(pipeline, 'video_info', return_value=(2, 24, 8, 8)), \
                 patch.object(pipeline.subprocess, 'Popen') as process:
                with self.assertRaises(ValueError):
                    pipeline.export_video(video, 'dlss')
                process.assert_not_called()

    def test_encoder_failure_preserves_previous_output(self):
        with artifact_directory() as temp:
            video = str(Path(temp) / 'clip.mp4')
            output = Path(temp) / 'clip_dlss.mp4'
            output.write_bytes(b'previous valid output')
            folder = Path(pipeline.out_dirs(video)[2])
            folder.mkdir()
            pipeline.imwrite(str(folder / '000000.png'), np.zeros((8, 8, 3), np.uint8))
            with patch.object(pipeline, 'video_info', return_value=(1, 24, 8, 8)), \
                 patch.object(pipeline, '_bundle_ffmpeg', return_value=sys.executable):
                with self.assertRaises(RuntimeError):
                    pipeline.export_video(video, 'dlss', with_audio=False)
            self.assertEqual(output.read_bytes(), b'previous valid output')


if __name__ == '__main__':
    unittest.main(verbosity=2)
