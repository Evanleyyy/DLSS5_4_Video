from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import media_export
from processing_ui import ProcessingMixin


class ConfirmationTests(unittest.TestCase):
    def request(self, image=False):
        return {'source': 'source.png' if image else 'source.mp4', 'is_image': image,
                'format': '图片', 'scope': '当前帧', 'channels': ['dlss'],
                'directory': str(ROOT / 'logs'), 'fps': 24, 'duration': 1,
                'audio': False, 'crf': 18, 'frame': 0, 'frames': 1, 'allow_generate': False}

    def test_export_with_missing_cache_never_starts_any_inference(self):
        with patch('media_export.pipeline.dlss_cache_matches', return_value=False), \
             patch('media_export.pipeline.generate_dlss') as dlss, \
             patch('media_export.pipeline.generate_depth') as depth, \
             patch('media_export.pipeline.generate_flow') as flow:
            with self.assertRaisesRegex(ValueError, '重新生成'):
                media_export._export_channels(self.request(), {'guidance_mode': 3})
            for operation in (dlss, depth, flow):
                operation.assert_not_called()

    def test_damaged_confirmed_cache_is_reported_without_regeneration(self):
        with patch('media_export.pipeline.dlss_cache_matches', return_value=True), \
             patch('media_export.pipeline.validate_dlss_frames', side_effect=ValueError('坏帧')), \
             patch('media_export.pipeline.generate_dlss') as dlss:
            with self.assertRaisesRegex(ValueError, '重新生成'):
                media_export._export_channels(self.request(), {})
            dlss.assert_not_called()

    def test_missing_image_depth_never_loads_a_model_on_export(self):
        request = self.request(image=True)
        request.update(channels=['depth'], images={'original': np.zeros((8, 8, 3), np.uint8)})
        with patch('media_export.pipeline.infer_depth_frame') as infer:
            with self.assertRaisesRegex(ValueError, '生成深度通道'):
                media_export._export_channels(request, {})
            infer.assert_not_called()

    def test_export_requires_successful_matching_settings_source_and_mask(self):
        dummy = type('State', (ProcessingMixin,), {})()
        dummy.current_is_image = True
        dummy._source_key = lambda: ('image', 'test.png', 1)
        dummy._mask_key = lambda: (True, '只处理涂抹区域', 4, 3)
        dummy._collect_settings = lambda: {'intensity': .7}
        dummy._confirmed_source = dummy._source_key()
        dummy._confirmed_settings = {'intensity': .7}
        dummy._confirmed_mask_key = dummy._mask_key()
        dummy._confirmed_image = np.zeros((8, 8, 3), np.uint8)
        dummy._require_confirmed_result()
        for attribute, changed in [('_confirmed_settings', {'intensity': .3}),
                                   ('_confirmed_source', ('image', 'other.png', 2)),
                                   ('_confirmed_mask_key', (True, '只处理涂抹区域', 4, 2))]:
            previous = getattr(dummy, attribute)
            setattr(dummy, attribute, changed)
            with self.assertRaisesRegex(ValueError, '仍在渲染'):
                dummy._require_confirmed_result()
            setattr(dummy, attribute, previous)
        dummy._confirmed_image = None
        with self.assertRaisesRegex(ValueError, '尚无处理结果'):
            dummy._require_confirmed_result()


if __name__ == '__main__':
    unittest.main()
