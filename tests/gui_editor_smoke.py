import sys
from pathlib import Path
import tkinter as tk
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
import gui
from editor_verification import verify_editor

root = tk.Tk()
try:
    app = gui.App(root)
    original = np.zeros((480, 640, 3), np.uint8)
    yy, xx = np.indices(original.shape[:2])
    original[:, :, 0] = (xx * 255 // 640).astype(np.uint8)
    original[:, :, 1] = (yy * 255 // 480).astype(np.uint8)
    original[:, :, 2] = 100
    processed = 255 - original
    print(verify_editor(app, ROOT / 'logs/gui-editor', original, processed, screenshots=True))
finally:
    root.destroy()
