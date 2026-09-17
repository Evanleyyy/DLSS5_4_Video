"""Entry point for the full, self-contained Windows executable."""
import multiprocessing
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime


def main():
    multiprocessing.freeze_support()
    runtime_verification = '--verify-runtimes' in sys.argv
    sr_verification = '--verify-sr' in sys.argv
    pause_verification = '--verify-pause' in sys.argv
    playback_verification = '--verify-playback' in sys.argv
    export_cache_verification = '--verify-export-cache' in sys.argv
    preset_verification = '--verify-presets' in sys.argv
    model_verification = '--verify-model-assets' in sys.argv
    installer_verification = '--verify-installer-models' in sys.argv
    residency_verification = '--verify-residency' in sys.argv
    realtime_verification = '--verify-realtime' in sys.argv
    verification = realtime_verification or residency_verification or installer_verification or runtime_verification or '--verify-package' in sys.argv or sr_verification or pause_verification or playback_verification or export_cache_verification or preset_verification or model_verification
    if verification:
        position = sys.argv.index('--verify-realtime' if realtime_verification else '--verify-residency' if residency_verification else '--verify-installer-models' if installer_verification else '--verify-runtimes' if runtime_verification else '--verify-model-assets' if model_verification else '--verify-presets' if preset_verification else '--verify-export-cache' if export_cache_verification else '--verify-playback' if playback_verification else '--verify-pause' if pause_verification else '--verify-sr' if sr_verification else '--verify-package')
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
            install_source = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--install-models-from=')), None)
            preparation = next((arg for arg in sys.argv if arg.startswith('--prepare-models=')), None)
            if install_source is not None:
                from installer_model_ui import run
                requested = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--models=')), '')
                raise SystemExit(run(install_source, [item for item in requested.split(',') if item], '--models-silent' in sys.argv))
            elif preparation:
                from model_prepare_ui import run
                raise SystemExit(run(preparation.split('=', 1)[1].split(',')))
            elif realtime_verification:
                from realtime_verification import verify
                verify(log_dir)
            elif residency_verification:
                from residency_verification import verify
                verify(log_dir)
            elif installer_verification:
                from installer_models_verification import verify
                verify(log_dir)
            elif runtime_verification:
                from runtime_verification import verify
                verify(log_dir)
            elif model_verification:
                from model_assets_verification import verify
                verify(log_dir)
            elif preset_verification:
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
