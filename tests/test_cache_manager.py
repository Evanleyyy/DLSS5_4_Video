import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import cache_manager as cache


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='regression-cache-', dir=ROOT / 'tests'))
        self.video = self.directory / '中文视频.mp4'
        self.video.write_bytes(b'source')

    def create_cache(self, kind='depth', source=None):
        folder = self.directory / (self.video.stem + '_' + kind)
        folder.mkdir()
        (folder / 'cache.json').write_text(json.dumps({'record': {
            'source': str(source or self.video), 'options': {'kind': kind}}}), encoding='utf-8')
        extension = {'depth': 'jpg', 'flow': 'flo', 'dlss': 'png'}[kind]
        (folder / ('000000.' + extension)).write_bytes(b'generated')
        return folder

    def test_cleanup_keeps_source_export_and_unrecognized_files(self):
        folder = self.create_cache()
        export = self.directory / '导出.png'
        export.write_bytes(b'export')
        (folder / '用户笔记.txt').write_text('保留', encoding='utf-8')
        (folder / '子目录').mkdir()
        (folder / '子目录' / '000000.jpg').write_bytes(b'keep')
        self.assertGreater(cache.clean_video(str(self.video), 'depth'), 0)
        self.assertFalse((folder / '000000.jpg').exists())
        self.assertTrue((folder / '用户笔记.txt').exists())
        self.assertTrue((folder / '子目录' / '000000.jpg').exists())
        self.assertEqual(export.read_bytes(), b'export')
        self.assertEqual(self.video.read_bytes(), b'source')

    def test_each_video_cache_type_removed_only_when_owned(self):
        for kind in ('depth', 'flow', 'dlss'):
            folder = self.create_cache(kind)
            cache.clean_video(str(self.video), kind)
            self.assertFalse(folder.exists())
        folder = self.create_cache(source=self.directory / '别的视频.mp4')
        with self.assertRaises(ValueError):
            cache.clean_video(str(self.video), 'depth')
        self.assertTrue((folder / '000000.jpg').exists())

    def test_missing_or_malformed_marker_preserves_frames(self):
        folder = self.create_cache()
        (folder / 'cache.json').write_text('invalid', encoding='utf-8')
        with self.assertRaises(ValueError):
            cache.clean_video(str(self.video), 'depth')
        self.assertTrue((folder / '000000.jpg').exists())

    def test_reparse_frame_rejected_before_deletion(self):
        folder = self.create_cache()
        actual = Path.lstat
        frame = folder / '000000.jpg'
        def fake_lstat(path, *args, **kwargs):
            info = actual(path, *args, **kwargs)
            if path == frame:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'lstat', fake_lstat), self.assertRaises(ValueError):
            cache.clean_video(str(self.video), 'depth')
        self.assertTrue(frame.exists())

    def test_processing_lock_prevents_cleanup_and_allows_nested_operations(self):
        folder = self.create_cache()
        errors = []
        def clean():
            try:
                cache.clean_video(str(self.video), 'depth')
            except RuntimeError as error:
                errors.append(str(error))
        with cache.video_cache_guard(str(self.video)):
            with cache.video_cache_guard(str(self.video)):
                thread = threading.Thread(target=clean)
                thread.start()
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertTrue((folder / '000000.jpg').exists())

    def test_runtime_scan_only_recognizes_software_owned_versions(self):
        root = self.directory / 'runtime'
        root.mkdir()
        valid = root / ('a' * 20)
        valid.mkdir()
        (valid / 'ready.txt').write_text('a' * 20, encoding='utf-8-sig')
        (valid / 'payload').write_bytes(b'payload')
        unknown = root / ('b' * 20)
        unknown.mkdir()
        (unknown / 'ready.txt').write_text('wrong')
        entries, warnings = cache.runtime_entries(root)
        self.assertEqual([entry['id'] for entry in entries], ['a' * 20])
        self.assertFalse(warnings)
        self.assertEqual(entries[0]['size'], 30)


if __name__ == '__main__':
    unittest.main()
