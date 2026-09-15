"""Entry point for the full, self-contained Windows executable."""
import multiprocessing
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime


def main():
    multiprocessing.freeze_support()
    verification = '--verify-package' in sys.argv
    if verification:
        position = sys.argv.index('--verify-package')
        log_dir = Path(sys.argv[position + 1]).resolve()
    else:
        local_data = os.environ.get('LOCALAPPDATA', str(Path.home()))
        log_dir = Path(local_data) / 'DLSS5Standalone' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_LOG_DIR'] = str(log_dir)
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    os.environ['TORCH_HOME'] = str(bundle / 'torch_home')
    with (log_dir / 'application.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        print(f'[{datetime.now().isoformat(timespec="seconds")}] 启动 {sys.executable}')
        try:
            if verification:
                from packaged_selftest import verify
                verify(log_dir, bundle)
            else:
                import gui
                gui.main()
        except Exception:
            traceback.print_exc()
            if verification:
                raise SystemExit(1)
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('启动失败', f'请查看错误日志：\n{log_dir / "application.log"}')
            root.destroy()
            raise SystemExit(1)


if __name__ == '__main__':
    main()
