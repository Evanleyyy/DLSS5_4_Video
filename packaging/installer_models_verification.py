"""Real installer-model UI, isolated settings, tiny checked assets, no external network."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

def verify(directory):
    import tkinter as tk
    from PIL import ImageGrab
    import installer_model_ui
    import model_assets
    import sr_settings
    directory = Path(directory).resolve()
    source = directory / '安装包 中文 空格'
    installed = directory / 'app'
    source.mkdir(parents=True)
    payload = b'small verified model fixture'
    entry = {'path': 'seedvr2/model.safetensors', 'size': len(payload),
             'sha256': hashlib.sha256(payload).hexdigest()}
    errors, report = [], {}
    def wait(root, window):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            root.update()
            if window.result is not None and not window.busy:
                assert not errors, errors
                return
            time.sleep(.01)
        raise TimeoutError(window.status.get())
    def create():
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        return root, installer_model_ui.InstallerModelWindow(root, source, auto_close=False)
    def close(root):
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()
    with patch.dict(os.environ, DLSS5_DATA_ROOT=str(directory / 'data')), \
         patch.object(sr_settings, 'app_root', return_value=installed), \
         patch.object(model_assets, 'assets', return_value=[entry]), \
         patch.object(model_assets.urllib.request, 'urlopen', side_effect=AssertionError('不得自行联网')):
        # Missing models only prompt; the download API must remain untouched.
        root, window = create()
        try:
            with patch.object(model_assets, 'prepare') as download:
                wait(root, window)
                download.assert_not_called()
                assert window.result['missing'] == [entry['path']]
                assert str(window.download_button.cget('state')) == 'disabled'
                for width, height in [(780, 510), (620, 560)]:
                    root.geometry(f'{width}x{height}')
                    for _ in range(8):
                        root.update()
                        time.sleep(.02)
                    for widget in (window.choose_button, window.download_button, window.close_button):
                        assert widget.winfo_rootx() >= root.winfo_rootx()
                        assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
                    ImageGrab.grab(window=root.winfo_id()).save(directory / f'缺少模型-{width}.png')
                target = sr_settings.model_root(window.cfg) / entry['path']
                def requested_download(*args, **kwargs):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                    return {'downloaded': 1, 'reused': 0}
                download.side_effect = requested_download
                window.rows['seedvr2'].invoke()
                assert str(window.download_button.cget('state')) == 'normal'
                window.download_button.invoke()
                wait(root, window)
                download.assert_called_once()
                assert not window.result['missing']
                report['missing_prompts_without_network_and_download_requires_click'] = True
        finally:
            close(root)
        # The same file at the target is reused, with no rewrite or network.
        before = target.stat().st_mtime_ns
        root, window = create()
        try:
            wait(root, window)
            assert window.result['reused'] == 1 and target.stat().st_mtime_ns == before
            report['installed_models_are_skipped'] = True
        finally:
            close(root)
        # A fresh installation receives a copy; changing installer location is harmless.
        target.unlink()
        local = source / 'models' / entry['path']
        local.parent.mkdir(parents=True)
        local.write_bytes(payload)
        root, window = create()
        try:
            wait(root, window)
            assert window.result['copied'] == 1 and target.read_bytes() == local.read_bytes()
            assert sr_settings.load_preferences()['model_root'] == ''
            ImageGrab.grab(window=root.winfo_id()).save(directory / '自动安装本地模型.png')
            report['local_model_copied_automatically'] = True
        finally:
            close(root)
        with patch.object(installer_model_ui.tk, 'Tk', side_effect=AssertionError('静默安装不得弹窗')):
            assert installer_model_ui.run(source, silent=True) == 0
            report['silent_install_is_offline'] = True
    report.update(status='passed', directory=str(directory))
    (directory / 'installer-model-ui-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
