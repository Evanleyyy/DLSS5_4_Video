from pathlib import Path
import json
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_runtime as runtimes
import dlss_layers
import parameter_presets
import pipeline


def fatbin(architectures, kind=2):
    records = bytearray()
    for sm in architectures:
        record = bytearray(72)
        struct.pack_into('<HHIQ', record, 0, kind, 0x101, 64, 8)
        struct.pack_into('<I', record, 28, sm)
        records.extend(record)
    return struct.pack('<IHHQ', 0xBA55ED50, 1, 16, len(records)) + records


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='runtime-test-', dir=ROOT / 'logs'))

    def test_architecture_reader_validates_bounds_and_ignores_ptx(self):
        path = self.folder / 'test.dll'
        path.write_bytes(fatbin([75, 86, 89, 120]) + fatbin([80], kind=1) + b'\x50\xed\x55\xba')
        self.assertEqual(runtimes.file_info(path)['architectures'], [75, 86, 89, 120])
        path.write_bytes(fatbin([86])[:-1])
        self.assertEqual(runtimes.file_info(path)['architectures'], [])

    def test_30_series_auto_uses_compatible_runtime_and_rejects_40_only(self):
        path = self.folder / 'runtime.dll'
        path.write_bytes(fatbin([86, 89]))
        with patch.object(runtimes, 'version_path', return_value=path) as paths, \
             patch.object(runtimes, 'runtime_root', return_value=self.folder):
            result = runtimes.resolve('auto', {'sm': 86})
            self.assertEqual(result['id'], '310.8.SF-v2')
            paths.assert_called_with('310.8.SF-v2')
            path.write_bytes(fatbin([89]))
            with self.assertRaisesRegex(ValueError, 'sm_86'):
                runtimes.resolve('bundled', {'sm': 86})

    def test_manifest_hash_failure_is_not_silently_loaded(self):
        path = self.folder / 'runtime.dll'
        path.write_bytes(fatbin([86]))
        (self.folder / 'manifest.json').write_text(json.dumps({'runtimes': {'310.8.SF-v2': {'sha256': '0' * 64}}}))
        with patch.object(runtimes, 'version_path', return_value=path), \
             patch.object(runtimes, 'runtime_root', return_value=self.folder):
            with self.assertRaisesRegex(ValueError, '校验失败'):
                runtimes.resolve('310.8.SF-v2', {'sm': 86})

    def test_missing_runtime_and_unknown_gpu_are_reported(self):
        with self.assertRaisesRegex(ValueError, '手动选择'):
            runtimes.resolve('auto', {'sm': None})
        with patch.object(runtimes, 'version_path', return_value=self.folder / 'missing.dll'):
            with self.assertRaises(FileNotFoundError):
                runtimes.resolve('310.8.SF-v2', {'sm': 86})

    def test_both_model_versions_survive_named_preset_round_trip(self):
        parameters = parameter_presets.normalize_parameters({'settings': {
            'runtime_version': '310.8.SF-v2', 'second_layer': {'runtime_version': 'bundled'}},
            'second_layer_parameters': {'runtime_version': '310.8.SF'}})
        self.assertEqual(parameters['settings']['runtime_version'], '310.8.SF-v2')
        self.assertEqual(parameters['settings']['second_layer']['runtime_version'], '310.8.SF')
        self.assertEqual(dlss_layers.normalize_settings({})['runtime_version'], 'auto')

    def test_runtime_change_invalidates_completed_frame_cache(self):
        video = self.folder / 'clip.mp4'
        video.write_bytes(b'test source')
        directory = Path(pipeline.out_dirs(str(video))[2])
        directory.mkdir()
        settings = dlss_layers.normalize_settings({'runtime_version': '310.8.SF-v2'})
        with patch.object(runtimes, 'fingerprint', return_value=[{'id': '310.8.SF-v2', 'sha256': 'old'}]):
            record = pipeline._cache_record(str(video), {'kind': 'dlss', 'frames': 1, 'settings': settings})
            pipeline._finish_cache(str(directory), record)
            self.assertTrue(pipeline.dlss_cache_matches(str(video), settings))
        with patch.object(runtimes, 'fingerprint', return_value=[{'id': '310.8.SF-v2', 'sha256': 'new'}]):
            self.assertFalse(pipeline.dlss_cache_matches(str(video), settings))

    def test_preferences_are_local_and_restore(self):
        with patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.folder)):
            runtimes.save_preferences('310.8.SF-v2', 'bundled')
            self.assertEqual(runtimes.load_preferences(), ['310.8.SF-v2', 'bundled'])

    def test_legacy_40_series_auto_still_uses_bundled_runtime(self):
        path = self.folder / 'original.dll'
        path.write_bytes(fatbin([89]))
        with patch.object(runtimes, 'version_path', return_value=path) as paths, \
             patch.object(runtimes, 'runtime_root', return_value=self.folder):
            result = runtimes.resolve('auto', {'sm': 89})
            self.assertEqual(result['id'], 'bundled')
            paths.assert_called_with('bundled')

    def test_037_preset_loads_without_rewriting_existing_user_file(self):
        path = self.folder / 'parameter_presets.json'
        old = {'version': 1, 'active_id': 'legacy', 'presets': [{
            'id': 'legacy', 'name': '原版双层预设', 'parameters': {
                'settings': {'preset': 2, 'intensity': .45, 'overall_weight': .7,
                             'second_layer': {'preset': 3, 'intensity': .6},
                             'super_resolution': {'engine': 'seedvr2', 'scale': 4}},
                'second_layer_parameters': {'preset': 3, 'intensity': .6}}}]}
        path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
        before = path.read_bytes()
        loaded = parameter_presets.PresetStore(path).load()
        settings = loaded['presets'][0]['parameters']['settings']
        self.assertEqual(loaded['active_id'], 'legacy')
        self.assertEqual(settings['runtime_version'], 'auto')
        self.assertEqual(settings['second_layer']['runtime_version'], 'auto')
        self.assertEqual((settings['preset'], settings['intensity'], settings['overall_weight']),
                         (2, .45, .7))
        self.assertEqual(settings['second_layer']['preset'], 3)
        self.assertEqual(settings['super_resolution']['engine'], 'seedvr2')
        self.assertEqual(settings['super_resolution']['scale'], 4)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
