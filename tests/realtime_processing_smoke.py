"""Exercise real Tk event scheduling, latest-only edits, masks and resident models."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import tkinter as tk
    import numpy as np
    import gui
    import pipeline
    instances, calls, errors = [], [], []
    gate = threading.Event()
    gate.set()
    class Renderer:
        def __init__(self, width, height, settings):
            self.settings, self.closed = dict(settings), False
            instances.append(self)
        def update(self, settings):
            self.settings.update(settings)
        def process(self, rgba, *args, **kwargs):
            intensity = self.settings['intensity']
            calls.append(intensity)
            assert gate.wait(10), '测试推理等待超时'
            if intensity == .1:
                raise RuntimeError('受控渲染失败')
            result = rgba.copy()
            result[..., 0] = int(intensity * 200)
            return result
        def close(self, **kwargs):
            self.closed = True
    directory = Path(tempfile.mkdtemp(prefix='realtime-', dir=ROOT / 'logs'))
    picture = directory / 'input.png'
    pipeline.imwrite(str(picture), np.full((32, 32, 3), 80, np.uint8))
    with patch.dict(os.environ, DLSS5_DATA_ROOT=str(directory / 'data')), \
         patch('runtime_session.Live', Renderer), \
         patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]), \
         patch('gui.filedialog.askopenfilename', return_value=str(picture)):
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        def wait(predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while not predicate():
                root.update()
                assert not errors, errors
                if time.monotonic() > deadline:
                    raise AssertionError('自动渲染未达到预期状态：' + app._task_status)
                time.sleep(.01)
            root.update()
        def idle():
            wait(lambda: not app._busy and not (app.thread and app.thread.is_alive())
                 and app._live_debounce is None and not getattr(app, '_auto_pending', False))
        try:
            app.import_image()
            wait(lambda: app._confirmed_image is not None)
            idle()
            assert not hasattr(app, 'confirm_process_btn'), '确认渲染按钮没有移除'
            assert len(calls) == len(instances) == 1
            for value in (.2, .4, .6):
                app.layer_vars[0]['intensity'].set(value)
                root.update()
            idle()
            assert calls == [1., .6], calls
            assert len(instances) == 1 and not instances[0].closed
            before = app._confirmed_image.copy()
            gate.clear()
            app.layer_vars[0]['intensity'].set(.3)
            wait(lambda: len(calls) == 3)
            scales = []
            def collect(widget):
                if isinstance(widget, tk.Scale):
                    scales.append(widget)
                for child in widget.winfo_children():
                    collect(child)
            collect(app.pages['参数'])
            assert any(str(scale.cget('state')) == 'normal' for scale in scales), '预览时锁死滑块'
            app.layer_vars[0]['intensity'].set(.7)
            root.update()
            app.layer_vars[0]['intensity'].set(.8)
            root.update()
            assert np.array_equal(before, app._confirmed_image), '处理中提前替换结果'
            gate.set()
            idle()
            assert calls == [1., .6, .3, .8], calls
            assert app._confirmed_settings['intensity'] == .8
            assert len(instances) == 1
            app.v_mask_enabled.set(1)
            app.selection.replace(0)
            app.on_settings_change()
            idle()
            assert len(calls) == 4, '只改遮罩不应重新运行 DLSS'
            assert np.array_equal(app._confirmed_image[..., :3], app.image_bgr)
            app._require_confirmed_result()
            previous = app._confirmed_image.copy()
            app.layer_vars[0]['intensity'].set(.1)
            idle()
            assert app._last_task_failed and app._model_sessions.dlss is None
            assert np.array_equal(previous, app._confirmed_image)
            for _ in range(25):
                root.update()
                time.sleep(.01)
            assert calls.count(.1) == 1, '失败任务不应自动无限重试'
            app.layer_vars[0]['intensity'].set(.9)
            idle()
            assert not app._last_task_failed and len(instances) == 2
            print('通过：自动渲染、合并最新参数、模型复用、遮罩缓存、失败不循环重试及修改后恢复。')
        finally:
            gate.set()
            app._auto_enabled = False
            if app.thread:
                app.thread.join(10)
            app._close_live()
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()


if __name__ == '__main__':
    main()
