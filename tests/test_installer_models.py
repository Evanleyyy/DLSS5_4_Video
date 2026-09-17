from pathlib import Path
import hashlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import installer_models
import model_assets


class InstallerModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='setup-models-', dir=ROOT / 'logs'))
        self.source = self.folder / '安装包 目录'
        self.source.mkdir()
        self.destination = self.folder / '已安装程序/runtime/sr-models'
        self.settings = {'model_root': str(self.destination)}
        self.payload = b'checked local model'
        self.entry = {'path': 'seedvr2/model.safetensors', 'size': len(self.payload),
                      'sha256': hashlib.sha256(self.payload).hexdigest()}
        assets = patch.object(model_assets, 'assets', return_value=[self.entry])
        assets.start()
        self.addCleanup(assets.stop)
        env = patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.folder / 'data'))
        env.start()
        self.addCleanup(env.stop)
        network = patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('安装检测不能联网'))
        network.start()
        self.addCleanup(network.stop)

    def place(self, relative, content=None):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload if content is None else content)
        return path

    def install(self, **kwargs):
        return installer_models.install_local(self.source, ['seedvr2'], self.settings, **kwargs)

    def test_supported_layouts_install_without_network(self):
        for relative in ['seedvr2/model.safetensors', 'models/seedvr2/model.safetensors',
                         'sr-models/seedvr2/model.safetensors', 'runtime/sr-models/seedvr2/model.safetensors',
                         'model.safetensors', 'models/model.safetensors']:
            with self.subTest(relative=relative):
                path = self.place(relative)
                result = self.install()
                target = self.destination / self.entry['path']
                self.assertEqual((result['copied'], result['missing']), (1, []))
                self.assertEqual(target.read_bytes(), self.payload)
                self.assertEqual(path.read_bytes(), self.payload)
                target.unlink()
                path.unlink()

    def test_existing_target_is_not_rewritten(self):
        self.place(self.entry['path'])
        self.install()
        target = self.destination / self.entry['path']
        before = target.stat().st_mtime_ns
        result = self.install()
        self.assertEqual((result['reused'], result['copied']), (1, 0))
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_missing_or_bad_same_size_model_only_reports_download_needed(self):
        for payload in (None, b'x' * len(self.payload)):
            if payload is not None:
                self.place(self.entry['path'], payload)
            result = self.install()
            self.assertEqual(result['missing'], [self.entry['path']])
            self.assertEqual(result['components']['seedvr2']['missing_bytes'], len(self.payload))
            self.assertFalse((self.destination / self.entry['path']).exists())

    def test_mixed_files_install_valid_part_and_report_missing_part(self):
        second = {**self.entry, 'path': 'seedvr2/vae.safetensors'}
        self.place(self.entry['path'])
        with patch.object(model_assets, 'assets', return_value=[self.entry, second]):
            result = self.install()
        self.assertEqual((result['copied'], result['missing']), (1, [second['path']]))

    def test_parent_directory_is_never_scanned(self):
        outside = self.source.parent / self.entry['path']
        outside.parent.mkdir()
        outside.write_bytes(self.payload)
        self.assertEqual(self.install()['copied'], 0)

    def test_previous_configured_root_is_copied_without_changing_source(self):
        previous = self.folder / '旧模型目录'
        path = previous / self.entry['path']
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        before = path.stat().st_mtime_ns
        result = self.install(extra_roots=[previous])
        self.assertEqual(result['copied'], 1)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual((self.destination / self.entry['path']).read_bytes(), self.payload)

    def test_setup_inside_installation_reuses_models_without_self_copy(self):
        self.place('runtime/sr-models/' + self.entry['path'])
        result = installer_models.install_local(self.source, ['seedvr2'],
            {'model_root': str(self.source / 'runtime/sr-models')})
        self.assertEqual((result['reused'], result['copied'], result['missing']), (1, 0, []))

    def test_copy_failure_keeps_original_target_and_cleans_only_own_partial(self):
        source = self.place(self.entry['path'])
        destination = self.destination / self.entry['path']
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'old model')
        def cancel_after_write(done, total):
            raise InterruptedError('取消')
        with self.assertRaises(InterruptedError):
            installer_models._copy_verified(source, destination, self.entry, lambda: None, cancel_after_write)
        self.assertEqual(destination.read_bytes(), b'old model')
        self.assertEqual(source.read_bytes(), self.payload)
        self.assertFalse(list(destination.parent.glob('*.install-*.part')))

    def test_legacy_guidance_layout_is_copied_to_model_root(self):
        entry = {**self.entry, 'path': 'guidance/depth.pth', 'legacy_path': 'models/checkpoints/depth.pth'}
        self.place('_internal/models/checkpoints/depth.pth')
        with patch.object(model_assets, 'assets', return_value=[entry]):
            result = installer_models.install_local(self.source, ['guidance'], self.settings)
        self.assertEqual(result['copied'], 1)
        self.assertEqual((self.destination / entry['path']).read_bytes(), self.payload)


if __name__ == '__main__':
    unittest.main()
