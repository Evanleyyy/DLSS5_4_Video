"""Exercise real official small-file downloads and both model preparation UIs."""
import json
import os
from pathlib import Path
import time


def verify(directory):
    import tkinter as tk
    from tkinter import ttk
    from unittest.mock import patch
    from PIL import ImageGrab
    import gui
    import model_assets
    import model_prepare_ui
    import sr_settings
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_DATA_ROOT'] = str(directory / 'data')
    cfg = sr_settings.normalize({'engine': 'vosr', 'model_root': str(directory / 'models')})
    sr_settings.save_preferences(cfg)
    entry = next(item for item in model_assets.assets(['vosr']) if item['path'] == 'vosr/VOSR2/args.json')
    result = {'status': 'running', 'source': entry['url']}
    root = None
    errors = []

    def widgets(widget):
        yield widget
        for child in widget.winfo_children():
            yield from widgets(child)

    def pump():
        root.update()
        time.sleep(.01)

    try:
        # The same downloader used for multi-GB weights, with one official config.
        with patch.object(model_assets, 'assets', return_value=[entry]):
            fresh = model_assets.prepare(['vosr'], cfg)
            assert fresh['downloaded'] == 1
            with patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('完整模型不应联网')):
                assert model_assets.prepare(['vosr'], cfg)['reused'] == 1
            result['official_download_and_offline_reuse'] = True
            root = tk.Tk()
            root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
            app = gui.App(root)
            app.tabs.select(app.pages['超分'])
            pump()
            button = next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == '检查并补全所选模型')
            button.invoke()
            deadline = time.monotonic() + 30
            while app._busy and time.monotonic() < deadline:
                pump()
            assert not app._busy
            assert '模型准备完成' in app.log.get('1.0', 'end')
            for width, height in ((1280, 800), (760, 640)):
                root.geometry(f'{width}x{height}')
                for _ in range(15):
                    pump()
                ImageGrab.grab(window=root.winfo_id()).save(directory / f'models-{width}.png')
            app._closing = True
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()
            root = None
            result['application_prepare_button'] = True

            create_root = tk.Tk
            def setup_root():
                nonlocal root
                root = create_root()
                root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
                deadline = time.monotonic() + 15
                def run_setup():
                    button = next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == '检查并准备模型')
                    button.invoke()
                    root.after(100, check_setup)
                def check_setup():
                    finished = [w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget('text') == '完成']
                    if finished:
                        ImageGrab.grab(window=root.winfo_id()).save(directory / 'setup-models.png')
                        finished[0].invoke()
                    elif time.monotonic() > deadline:
                        errors.append('安装模型准备窗口超时')
                        root.destroy()
                    else:
                        root.after(100, check_setup)
                root.after(150, run_setup)
                return root
            with patch.object(model_prepare_ui.tk, 'Tk', side_effect=setup_root):
                assert model_prepare_ui.run(['vosr']) == 0
            root = None
            result['setup_prepare_window'] = True
        assert not errors, errors
        result['status'] = 'passed'
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
