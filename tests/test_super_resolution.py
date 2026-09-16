import json
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
import sr_settings
import dlss_layers
import pipeline
import cache_manager
from image_editor import SelectionMask
from image_editor_ui import ImageEditorMixin


class Value:
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value


class SuperResolutionTests(unittest.TestCase):
    def test_existing_settings_remain_dlss_and_sr_has_no_guidance(self):
        old = dlss_layers.normalize_settings({'guidance_mode': 3})
        self.assertEqual(old['super_resolution']['engine'], 'dlss')
        self.assertEqual(dlss_layers.guidance_needs(old), (True, True))
        old['super_resolution']['engine'] = 'seedvr2'
        self.assertEqual(dlss_layers.guidance_needs(old), (False, False))

    def test_reject_invalid_model_and_nonfinite_parameters(self):
        for config in ({'engine': 'unknown'}, {'scale': 3}, {'tile': 257}, {'batch': 6},
                       {'seed': -1}, {'pisa_pixel': float('nan')}, {'blocks': 33}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                sr_settings.normalize(config)

    def test_model_or_independent_parameter_change_invalidates_key(self):
        a = dlss_layers.normalize_settings({'super_resolution': {'engine': 'pisa'}})
        baseline = dlss_layers.settings_key(a)
        for key, value in [('pisa_pixel', .5), ('pisa_semantic', 0), ('seed', 123), ('scale', 4), ('engine', 'vosr')]:
            changed = json.loads(json.dumps(a))
            changed['super_resolution'][key] = value
            self.assertNotEqual(baseline, dlss_layers.settings_key(changed))

    def test_scaled_selection_preserves_unpainted_pixels_and_alpha(self):
        dummy = type('Editor', (ImageEditorMixin,), {})()
        dummy.image_bgr = np.full((7, 9, 3), 20, np.uint8)
        dummy.image_dlss = np.full((28, 36, 3), 220, np.uint8)
        dummy.image_alpha = np.tile(np.arange(9, dtype=np.uint8) * 28, (7, 1))
        dummy.selection = SelectionMask(9, 7)
        dummy.selection.data[:, 4:] = 255
        dummy.v_mask_enabled, dummy.v_feather = Value(True), Value(0)
        dummy.v_mask_mode = Value('只处理涂抹区域')
        result = dummy._image_output()
        self.assertEqual(result.shape, (28, 36, 4))
        self.assertTrue(np.all(result[:, 0, :3] == 20))
        self.assertTrue(np.all(result[:, -1, :3] == 220))
        self.assertTrue(np.array_equal(result[..., 3], cv2.resize(dummy.image_alpha, (36, 28))))
        dummy.v_mask_mode = Value('保护涂抹区域')
        reverse = dummy._image_output()
        self.assertTrue(np.all(reverse[:, 0, :3] == 220))
        self.assertTrue(np.all(reverse[:, -1, :3] == 20))

    def test_owned_sr_cleanup_cannot_target_model_directory(self):
        directory = Path(tempfile.mkdtemp(prefix='regression-sr-', dir=ROOT / 'tests'))
        with patch.dict(os.environ, DLSS5_DATA_ROOT=str(directory)):
            model = directory / 'models'
            model.mkdir()
            (model / 'keep.txt').write_text('model')
            with self.assertRaises(ValueError):
                cache_manager.clean_sr_cache({'path': str(model)})
            job = directory / 'sr-cache' / ('job-' + 'a' * 32)
            job.mkdir(parents=True)
            (job / 'owner.json').write_text(json.dumps({'application': 'DLSS5Standalone', 'pid': os.getpid()}))
            (job / 'input.png').write_bytes(b'input')
            (job / 'active').touch()
            with self.assertRaises(RuntimeError):
                cache_manager.clean_sr_cache({'path': str(job)})
            (job / 'active').unlink()
            self.assertGreater(cache_manager.clean_sr_cache({'path': str(job)}), 0)
            self.assertTrue((model / 'keep.txt').exists())
            self.assertFalse(job.exists())


if __name__ == '__main__':
    unittest.main()
