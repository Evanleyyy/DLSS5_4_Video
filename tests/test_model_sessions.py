"""Resource lifetimes across task boundaries, not just per-frame caching."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_layers
import model_sessions
import runtime_session
NativeSession = runtime_session.Live


class ModelSessionTests(unittest.TestCase):
    def setUp(self):
        self.owner = model_sessions.ModelSessions()
        self.addCleanup(self.owner.close)
        self.instances = []
        instances = self.instances
        class Native:
            def __init__(self, width, height, settings):
                self.settings, self.closed, self.resets = dict(settings), False, []
                instances.append(self)
            def process(self, rgba, motion, depth, reset=False):
                if self.closed:
                    raise RuntimeError('closed')
                self.resets.append(reset)
                return rgba.copy()
            def update(self, settings):
                self.settings.update(settings)
            def close(self, **kwargs):
                self.closed = True
        factory = patch('dlss_layers.runtime_session.Live', Native)
        factory.start()
        self.addCleanup(factory.stop)

    def render(self, settings=None, width=8, owner=None):
        with model_sessions.bind(owner or self.owner):
            lease = model_sessions.acquire_dlss(width, 8, settings or {})
            try:
                lease.process(np.zeros((8, width, 4), np.uint8), np.zeros((8, width, 2), np.float32),
                              np.zeros((8, width), np.float32), reset=True)
            finally:
                lease.close()

    def test_different_confirmed_parameters_reuse_model_and_reset_history(self):
        self.render({'intensity': .9})
        self.render({'intensity': .2, 'style': 1})
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(self.instances[0].settings['intensity'], .2)
        self.assertEqual(self.instances[0].resets, [True, True])
        self.assertFalse(self.instances[0].closed)
        self.owner.close()
        self.assertTrue(self.instances[0].closed)

    def test_dimensions_rebuild_before_creating_new_session(self):
        self.render(width=8)
        self.render(width=16)
        self.assertEqual(len(self.instances), 2)
        self.assertTrue(self.instances[0].closed)
        self.assertFalse(self.instances[1].closed)

    def test_both_layers_reuse_and_disabling_second_frees_only_second(self):
        self.render({'second_layer': {'intensity': .8}})
        self.render({'intensity': .5, 'second_layer': {'intensity': .2}})
        self.assertEqual(len(self.instances), 2)
        self.render({})
        self.assertFalse(self.instances[0].closed)
        self.assertTrue(self.instances[1].closed)

    def test_switching_to_super_resolution_releases_dlss_and_reuses_worker(self):
        self.render()
        worker = Mock(alive=True)
        factory = Mock(return_value=worker)
        self.owner.acquire_sr(('seedvr2', 'weights-a'), factory)
        self.assertTrue(self.instances[0].closed)
        self.owner.acquire_sr(('seedvr2', 'weights-a'), factory)
        factory.assert_called_once()
        self.owner.acquire_sr(('seedvr2', 'weights-b'), factory)
        worker.close.assert_called_once()
        self.assertEqual(factory.call_count, 2)

    def test_switching_back_to_dlss_releases_super_resolution(self):
        worker = Mock(alive=True)
        self.owner.acquire_sr(('vosr',), lambda: worker)
        self.render()
        worker.close.assert_called_once()

    def test_dead_super_resolution_worker_is_replaced(self):
        worker = Mock(alive=False)
        factory = Mock(return_value=worker)
        self.owner.acquire_sr(('pisa',), factory)
        self.owner.acquire_sr(('pisa',), factory)
        self.assertEqual(factory.call_count, 2)
        worker.close.assert_called_once()

    def test_guidance_load_evicts_resident_renderers_only_in_active_owner(self):
        self.render()
        with model_sessions.bind(self.owner):
            model_sessions.release_for_guidance()
        self.assertTrue(self.instances[0].closed)
        self.assertIsNone(self.owner.dlss)

    def test_failed_native_call_discards_tainted_model(self):
        self.render()
        self.instances[0].process = Mock(side_effect=RuntimeError('GPU failure'))
        with self.assertRaisesRegex(RuntimeError, 'GPU failure'):
            self.render({'intensity': .4})
        self.assertIsNone(self.owner.dlss)
        self.assertTrue(self.instances[0].closed)
        self.render()
        self.assertEqual(len(self.instances), 2)

    def test_zero_weight_releases_loaded_layers_without_loading_new_ones(self):
        self.render({'second_layer': {}})
        self.render({'overall_weight': 0})
        self.assertEqual(len(self.instances), 2)
        self.assertTrue(all(item.closed for item in self.instances))

    def test_native_parameter_update_preserves_process_but_create_options_restart(self):
        live = NativeSession.__new__(NativeSession)
        live._lock, live._closed = threading.RLock(), False
        live.settings, live.runtime = {'preset': 1, 'runtime_version': 'auto'}, {'id': 'bundled'}
        live._start, live._stop = Mock(), Mock()
        live.update({'intensity': .4})
        live._stop.assert_not_called()
        with patch('runtime_session.dlss_runtime.resolve', return_value={'id': 'bundled'}):
            live.update({'runtime_version': 'bundled'})
        live._stop.assert_not_called()
        live.update({'preset': 2})
        live._stop.assert_called_once()
        live._start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
