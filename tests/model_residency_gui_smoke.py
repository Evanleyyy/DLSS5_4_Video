"""Exercise actual confirmation buttons while counting native model constructions."""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import tkinter as tk
    import numpy as np
    import gui
    import pipeline
    sessions = []
    class Renderer:
        def __init__(self, width, height, settings):
            self.settings, self.closed = dict(settings), False
            self.resets = []
            sessions.append(self)
        def update(self, settings):
            self.settings.update(settings)
        def process(self, rgba, motion, depth, reset=False):
            assert not self.closed
            self.resets.append(reset)
            result = rgba.copy()
            result[..., 0] = int(self.settings['intensity'] * 200)
            return result
        def close(self, **kwargs):
            self.closed = True
    folder = Path(tempfile.mkdtemp(prefix='resident-gui-', dir=ROOT / 'logs'))
    picture = folder / '图片.png'
    pipeline.imwrite(str(picture), np.full((32, 32, 3), 80, np.uint8))
    errors = []
    with patch.dict(os.environ, DLSS5_DATA_ROOT=str(folder / 'data')), \
         patch('runtime_session.Live', Renderer), \
         patch('dlss_layers.dlss_runtime.fingerprint', return_value=[]), \
         patch('gui.filedialog.askopenfilename', return_value=str(picture)):
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        def wait():
            until = time.monotonic() + 10
            while time.monotonic() < until:
                root.update()
                if (not app._busy and not (app.thread and app.thread.is_alive())
                        and app._live_debounce is None and not app._auto_pending):
                    assert not errors, errors
                    assert not app._last_task_failed
                    return
                time.sleep(.01)
            raise TimeoutError('确认处理没有结束')
        try:
            app.import_image()
            wait()
            assert len(sessions) == 1
            assert not sessions[0].closed, '单张处理完成后立即关闭了模型'
            before = app._confirmed_image.copy()
            app.layer_vars[0]['intensity'].set(.3)
            root.update()
            wait()
            assert len(sessions) == 1, '同一模型修改参数后重复加载'
            assert not np.array_equal(before, app._confirmed_image), '新参数没有生效'
            assert sessions[0].resets == [True, True], '独立任务必须重置时序历史'
            app._close_live()
            assert sessions[0].closed, '显式关闭未释放模型'
            print('通过：同一模型只创建一次，新参数生效，任务间重置历史，关闭时释放。')
        finally:
            app._close_live()
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()


if __name__ == '__main__':
    main()
