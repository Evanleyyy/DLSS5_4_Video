import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import task_control


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('等待状态超时')
        time.sleep(.01)


class PauseTests(unittest.TestCase):
    def test_running_worker_stops_when_parent_is_gone(self):
        directory = Path(tempfile.mkdtemp(prefix='owner-dead-', dir=ROOT / 'logs'))
        (directory / 'pause-request.json').write_text(json.dumps({
            'requested': False, 'token': 0, 'parent_pid': 12345}))
        remote = task_control.FilePauseControl(directory)
        with patch.object(task_control, '_parent_alive', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '主程序已退出'):
                remote.checkpoint()

    def test_pause_holds_progress_and_continues_same_sequence(self):
        control = task_control.PauseControl()
        first, proceed = threading.Event(), threading.Event()
        values = []
        def work():
            with task_control.bind(control):
                for index in range(4):
                    task_control.checkpoint()
                    values.append(index)
                    if index == 0:
                        first.set()
                        proceed.wait(3)
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(first.wait(3))
            control.pause()
            self.assertEqual(control.state, 'pausing')
            proceed.set()
            until(lambda: control.state == 'paused')
            task_control.checkpoint()  # The UI thread must never inherit the worker pause.
            time.sleep(.1)
            self.assertEqual(values, [0])
            control.resume()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(values, [0, 1, 2, 3])
        finally:
            control.finish()
            proceed.set()
            thread.join(3)

    def test_repeated_requests_and_finish_release_waiter(self):
        control = task_control.PauseControl()
        control.pause()
        control.resume()
        control.pause()
        thread = threading.Thread(target=control.checkpoint)
        thread.start()
        try:
            until(lambda: control.state == 'paused')
            control.finish()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            control.pause()
            self.assertEqual(control.state, 'finished')
        finally:
            control.finish()

    def test_remote_acknowledgement_tokens_and_resume(self):
        directory = Path(tempfile.mkdtemp(prefix='pause-', dir=ROOT / 'logs'))
        synchronized = []
        local = task_control.PauseControl()
        remote = task_control.FilePauseControl(directory, lambda: synchronized.append(True))
        with local.remote(directory):
            local.pause()
            thread = threading.Thread(target=remote.checkpoint)
            thread.start()
            try:
                until(local.sync_remote)
                self.assertEqual(local.state, 'paused')
                self.assertTrue(thread.is_alive())
                self.assertEqual(synchronized, [True])
                local.resume()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                local.pause()
                self.assertFalse(local.sync_remote())  # Previous pause acknowledgement is stale.
                self.assertEqual(local.state, 'pausing')
                local.finish()
                self.assertFalse(json.loads((directory / 'pause-request.json').read_text())['requested'])
            finally:
                local.finish()
                thread.join(3)


class PipelinePauseTests(unittest.TestCase):
    def test_video_generation_and_export_stop_between_frames(self):
        import cv2
        import numpy as np
        import torch
        import pipeline
        import dlss_layers
        import media_export
        directory = Path(tempfile.mkdtemp(prefix='pause-pipelines-', dir=ROOT / 'logs'))
        video = directory / 'input.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 6, (64, 48))
        self.assertTrue(writer.isOpened())
        for index in range(8):
            writer.write(np.full((48, 64, 3), index * 20, np.uint8))
        writer.release()

        class FakeLive:
            def __init__(self, *args):
                pass
            def process(self, rgba, *args, **kwargs):
                return rgba.copy()
            def close(self):
                pass

        def fake_flow(first, second, **kwargs):
            return [torch.zeros((1, 2, first.shape[-2], first.shape[-1]))]

        with patch.object(pipeline, 'get_depth_model', return_value=object()), \
             patch.object(pipeline, 'infer_depth_frame', side_effect=lambda frame, **kw: np.zeros(frame.shape[:2], np.float32)), \
             patch.object(pipeline, 'get_flow_model', return_value=(fake_flow, lambda a, b: (a, b))), \
             patch.object(pipeline, 'DEVICE', 'cpu'), patch.object(dlss_layers, 'LayeredLive', FakeLive):
            for kind in ('depth', 'flow', 'dlss', 'export'):
                with self.subTest(kind=kind):
                    control = task_control.PauseControl()
                    progress, errors = [], []
                    def report(i, n, *args):
                        progress.append(i)
                        if len(progress) == 2:
                            control.pause()
                    def work():
                        try:
                            with task_control.bind(control):
                                if kind == 'export':
                                    frames = (np.full((48, 64, 3), i * 20, np.uint8) for i in range(8))
                                    media_export.encode_video(frames, 8, 6, directory / 'output.mp4', progress=report)
                                else:
                                    getattr(pipeline, 'generate_' + kind)(str(video), progress=report)
                        except Exception as error:
                            errors.append(error)
                    thread = threading.Thread(target=work)
                    thread.start()
                    try:
                        until(lambda: control.state == 'paused' or bool(errors), 15)
                        self.assertEqual(errors, [])
                        self.assertEqual(len(progress), 2)
                        time.sleep(.1)
                        self.assertEqual(len(progress), 2)
                        control.resume()
                        thread.join(15)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(errors, [])
                        self.assertEqual(len(progress), 8)
                    finally:
                        control.finish()
                        thread.join(15)
            self.assertEqual(pipeline.video_info(str(directory / 'output.mp4'))[0], 8)


if __name__ == '__main__':
    unittest.main()
