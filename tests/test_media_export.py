import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import pipeline
import media_export


class MediaExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='export-', dir=ROOT / 'tests'))
        self.original = np.full((17, 25, 3), 40, np.uint8)
        self.processed = np.full_like(self.original, 200)
        self.mask = np.zeros((17, 25), np.uint8)
        self.mask[4:13, 6:19] = 255

    def request(self):
        return {'is_image': True, 'source': str(self.folder / '测试图.png'),
                'format': '图片', 'scope': '当前帧', 'channels': ['original', 'dlss', 'mask'],
                'directory': str(self.folder), 'fps': 12., 'duration': .25,
                'audio': False, 'crf': 18, 'frame': 0, 'frames': 1,
                'images': {'original': self.original, 'dlss': self.processed, 'mask': self.mask}}

    def test_selected_png_channels_are_separate_and_lossless(self):
        request = self.request()
        with patch.object(pipeline, 'infer_depth_frame') as depth:
            result = media_export.export_channels(request, {})
            depth.assert_not_called()
        paths = list(Path(result['directory']).glob('*.png'))
        self.assertEqual(len(paths), 3)
        self.assertFalse(any('depth' in path.name or 'flow' in path.name for path in paths))
        for path in paths:
            channel = path.stem.rsplit('_', 1)[1]
            saved = pipeline.imread(str(path), -1)
            np.testing.assert_array_equal(saved, request['images'][channel])
        second = media_export.export_channels(request, {})
        self.assertNotEqual(result['directory'], second['directory'])

    def test_static_video_duration_and_odd_dimensions(self):
        request = self.request()
        request.update(format='视频', channels=['original', 'mask'])
        result = media_export.export_channels(request, {})
        self.assertEqual(len(result['outputs']), 2)
        for path in result['outputs']:
            self.assertEqual(pipeline.video_info(path), (3, 12., 26, 18))

    def test_video_to_current_frame_and_sequence(self):
        video = self.folder / '源视频.mp4'
        media_export.encode_video([self.original, self.processed, self.original], 3, 12., video)
        expected = list(pipeline.iter_frames(str(video)))[1][1]
        request = self.request()
        request.update(source=str(video), is_image=False, channels=['original'], frame=1, frames=3)
        current = media_export.export_channels(request, {})
        self.assertEqual(len(current['outputs']), 1)
        np.testing.assert_array_equal(pipeline.imread(current['outputs'][0]), expected)
        request['scope'] = '全部帧'
        sequence = media_export.export_channels(request, {})
        paths = sorted(Path(sequence['outputs'][0]).glob('*.png'))
        self.assertEqual([path.name for path in paths], ['000000.png', '000001.png', '000002.png'])
        np.testing.assert_array_equal(pipeline.imread(str(paths[1])), expected)

    def test_failed_frame_stream_keeps_previous_file(self):
        destination = self.folder / 'existing.mp4'
        destination.write_bytes(b'previous output')
        with self.assertRaises(ValueError):
            media_export.encode_video([self.original], 2, 12., destination)
        self.assertEqual(destination.read_bytes(), b'previous output')
        self.assertFalse(list(self.folder.glob('.dlss-export-*')))

    def test_empty_and_unavailable_channels_rejected(self):
        for channels in ([], ['flow'], ['original', 'original'], ['unsupported']):
            request = self.request()
            request['channels'] = channels
            with self.assertRaises(ValueError):
                media_export.validate_request(request)
        request = self.request()
        request.update(format='视频', duration=float('nan'))
        with self.assertRaises(ValueError):
            media_export.validate_request(request)


if __name__ == '__main__':
    unittest.main()
