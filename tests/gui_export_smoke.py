import sys
import tkinter as tk
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
import gui
import pipeline
from export_verification import verify_export

folder = ROOT / 'logs/gui-export'
folder.mkdir(parents=True, exist_ok=True)
root = tk.Tk()
root.withdraw()
try:
    app = gui.App(root)
    original = pipeline.imread(str(ROOT / 'tests/本地验证/奇数尺寸图片.png'))
    processed = app._image_dlss(original)
    report = verify_export(app, folder, ROOT / 'tests/本地验证/测试片.mp4', original, processed, screenshots=True)
    (folder / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(report['status'], report['checks'])
finally:
    root.destroy()
