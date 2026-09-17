from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
from parameter_presets import PresetStore, normalize_parameters


class PresetTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / 'logs/preset-unit'
        directory.mkdir(parents=True, exist_ok=True)
        self.store = PresetStore(Path(tempfile.mkdtemp(dir=directory)) / 'parameter_presets.json')
        self.parameters = normalize_parameters({'settings': {
            'intensity': .35, 'overall_weight': .65,
            'color_preservation': {'strength': .8, 'mask_scope': 'color'},
            'input_denoise': {'enabled': False, 'luma': 12, 'chroma': 8, 'weight': .5},
            'output_denoise': {'enabled': True, 'luma': 2, 'chroma': 4, 'weight': .3},
            'super_resolution': {'engine': 'seedvr2', 'scale': 4, 'seed': 19, 'batch': 9,
                                 'model_root': 'F:/本地模型'}},
            'second_layer_parameters': {'preset': 3, 'style': 2, 'local_struct': 3.7}})

    def test_create_restart_update_rename_activate_delete(self):
        first = self.store.save('  人像 柔和  ', self.parameters)
        identifier = first['active_id']
        self.assertEqual(PresetStore(self.store.path).load(), first)
        self.assertEqual(first['presets'][0]['name'], '人像 柔和')
        changed = copy.deepcopy(self.parameters)
        changed['settings']['intensity'] = .75
        renamed = self.store.save('夜景', changed, identifier)
        self.assertEqual(len(renamed['presets']), 1)
        self.assertEqual(renamed['presets'][0]['name'], '夜景')
        self.assertEqual(renamed['presets'][0]['parameters'], changed)
        second = self.store.save('动画', self.parameters)['active_id']
        self.assertEqual(self.store.activate(identifier)['active_id'], identifier)
        self.assertEqual(self.store.delete(second)['active_id'], identifier)
        self.assertEqual(self.store.delete(identifier), {'version': 1, 'active_id': None, 'presets': []})

    def test_disabled_stages_keep_parameters(self):
        data = self.store.save('关闭后再启用', self.parameters)
        actual = self.store.load()['presets'][0]['parameters']
        self.assertEqual(actual, self.parameters)
        self.assertIsNone(actual['settings']['second_layer'])
        self.assertEqual(actual['second_layer_parameters']['local_struct'], 3.7)
        self.assertEqual(actual['settings']['input_denoise']['luma'], 12)
        self.assertEqual(data['presets'][0]['parameters']['settings']['super_resolution']['batch'], 9)

    def test_duplicate_names_never_overwrite(self):
        first = self.store.save('Test', self.parameters)['active_id']
        second = self.store.save('其他', self.parameters)['active_id']
        original = self.store.path.read_bytes()
        for identifier in (None, second):
            with self.assertRaisesRegex(ValueError, '同名'):
                self.store.save(' test ', self.parameters, identifier)
            self.assertEqual(self.store.path.read_bytes(), original)
        self.store.save('TEST', self.parameters, first)

    def test_invalid_names_parameters_and_deleted_id_do_not_write(self):
        self.store.save('原有', self.parameters)
        original = self.store.path.read_bytes()
        for name in ('', '  ', 'a\nb', 'a' * 81):
            with self.assertRaises(ValueError):
                self.store.save(name, self.parameters)
        with self.assertRaises(ValueError):
            self.store.save('新', {'settings': {'overall_weight': float('nan')}})
        for operation in (lambda: self.store.save('原有', self.parameters, 'missing'),
                          lambda: self.store.activate('missing'), lambda: self.store.delete('missing')):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_corrupt_or_future_file_preserved(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        for text in ('{bad json', '[]', '{"version":2,"presets":[]}',
                     json.dumps({'version': 1, 'active_id': None, 'presets': [{'id': 'a'}]})):
            self.store.path.write_text(text, encoding='utf-8')
            original = self.store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, '原文件已保留'):
                self.store.save('新', self.parameters)
            self.assertEqual(self.store.path.read_bytes(), original)

    def test_failed_atomic_replace_keeps_previous_data(self):
        self.store.save('原有', self.parameters)
        original = self.store.path.read_bytes()
        with patch('parameter_presets.os.replace', side_effect=OSError('磁盘写入失败')):
            with self.assertRaises(OSError):
                self.store.save('新', self.parameters)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.store.path.parent.glob('*.tmp')), [])

    def test_multiple_store_instances_reload_and_lock(self):
        other = PresetStore(self.store.path)
        self.store.save('第一窗口', self.parameters)
        other.save('第二窗口', self.parameters)
        self.assertEqual(len(self.store.load()['presets']), 2)
        with self.store._locked():
            with self.assertRaisesRegex(ValueError, '另一个窗口'):
                other.save('同时写入', self.parameters)
        self.assertEqual(len(other.save('锁释放后', self.parameters)['presets']), 3)


if __name__ == '__main__':
    unittest.main()
