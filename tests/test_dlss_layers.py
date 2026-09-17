from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_layers as layers
import pipeline


class FakeFirst:
    def __init__(self, width, height, settings):
        self.settings = dict(settings)
        self.closed = False
        self.resets = []

    def update(self, settings):
        self.settings = dict(settings)

    def process(self, rgba, motion, depth, reset=False):
        self.resets.append(reset)
        output = rgba.copy()
        output[..., :3] += 3
        return output

    def close(self):
        self.closed = True


class FakeSecond(FakeFirst):
    def process(self, rgba, motion, depth, reset, settings):
        self.update(settings)
        self.resets.append(reset)
        output = rgba.copy()
        output[..., :3] *= 2
        return output


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.first_patch = patch.object(layers.runtime_session, 'Live', side_effect=FakeFirst)
        self.second_patch = patch.object(layers, 'SecondPass', side_effect=FakeSecond)
        self.first_factory = self.first_patch.start()
        self.second_factory = self.second_patch.start()
        self.addCleanup(self.first_patch.stop)
        self.addCleanup(self.second_patch.stop)
        self.rgba = np.full((8, 8, 4), 20, np.uint8)
        self.rgba[..., 3] = 255
        self.flow = np.zeros((8, 8, 2), np.float32)
        self.depth = np.zeros((8, 8), np.float32)

    def session(self, **settings):
        live = layers.LayeredLive(8, 8, settings)
        self.addCleanup(live.close)
        return live

    def process(self, live):
        return live.process(self.rgba, self.flow, self.depth)

    def test_second_pass_receives_first_result_and_has_independent_parameters(self):
        live = self.session(preset=1, style=0, second_layer={'preset': 3, 'style': 2, 'local_tone': 2.3})
        output = self.process(live)
        self.assertTrue((output[..., :3] == 46).all())  # (20 + 3) * 2, not two independent inputs.
        self.assertEqual(live.first.settings['preset'], 1)
        self.assertEqual(live.second.settings['preset'], 3)
        self.assertEqual(live.first.settings['local_tone'], 1)
        self.assertEqual(live.second.settings['local_tone'], 2.3)
        self.assertEqual(set(live.first.settings), set(layers.DEFAULTS))
        self.assertEqual(set(live.second.settings), set(layers.DEFAULTS))

    def test_overall_weight_blends_entire_chain_and_keeps_alpha(self):
        live = self.session(second_layer={}, overall_weight=.5)
        output = self.process(live)
        self.assertTrue((output[..., :3] == 33).all())
        np.testing.assert_array_equal(output[..., 3], self.rgba[..., 3])

    def test_zero_weight_returns_original_without_loading_either_model(self):
        live = self.session(second_layer={}, overall_weight=0)
        np.testing.assert_array_equal(self.process(live), self.rgba)
        self.first_factory.assert_not_called()
        self.second_factory.assert_not_called()

    def test_disabled_second_pass_uses_one_model_and_preserves_settings_when_reenabled(self):
        live = self.session()
        self.assertTrue((self.process(live)[..., :3] == 23).all())
        self.second_factory.assert_not_called()
        live.update({'second_layer': {'preset': 2}})
        self.process(live)
        second = live.second
        live.update({'second_layer': None})
        self.assertTrue(second.closed)
        self.assertIsNone(live.second)
        self.assertTrue((self.process(live)[..., :3] == 23).all())

    def test_temporal_history_is_continuous_and_resets_for_parameter_changes(self):
        live = self.session(second_layer={})
        self.process(live)
        self.process(live)
        live.update({'second_layer': {'style': 2}})
        self.process(live)
        self.assertEqual(live.first.resets, [True, False, True])
        self.assertEqual(live.second.resets, [True, False, True])

    def test_second_pass_failure_closes_both_sessions(self):
        live = self.session(second_layer={})
        self.process(live)
        first, second = live.first, live.second
        with patch.object(second, 'process', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.process(live)
        self.assertTrue(first.closed and second.closed)
        with self.assertRaises(RuntimeError):
            self.process(live)

    def test_both_layers_contribute_guidance_and_images_disable_both_without_mutation(self):
        settings = {'guidance_mode': 1, 'second_layer': {'guidance_mode': 2}}
        self.assertEqual(layers.guidance_needs(settings), (True, True))
        converted = layers.image_settings(settings)
        self.assertEqual(layers.guidance_needs(converted), (False, False))
        self.assertEqual(settings['second_layer']['guidance_mode'], 2)
        self.assertEqual(layers.guidance_needs({**settings, 'overall_weight': 0}), (False, False))

    def test_invalid_weight_and_second_layer_parameters_rejected(self):
        for weight in (-.1, 1.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                layers.normalize_settings({'overall_weight': weight})
        with self.assertRaises(ValueError):
            layers.normalize_settings({'second_layer': {'preset': 4}})

    def test_video_cache_is_not_reused_for_different_second_pass_or_weight(self):
        directory = Path(tempfile.mkdtemp(prefix='regression-layers-', dir=ROOT / 'tests'))
        video = directory / '原视频.mp4'
        video.write_bytes(b'video')
        folder = Path(pipeline.out_dirs(str(video))[2])
        folder.mkdir()
        settings = {'second_layer': {'style': 2}, 'overall_weight': .5}
        record = pipeline._cache_record(str(video), {'kind': 'dlss', 'settings': settings, 'frames': 3})
        pipeline._finish_cache(str(folder), record)
        self.assertTrue(pipeline.dlss_cache_matches(str(video), settings, 2))
        self.assertFalse(pipeline.dlss_cache_matches(str(video), settings, 3))
        self.assertFalse(pipeline.dlss_cache_matches(str(video), {**settings, 'overall_weight': 1}))
        self.assertFalse(pipeline.dlss_cache_matches(str(video), {**settings, 'second_layer': None}))


if __name__ == '__main__':
    unittest.main()
