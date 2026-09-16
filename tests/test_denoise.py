from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_layers as layers
import image_denoise
import pipeline
from test_dlss_layers import FakeFirst, FakeSecond


class DenoiseTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(71)
        self.rgba = np.clip(rng.normal(45, 8, (32, 40, 4)), 0, 255).astype(np.uint8)
        self.rgba[..., 3] = np.arange(40, dtype=np.uint8)
        self.active = {'enabled': True, 'luma': 12, 'chroma': 9, 'weight': 1}

    def test_defaults_are_disabled_and_independent_and_legacy_keys_match(self):
        settings = layers.normalize_settings()
        self.assertFalse(settings['input_denoise']['enabled'])
        self.assertEqual(layers.settings_key({}), layers.settings_key(settings))
        settings['input_denoise']['luma'] = 20
        self.assertEqual(settings['output_denoise']['luma'], 3)
        self.assertEqual(layers.normalize_settings()['input_denoise']['luma'], 3)
        copied = layers.image_settings(settings)
        copied['input_denoise']['luma'] = 7
        self.assertEqual(settings['input_denoise']['luma'], 20)

    def test_disabled_zero_weight_and_zero_strength_skip_filter_exactly(self):
        with patch.object(image_denoise.cv2, 'fastNlMeansDenoisingColored') as denoise:
            for settings in ({**self.active, 'enabled': False}, {**self.active, 'weight': 0},
                             {**self.active, 'luma': 0, 'chroma': 0}):
                np.testing.assert_array_equal(image_denoise.apply_rgba(self.rgba, settings), self.rgba)
            denoise.assert_not_called()

    def test_actual_filter_weights_channel_order_alpha_and_no_input_mutation(self):
        original = self.rgba.copy()
        bgr = cv2.cvtColor(original, cv2.COLOR_RGBA2BGR)
        denoised = cv2.fastNlMeansDenoisingColored(bgr, None, 12, 9, 7, 21)[..., ::-1]
        for weight in (0, .5, 1):
            output = image_denoise.apply_rgba(original, {**self.active, 'weight': weight})
            expected = np.rint(original[..., :3].astype(np.float32) * (1 - weight) +
                               denoised.astype(np.float32) * weight).astype(np.uint8)
            np.testing.assert_array_equal(output[..., :3], expected)
            np.testing.assert_array_equal(output[..., 3], original[..., 3])
        np.testing.assert_array_equal(original, self.rgba)

    def test_noise_is_reduced_on_fixed_sample_without_flattening_contrast(self):
        clean = np.full((96, 128, 3), 90, np.uint8)
        clean[:, 64:] = [190, 160, 140]
        noisy = np.clip(clean.astype(float) + np.random.default_rng(5).normal(0, 12, clean.shape),
                        0, 255).astype(np.uint8)
        rgba = cv2.cvtColor(noisy, cv2.COLOR_RGB2RGBA)
        output = image_denoise.apply_rgba(rgba, self.active)[..., :3]
        noisy_mse = np.mean((noisy.astype(float) - clean) ** 2)
        clean_mse = np.mean((output.astype(float) - clean) ** 2)
        self.assertLess(clean_mse, noisy_mse * .4)
        self.assertGreater(output[:, 80:, 0].mean() - output[:, :48, 0].mean(), 85)

    def test_all_stage_combinations_single_and_double_layer_in_correct_order(self):
        flow, depth = np.zeros((32, 40, 2), np.float32), np.zeros((32, 40), np.float32)
        for second in (None, {}):
            for pre, post in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(second=second, pre=pre, post=post), \
                     patch.object(layers.dlss_engine, 'Live', side_effect=FakeFirst), \
                     patch.object(layers, 'SecondPass', side_effect=FakeSecond):
                    settings = {'second_layer': second, 'overall_weight': .5,
                                'input_denoise': {**self.active, 'enabled': pre, 'weight': .5},
                                'output_denoise': {**self.active, 'enabled': post}}
                    expected = image_denoise.apply_rgba(self.rgba, settings['input_denoise']).copy()
                    expected[..., :3] += 3
                    if second is not None:
                        expected[..., :3] *= 2
                    expected = image_denoise.apply_rgba(expected, settings['output_denoise'])
                    expected = layers.blend_result(self.rgba, expected, .5)
                    live = layers.LayeredLive(40, 32, settings)
                    try:
                        np.testing.assert_array_equal(live.process(self.rgba, flow, depth), expected)
                        self.assertEqual(set(live.first.settings), set(layers.DEFAULTS))
                    finally:
                        live.close()

    def test_overall_zero_skips_denoising_and_native_models(self):
        with patch.object(image_denoise, 'apply_rgba') as denoise, \
             patch.object(layers.dlss_engine, 'Live') as first, patch.object(layers, 'SecondPass') as second:
            live = layers.LayeredLive(40, 32, {'overall_weight': 0, 'second_layer': {},
                'input_denoise': self.active, 'output_denoise': self.active})
            np.testing.assert_array_equal(live.process(self.rgba, None, None), self.rgba)
            denoise.assert_not_called()
            first.assert_not_called()
            second.assert_not_called()
            live.close()

    def test_parameter_changes_reset_history_and_failure_releases_sessions(self):
        with patch.object(layers.dlss_engine, 'Live', side_effect=FakeFirst), \
             patch.object(layers, 'SecondPass', side_effect=FakeSecond):
            live = layers.LayeredLive(40, 32, {'second_layer': {}})
            self.addCleanup(live.close)
            live.process(self.rgba, None, None)
            live.process(self.rgba, None, None)
            for key in ('input_denoise', 'output_denoise'):
                settings = layers.normalize_settings(live.settings)
                settings[key] = self.active
                live.update(settings)
                live.process(self.rgba, None, None)
            first, second = live.first, live.second
            self.assertEqual(first.resets, [True, False, True, True])
            self.assertEqual(second.resets, first.resets)
            with patch.object(image_denoise.cv2, 'fastNlMeansDenoisingColored', side_effect=RuntimeError('denoise failed')):
                with self.assertRaisesRegex(RuntimeError, 'denoise failed'):
                    live.process(self.rgba, None, None)
            self.assertTrue(first.closed and second.closed)

    def test_invalid_settings_are_rejected(self):
        for key in ('input_denoise', 'output_denoise'):
            for field, values in [('luma', [-1, 31, float('nan')]), ('chroma', [-1, 31, float('inf')]),
                                  ('weight', [-.01, 1.01, float('nan')]), ('enabled', [2, 'yes'])]:
                for value in values:
                    with self.subTest(key=key, field=field, value=value), self.assertRaises(ValueError):
                        layers.normalize_settings({key: {field: value}})

    def test_cached_results_are_invalidated_by_each_stage_parameter(self):
        directory = Path(tempfile.mkdtemp(prefix='regression-denoise-', dir=ROOT / 'tests'))
        video = directory / '原视频.mp4'
        video.write_bytes(b'video')
        folder = Path(pipeline.out_dirs(str(video))[2])
        folder.mkdir()
        settings = layers.normalize_settings()
        pipeline._finish_cache(str(folder), pipeline._cache_record(str(video),
            {'kind': 'dlss', 'settings': settings, 'frames': 2}))
        self.assertTrue(pipeline.dlss_cache_matches(str(video), {}))
        for key in ('input_denoise', 'output_denoise'):
            for field, value in [('enabled', True), ('luma', 8), ('chroma', 6), ('weight', .5)]:
                changed = layers.normalize_settings(settings)
                changed[key][field] = value
                self.assertFalse(pipeline.dlss_cache_matches(str(video), changed))


if __name__ == '__main__':
    unittest.main()
