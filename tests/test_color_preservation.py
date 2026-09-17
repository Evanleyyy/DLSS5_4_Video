from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import color_preservation as color
from image_editor import SelectionMask, blend_selection, compose_result


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class ColorTests(unittest.TestCase):
    def pair(self):
        original = np.full((160, 192, 3), (85, 115, 145), np.uint8)
        texture = (np.indices(original.shape[:2]).sum(axis=0) % 2 * 2 - 1) * 14
        rendered = (original.astype(np.int16) + (30, -20, 15) + texture[..., None]).astype(np.uint8)
        return original, rendered

    def test_full_strength_restores_color_without_replacing_model_texture(self):
        original, rendered = self.pair()
        result = color.apply(original, rendered, 1)
        area = np.s_[12:-12, 12:-12]
        np.testing.assert_allclose(result[area].mean(axis=(0, 1)), original[0, 0], atol=.1)
        # Original is flat; the generated checker detail must retain its amplitude.
        np.testing.assert_allclose(result[area].std(axis=(0, 1)), 14, atol=.1)
        self.assertFalse(np.array_equal(result, original))

    def test_strength_is_monotonic_and_zero_is_bit_exact(self):
        original, rendered = self.pair()
        before = rendered.copy()
        np.testing.assert_array_equal(color.apply(original, rendered, 0), rendered)
        errors = []
        for strength in (0, .25, .5, .75, 1):
            result = color.apply(original, rendered, strength)
            errors.append(float(np.abs(result[12:-12, 12:-12].mean(axis=(0, 1)) - original[0, 0]).mean()))
        self.assertTrue(all(a > b for a, b in zip(errors, errors[1:])), errors)
        np.testing.assert_array_equal(rendered, before)

    def test_source_high_frequency_noise_is_not_copied(self):
        original, rendered = self.pair()
        clean = color.apply(original, rendered, 1)
        noise = (np.indices(original.shape[:2]).sum(axis=0) % 2 * 2 - 1) * 18
        noisy = (original.astype(np.int16) + noise[..., None]).astype(np.uint8)
        result = color.apply(noisy, rendered, 1)
        self.assertLess(np.abs(result[12:-12, 12:-12].astype(float) - clean[12:-12, 12:-12]).mean(), .1)

    def test_row_blocks_have_no_seams_and_repeated_frames_are_stable(self):
        rng = np.random.default_rng(12)
        original = rng.integers(50, 180, (389, 257, 3), dtype=np.uint8)
        rendered = rng.integers(50, 180, original.shape, dtype=np.uint8)
        result = color.apply(original, rendered, .7)
        # Horizontal mirror exercises a different memory layout; vertical mirror
        # changes block boundaries, exposing filtering seams at rows 128/256/384.
        mirrored = color.apply(original[::-1, ::-1], rendered[::-1, ::-1], .7)[::-1, ::-1]
        self.assertLessEqual(np.abs(result.astype(float) - mirrored).max(), 1)
        np.testing.assert_array_equal(color.apply(original, rendered, .7), result)

    def test_transparent_rgb_never_bleeds_into_visible_colors(self):
        original = np.full((40, 48, 4), (80, 100, 120, 255), np.uint8)
        rendered = np.full_like(original, (100, 130, 160, 255))
        original[:, :24, 3] = 0
        changed = original.copy()
        changed[:, :24, :3] = (255, 0, 255)
        a, b = color.apply(original, rendered, 1), color.apply(changed, rendered, 1)
        np.testing.assert_array_equal(a[:, 24:], b[:, 24:])
        np.testing.assert_array_equal(a[..., 3], original[..., 3])

    def test_tiny_images_gamut_bounds_and_input_validation(self):
        for shape in ((1, 1, 3), (1, 7, 4), (9, 1, 3)):
            source, result = np.zeros(shape, np.uint8), np.full(shape, 255, np.uint8)
            self.assertEqual(color.apply(source, result, 1).shape, shape)
        original, rendered = self.pair()
        for value in (-.1, 1.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                color.apply(original, rendered, value)
        for settings in ({'mask_scope': 'unknown'}, [], {'strength': float('nan')}):
            with self.assertRaises(ValueError):
                color.normalize(settings)
        with self.assertRaises(ValueError):
            color.apply(original, rendered[:10], 1)

    def editor(self):
        editor = type('Editor', (), {})()
        editor.image_bgr, editor.image_dlss = self.pair()
        editor._image_color_deferred = True
        editor._image_color_cache = None
        editor.v_color_preservation = Value(100)
        editor.v_color_mask_scope = Value(color.MASK_SCOPES['color'])
        editor.v_mask_enabled = Value(1)
        editor.v_mask_mode = Value('只处理涂抹区域')
        editor.v_feather = Value(0)
        editor.selection = SelectionMask(192, 160)
        editor.selection.replace(np.tile(np.r_[np.zeros(96), np.full(96, 255)], (160, 1)))
        def output():
            settings = {'color_preservation': {
                'strength': editor.v_color_preservation.get() / 100,
                'mask_scope': next(key for key, label in color.MASK_SCOPES.items()
                                   if label == editor.v_color_mask_scope.get())}}
            return compose_result(editor.image_bgr, editor.image_dlss, settings,
                                  editor.selection.alpha(editor.v_feather.get()) if editor.v_mask_enabled.get() else None,
                                  editor.v_mask_mode.get() == '保护涂抹区域', getattr(editor, 'image_alpha', None))
        editor._image_output = output
        return editor

    def test_color_only_mask_keeps_model_detail_outside_and_inverts(self):
        editor = self.editor()
        rendered = editor.image_dlss.copy()
        corrected = color.apply(editor.image_bgr, rendered, 1)
        output = editor._image_output()
        np.testing.assert_array_equal(output[:, :96], rendered[:, :96])
        np.testing.assert_array_equal(output[:, 96:], corrected[:, 96:])
        editor.v_mask_mode.value = '保护涂抹区域'
        protected = editor._image_output()
        np.testing.assert_array_equal(protected[:, 96:], rendered[:, 96:])
        np.testing.assert_array_equal(protected[:, :96], corrected[:, :96])
        np.testing.assert_array_equal(editor.image_dlss, rendered)

    def test_whole_result_mask_keeps_original_outside_and_feathers_color_mode(self):
        editor = self.editor()
        editor.v_color_mask_scope.value = color.MASK_SCOPES['result']
        output = editor._image_output()
        np.testing.assert_array_equal(output[:, :96], editor.image_bgr[:, :96])
        editor.v_color_mask_scope.value = color.MASK_SCOPES['color']
        editor.v_feather.value = 12
        result = editor._image_output()
        alpha = editor.selection.alpha(12)
        expected = blend_selection(editor.image_dlss, color.apply(editor.image_bgr, editor.image_dlss, 1), alpha)
        np.testing.assert_array_equal(result, expected)
        self.assertTrue(np.any((alpha > 0) & (alpha < 255)))

    def test_color_mask_zero_strength_full_selection_alpha_and_cache_refresh(self):
        editor = self.editor()
        editor.image_alpha = np.full((160, 192), 173, np.uint8)
        editor.v_color_preservation.value = 0
        np.testing.assert_array_equal(editor._image_output()[..., :3], editor.image_dlss)
        editor.v_color_preservation.value = 100
        editor.selection.replace(255)
        output = editor._image_output()
        np.testing.assert_array_equal(output[..., 3], editor.image_alpha)
        corrected = output.copy()
        editor.image_dlss = editor.image_dlss.copy()
        editor.image_dlss[40:44, 40:44] += 20
        self.assertFalse(np.array_equal(editor._image_output(), corrected))


if __name__ == '__main__':
    unittest.main()
