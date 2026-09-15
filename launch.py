"""Local GUI entry point with persistent diagnostics."""
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT / 'source' / 'dlss5standaloneV2')
sys.path.insert(0, str(Path.cwd()))
os.environ['PATH'] = str(ROOT / 'runtime') + os.pathsep + os.environ.get('PATH', '')
os.environ['TORCH_HOME'] = str(Path.cwd() / 'torch_home')
logs = ROOT / 'logs'
logs.mkdir(exist_ok=True)
with (logs / 'gui.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = sys.stderr = log
    try:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] GUI 启动: {sys.executable}")
        import gui
        gui.main()
    except Exception:
        traceback.print_exc()
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('启动失败', '请查看项目 logs/gui.log 中的错误信息。')
        root.destroy()
        raise
