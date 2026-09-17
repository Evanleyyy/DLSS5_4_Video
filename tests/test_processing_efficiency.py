"""Count expensive operations at the confirmed-processing and layer boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_layers
import pipeline
import runtime_session
from processing_ui import ProcessingMixin


class EfficiencyTests(unittest.TestCase):
    def app(self):
        app = type('State', (ProcessingMixin,), {})()
        app._init_processing()
        app.image_bgr = np.zeros((8, 8, 3), np.uint8)
        app.image_dlss = None
        app._image_dlss = Mock(return_value=app.image_bgr.copy())
        app._post = lambda callback, *args: callback(*args)
        app.processing_note = Mock()
        app.set_status = Mock()
        app.set_progress = Mock()
        return app

    def test_unchecked_stages_do_not_infer_or_filter(self):
        frame = np.full((8, 8, 4), 64, np.uint8)
        fake = Mock()
        fake.process.return_value = frame
        with patch('dlss_layers.runtime_session.Live', return_value=fake), \
             patch('dlss_layers.SecondPass') as second, \
             patch('image_denoise.cv2.fastNlMeansDenoisingColored') as denoise, \
             patch('color_preservation.cv2.GaussianBlur') as color:
            live = dlss_layers.LayeredLive(8, 8, {})
            live.process(frame, np.zeros((8, 8, 2), np.float32), np.zeros((8, 8), np.float32))
            live.close()
            fake.process.assert_called_once()
            second.assert_not_called()
            denoise.assert_not_called()
            color.assert_not_called()

    def test_video_without_guidance_or_auxiliary_exports_skips_depth_and_flow(self):
        with patch('processing_ui.pipeline.generate_depth') as depth, \
             patch('processing_ui.pipeline.generate_flow') as flow, \
             patch('processing_ui.pipeline.generate_dlss') as dlss:
            self.app()._process_confirmed(('video', 'sample.mp4'), dlss_layers.normalize_settings({}), None, None, ['dlss'])
            depth.assert_not_called()
            flow.assert_not_called()
            dlss.assert_called_once()

    def test_guidance_and_explicit_export_channels_are_independent_requests(self):
        for options, channels, expected in [
                ({'guidance_mode': 1}, ['dlss'], (False, True)),
                ({'guidance_mode': 2}, ['dlss'], (True, False)),
                ({'second_layer': {'guidance_mode': 3}}, ['dlss'], (True, True)),
                ({}, ['dlss', 'depth'], (True, False)),
                ({}, ['dlss', 'flow'], (False, True)),
                ({'guidance_mode': 3, 'overall_weight': 0}, ['dlss'], (False, False)),
                ({'guidance_mode': 3, 'super_resolution': {'engine': 'seedvr2'}}, ['dlss'], (False, False))]:
            with self.subTest(options=options, channels=channels), \
                 patch('processing_ui.pipeline.generate_depth') as depth, \
                 patch('processing_ui.pipeline.generate_flow') as flow, \
                 patch('processing_ui.pipeline.generate_dlss'):
                self.app()._process_confirmed(('video', 'sample.mp4'), dlss_layers.normalize_settings(options), None, None, channels)
                self.assertEqual((depth.called, flow.called), expected)

    def test_changing_disabled_denoise_does_not_repeat_image_inference(self):
        app = self.app()
        source, mask = ('image', 'sample.png', 1), (False, '', 0, 0)
        with patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]):
            app._process_confirmed(source, dlss_layers.normalize_settings({}), mask, None, ['dlss'])
            settings = dlss_layers.normalize_settings({'input_denoise': {'enabled': False, 'luma': 20}})
            app._process_confirmed(source, settings, mask, None, ['dlss'])
        self.assertEqual(app._image_dlss.call_count, 1, '关闭的降噪参数变化不应重新运行模型')

    def test_completed_video_is_reused_but_damaged_frames_are_regenerated(self):
        directory = Path(tempfile.mkdtemp(prefix='efficiency-', dir=ROOT / 'logs'))
        video = str(directory / 'test.avi')
        writer = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*'MJPG'), 24, (32, 24))
        self.assertTrue(writer.isOpened())
        for _ in range(2):
            writer.write(np.full((24, 32, 3), 70, np.uint8))
        writer.release()
        live = Mock()
        live.process.side_effect = lambda rgba, *args, **kwargs: rgba.copy()
        with patch('dlss_layers.LayeredLive', return_value=live) as factory, \
             patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]):
            self.assertEqual(pipeline.generate_dlss(video, {}), 2)
            self.assertEqual(pipeline.generate_dlss(video, {}), 2)
            self.assertEqual(factory.call_count, 1, '相同视频与参数不应再次创建模型')
            (Path(pipeline.out_dirs(video)[2]) / '000001.png').write_bytes(b'broken')
            self.assertEqual(pipeline.generate_dlss(video, {}), 2)
            self.assertEqual(factory.call_count, 2)
            pipeline.generate_dlss(video, {'intensity': .4})
            self.assertEqual(factory.call_count, 3, '有效参数改变必须重新生成')

    def test_image_depth_is_reused_when_only_color_changes(self):
        app = self.app()
        source, mask = ('image', 'sample.png', 1), (False, '', 0, 0)
        with patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]), \
             patch('processing_ui.pipeline.infer_depth_frame', return_value=np.zeros((8, 8), np.float32)) as infer:
            app._process_confirmed(source, dlss_layers.normalize_settings({}), mask, None, ['dlss', 'depth'])
            app._process_confirmed(source, dlss_layers.normalize_settings({'color_preservation': {'strength': .5}}), mask, None, ['dlss', 'depth'])
            infer.assert_called_once()

    def test_cached_frame_with_valid_header_but_damaged_pixels_is_regenerated(self):
        directory = Path(tempfile.mkdtemp(prefix='efficiency-crc-', dir=ROOT / 'logs'))
        video = directory / 'source.mp4'
        video.write_bytes(b'source')
        frames = Path(pipeline.out_dirs(str(video))[2])
        frames.mkdir()
        path = frames / '000000.png'
        frame = np.full((24, 32, 3), 80, np.uint8)
        pipeline.imwrite(str(path), frame)
        data = bytearray(path.read_bytes())
        data[data.index(b'IDAT') + 4] ^= 1
        path.write_bytes(data)
        live = Mock()
        live.process.side_effect = lambda rgba, *args, **kwargs: rgba.copy()
        with patch('pipeline.video_info', return_value=(1, 24, 32, 24)), \
             patch('pipeline.iter_frames', return_value=iter([(0, frame)])), \
             patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]), \
             patch('dlss_layers.LayeredLive', return_value=live) as factory:
            pipeline._finish_cache(str(frames), pipeline._cache_record(str(video),
                {'kind': 'dlss', 'settings': {}, 'frames': 1}))
            pipeline.generate_dlss(str(video), {})
            factory.assert_called_once()
        np.testing.assert_array_equal(pipeline.imread(str(path)), frame)

    def test_idle_worker_acknowledgment_avoids_full_shutdown_timeout(self):
        live = runtime_session.Live.__new__(runtime_session.Live)
        live._job = None
        process = live._process = Mock(pid=123)
        process.is_alive.return_value = True  # Native shutdown is stuck.
        connection = live._connection = Mock()
        connection.poll.return_value = True
        connection.recv.return_value = ('closing', None)
        live._stop()
        connection.recv.assert_called_once()
        self.assertLess(process.join.call_args_list[0].args[0], 1)
        process.terminate.assert_called_once()
        process.close.assert_called_once()

    def test_worker_acknowledges_only_after_delivering_frame_result(self):
        connection, renderer = Mock(), Mock()
        frame = np.zeros((8, 8, 4), np.uint8)
        renderer.process.return_value = frame
        connection.recv.side_effect = ['start', ('process', ({}, frame, None, None, True)), ('close', None)]
        def check_before_native_close():
            self.assertEqual([call.args[0][0] for call in connection.send.call_args_list],
                             ['ready', 'result', 'closing'])
        renderer.close.side_effect = check_before_native_close
        with patch('runtime_session.dlss_engine.Live', return_value=renderer), \
             patch('runtime_session.dlss_engine.LOG_PATH', ''):
            runtime_session._worker(connection, 8, 8, {}, 'test.log')
        renderer.close.assert_called_once()
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
