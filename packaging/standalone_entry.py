"""Entry point for the full, self-contained Windows executable."""
import multiprocessing
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime


def main():
    multiprocessing.freeze_support()
    sr_verification = '--verify-sr' in sys.argv
    pause_verification = '--verify-pause' in sys.argv
    playback_verification = '--verify-playback' in sys.argv
    export_cache_verification = '--verify-export-cache' in sys.argv
    preset_verification = '--verify-presets' in sys.argv
    verification = '--verify-package' in sys.argv or sr_verification or pause_verification or playback_verification or export_cache_verification or preset_verification
    if verification:
        position = sys.argv.index('--verify-presets' if preset_verification else '--verify-export-cache' if export_cache_verification else '--verify-playback' if playback_verification else '--verify-pause' if pause_verification else '--verify-sr' if sr_verification else '--verify-package')
        log_dir = Path(sys.argv[position + 1]).resolve()
        os.environ['DLSS5_DATA_ROOT'] = str(log_dir / 'app-data')
    else:
        if (Path(sys.executable).parent / 'runtime').is_dir():
            os.environ.setdefault('DLSS5_DATA_ROOT', str(Path(sys.executable).parent / 'data'))
            log_dir = Path(os.environ['DLSS5_DATA_ROOT']) / 'logs'
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
            if preset_verification:
                from preset_verification import verify
                verify(log_dir)
            elif export_cache_verification:
                from export_cache_verification import verify
                verify(log_dir)
            elif playback_verification:
                from playback_verification import verify
                verify(log_dir)
            elif pause_verification:
                from pause_verification import verify
                verify(log_dir)
            elif sr_verification:
                from sr_verification import verify
                verify(log_dir)
            elif verification:
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
