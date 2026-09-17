import sys
import time
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import gui
import pipeline

root = tk.Tk()
root.withdraw()
app = gui.App(root)
errors = []
root.report_callback_exception = lambda *info: errors.append(repr(info))
folder = ROOT / 'tests' / '本地验证'
source = folder / '奇数尺寸图片.png'
image = np.zeros((239, 317, 3), np.uint8)
image[:, :158] = [25, 120, 200]
image[:, 158:] = [200, 100, 25]
pipeline.imwrite(str(source), image)

def wait_worker():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        root.update()
        if (app.thread is None or not app.thread.is_alive()) and app._ui_events.empty():
            break
        time.sleep(0.01)
    else:
        raise AssertionError('GUI worker timed out')
    assert not errors, errors

with patch.object(gui.filedialog, 'askopenfilename', return_value=str(source)):
    app.import_image()
assert app.image_dlss is None
app.confirm_processing()
wait_worker()
assert app.image_dlss is not None and app.image_dlss.shape == image.shape
app.v_intensity.set(0.5)
app._refresh_dlss()
app.confirm_processing()
wait_worker()
assert app.image_dlss.shape == image.shape
with patch.object(gui.filedialog, 'askopenfilename', return_value=str(folder / '测试片.mp4')):
    app.import_video()
app.run_worker('dlss')
wait_worker()
assert '出错' not in app.status.cget('text'), app.status.cget('text')
app.v_export_format.set('视频')
with patch.object(gui.filedialog, 'askdirectory', return_value=str(folder)):
    app.export()
wait_worker()
assert '出错' not in app.status.cget('text'), app.status.cget('text')
assert app.last_export and Path(app.last_export['outputs'][0]).is_file()
app._close_live()
root.destroy()
print('GUI startup, image, settings refresh, video processing and export: PASS')
