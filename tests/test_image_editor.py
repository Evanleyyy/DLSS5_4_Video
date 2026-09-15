import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source/dlss5standaloneV2'))
from image_editor import Viewport, SelectionMask, blend_selection


class ImageEditorTests(unittest.TestCase):
    def test_zoom_retains_pixel_under_pointer(self):
        view = Viewport()
        view.pan_x, view.pan_y = 47, -31
        size = 1600, 900, 702, 441
        s, x, y = view.transform(*size)
        pixel = ((311 - x) / s, (217 - y) / s)
        view.zoom_at(2.5, 311, 217, *size)
        s, x, y = view.transform(*size)
        np.testing.assert_allclose((x + pixel[0]*s, y + pixel[1]*s), (311, 217))

    def test_brush_erase_history_and_redo_invalidation(self):
        mask = SelectionMask(200, 120)
        mask.begin_stroke()
        mask.paint((40, 50), (140, 50), 20)
        mask.end_stroke()
        drawn = mask.data.copy()
        self.assertTrue(np.all(drawn[50, 40:141] == 255))
        mask.begin_stroke()
        mask.paint((90, 50), (90, 50), 20, erase=True)
        mask.end_stroke()
        self.assertEqual(mask.data[50, 90], 0)
        mask.undo()
        np.testing.assert_array_equal(mask.data, drawn)
        mask.redo()
        self.assertEqual(mask.data[50, 90], 0)
        mask.undo()
        mask.replace(0)
        self.assertFalse(mask.redo_stack)

    def test_feather_reversible_and_borders_preserved(self):
        mask = SelectionMask(101, 101)
        mask.data[30:71, 30:71] = 255
        mask.revision += 1
        hard = mask.data.copy()
        soft = mask.alpha(12)
        self.assertTrue(0 < soft[50, 28] < soft[50, 32] < 255)
        self.assertEqual(soft[50, 50], 255)
        np.testing.assert_array_equal(mask.data, hard)
        np.testing.assert_array_equal(mask.alpha(0), hard)
        mask.replace(255)
        self.assertTrue(np.all(mask.alpha(20) == 255))

    def test_selected_and_protected_pixels_in_export(self):
        original = np.full((80, 100, 3), 20, np.uint8)
        processed = np.full_like(original, 220)
        mask = SelectionMask(100, 80)
        mask.data[20:60, 20:80] = 255
        mask.revision += 1
        alpha = mask.alpha(8)
        selected = blend_selection(original, processed, alpha)
        protected = blend_selection(original, processed, alpha, True)
        np.testing.assert_array_equal(selected[0, 0], original[0, 0])
        np.testing.assert_array_equal(selected[40, 50], processed[40, 50])
        np.testing.assert_array_equal(protected[0, 0], processed[0, 0])
        np.testing.assert_array_equal(protected[40, 50], original[40, 50])
        self.assertTrue(20 < selected[40, 19, 0] < 220)

    def test_off_image_strokes_are_clipped(self):
        mask = SelectionMask(90, 70)
        mask.paint((-10000, -10000), (-1000, -1000), 20)
        self.assertFalse(mask.data.any())
        mask.paint((30, 30), (10000, 30), 10)
        self.assertEqual(mask.data[30, 89], 255)
        self.assertEqual(mask.data[60, 89], 0)


if __name__ == '__main__':
    unittest.main()
