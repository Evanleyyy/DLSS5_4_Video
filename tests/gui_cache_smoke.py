from pathlib import Path
import sys
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
import gui
from cache_verification import verify_cache

root = tk.Tk()
try:
    app = gui.App(root)
    print(verify_cache(app, ROOT / 'logs/gui-cache', screenshots=True))
finally:
    root.destroy()
