import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import model_assets

PAYLOAD = b'model-test-content\n' * 180000


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.requests.append((self.path, self.headers.get('Range')))
        if self.path == '/denied':
            self.send_error(401)
            return
        offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
        if self.path == '/ignore':
            offset = 0
        payload = b'x' * len(PAYLOAD) if self.path == '/corrupt' else PAYLOAD
        self.send_response(206 if offset else 200)
        if offset:
            self.send_header('Content-Range', f'bytes {offset}-{len(payload) - 1}/{len(payload)}')
        self.send_header('Content-Length', str(len(payload) - offset))
        self.end_headers()
        try:
            self.wfile.write(payload[offset:])
        except (ConnectionError, OSError):
            pass


class ModelAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='model-assets-', dir=ROOT / 'logs'))
        self.root = self.directory / 'models'
        self.path = self.root / 'seedvr2/model.safetensors'
        self.path.parent.mkdir(parents=True)
        self.partial = self.path.with_name(self.path.name + '.part')
        self.entry = {'path': 'seedvr2/model.safetensors', 'size': len(PAYLOAD),
                      'sha256': hashlib.sha256(PAYLOAD).hexdigest(),
                      'url': f'http://127.0.0.1:{self.server.server_port}/range'}
        self.env = patch.dict(os.environ, DLSS5_DATA_ROOT=str(self.directory / 'data'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.entries = patch.object(model_assets, 'assets', return_value=[self.entry])
        self.assets_mock = self.entries.start()
        self.addCleanup(self.entries.stop)
        Handler.requests.clear()

    def prepare(self, **kwargs):
        return model_assets.prepare(['seedvr2'], {'model_root': str(self.root)}, **kwargs)

    def test_valid_local_file_never_contacts_network(self):
        self.path.write_bytes(PAYLOAD)
        with patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('不应联网')):
            result = self.prepare()
            self.assertEqual(result['reused'], 1)
            self.assertEqual(result['downloaded'], 0)
            self.assertEqual(self.prepare()['reused'], 1)

    def test_missing_file_downloaded_then_reused(self):
        self.assertEqual(self.prepare()['downloaded'], 1)
        self.assertEqual(self.path.read_bytes(), PAYLOAD)
        self.assertFalse(self.partial.exists())
        self.assertEqual(self.prepare()['reused'], 1)
        self.assertEqual(len(Handler.requests), 1)

    def test_check_only_is_offline(self):
        with patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('不应联网')):
            self.assertEqual(self.prepare(check_only=True)['missing'], [self.entry['path']])
        self.assertFalse(self.path.exists())

    def test_only_missing_file_is_downloaded(self):
        other = self.path.with_name('local.safetensors')
        other.write_bytes(PAYLOAD)
        self.assets_mock.return_value = [{**self.entry, 'path': 'seedvr2/local.safetensors'}, self.entry]
        result = self.prepare()
        self.assertEqual((result['reused'], result['downloaded']), (1, 1))
        self.assertEqual(len(Handler.requests), 1)

    def test_resume_partial(self):
        self.partial.write_bytes(PAYLOAD[:1000])
        self.prepare()
        self.assertEqual(Handler.requests[0][1], 'bytes=1000-')
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_server_ignoring_range_restarts_safely(self):
        self.entry['url'] = self.entry['url'].replace('/range', '/ignore')
        self.partial.write_bytes(PAYLOAD[:1000])
        self.prepare()
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_complete_partial_is_verified_without_network(self):
        self.partial.write_bytes(PAYLOAD)
        with patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('不应联网')):
            self.prepare()
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_bad_hash_never_overwrites_existing_file(self):
        self.path.write_bytes(b'original file')
        self.entry['url'] = self.entry['url'].replace('/range', '/corrupt')
        with patch.object(model_assets.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '校验失败'):
            self.prepare()
        self.assertEqual(self.path.read_bytes(), b'original file')

    def test_modified_local_file_invalidates_verification_receipt(self):
        self.path.write_bytes(PAYLOAD)
        self.prepare()
        self.path.write_bytes(b'x' * len(PAYLOAD))
        self.assertEqual(self.prepare()['downloaded'], 1)
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_access_error_has_no_mirror_fallback(self):
        self.entry['url'] = self.entry['url'].replace('/range', '/denied')
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            self.prepare()
        self.assertEqual(len(Handler.requests), 1)
        self.assertFalse(self.path.exists())

    def test_cancel_retains_partial_and_next_run_resumes(self):
        def checkpoint():
            if self.partial.exists() and self.partial.stat().st_size >= 1048576:
                raise InterruptedError('停止测试下载')
        with self.assertRaises(InterruptedError):
            self.prepare(checkpoint=checkpoint)
        offset = self.partial.stat().st_size
        self.assertFalse(self.path.exists())
        self.prepare()
        self.assertEqual(Handler.requests[-1][1], f'bytes={offset}-')
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_concurrent_preparation_is_rejected(self):
        with model_assets._locked(self.root), self.assertRaisesRegex(RuntimeError, '另一窗口'):
            self.prepare()

    def test_path_cannot_escape_model_root(self):
        self.entry['path'] = '../outside.bin'
        with self.assertRaisesRegex(ValueError, '超出'):
            self.prepare()
        self.assertFalse((self.directory / 'outside.bin').exists())

    def test_real_manifest_uses_official_sources(self):
        self.entries.stop()
        entries = model_assets.assets(['pisa', 'seedvr2', 'vosr', 'guidance'])
        self.assertEqual(len(entries), 184)
        self.assertTrue(all('Manojb' not in item['url'] for item in entries))
        self.assertTrue(all(item['url'].startswith('https://') for item in entries))
        sd = next(item for item in entries if item['path'].startswith('pisa/sd21/'))
        self.assertIn('/stabilityai/', sd['url'])

    def test_legacy_guidance_is_reused(self):
        bundle = self.directory / 'old-internal'
        bundle.mkdir()
        (bundle / 'depth.pth').write_bytes(PAYLOAD)
        self.entry['legacy_path'] = 'depth.pth'
        with patch.object(sys, '_MEIPASS', str(bundle), create=True), patch.object(model_assets.urllib.request, 'urlopen') as request:
            result = self.prepare()
        request.assert_not_called()
        self.assertEqual(result['paths'][self.entry['path']], str(bundle / 'depth.pth'))

    def test_missing_runtime_does_not_trigger_weight_download(self):
        import sr_backend
        with patch.object(sr_backend.sr_settings, 'missing_runtime', return_value=['python.exe']), \
                patch.object(model_assets, 'prepare') as prepare:
            with self.assertRaisesRegex(FileNotFoundError, '运行库不完整'):
                sr_backend.run_job({'settings': {'super_resolution': {'engine': 'seedvr2'}}})
        prepare.assert_not_called()

    def test_first_download_publishes_current_model_fingerprint(self):
        import pipeline
        import sr_backend
        import sr_settings
        video = self.directory / 'video.mp4'
        video.write_bytes(b'source-metadata-only')
        cfg = {'super_resolution': {'engine': 'seedvr2'}}
        before, after = [('model', 0, 0)], [('model', 123, 456)]
        with patch.object(pipeline, 'video_info', return_value=(1, 30, 100, 100)), \
                patch.object(sr_settings, 'fingerprint', side_effect=[before, after]), \
                patch.object(sr_backend, 'process_video', return_value={'count': 1}):
            self.assertEqual(pipeline.generate_dlss(str(video), cfg), 1)
        state = json.loads((Path(pipeline.out_dirs(str(video))[2]) / 'cache.json').read_text())
        self.assertTrue(state['complete'])
        self.assertEqual(state['record']['options']['model_assets'], json.loads(json.dumps(after)))


if __name__ == '__main__':
    unittest.main()
