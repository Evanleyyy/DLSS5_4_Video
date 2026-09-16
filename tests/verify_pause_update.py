"""Check the small updater against the existing offline installation."""
import json
import os
from pathlib import Path
import subprocess
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'runtime/installed-app'
LOGS = ROOT / 'logs/pause-update'
INSTALLER = ROOT / 'output/DLSS5_Update_0.3.1.exe'


def registry():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7BE891EC-ED7C-4745-9DFE-4523CDF01B9E}_is1',
            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return {name: winreg.QueryValueEx(key, name)[0] for name in ('DisplayVersion', 'UninstallString', 'InstallLocation')}


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'stages': {}}
    env = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'DLSS5_DATA_ROOT', 'DLSS5_LAUNCHER_PATH', 'DLSS5_RUNTIME_ID'):
        env.pop(name, None)
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') + ';' + os.environ['SystemRoot']
    env['PYTHONNOUSERSITE'] = '1'
    def run(name, command, expected=0):
        started = time.monotonic()
        process = subprocess.run(command, cwd=TARGET, env=env, creationflags=subprocess.CREATE_NO_WINDOW, timeout=900)
        assert process.returncode == expected, (name, process.returncode)
        result['stages'][name] = {'passed': True, 'seconds': round(time.monotonic() - started, 3)}
        print(name, result['stages'][name], flush=True)
    try:
        before = registry()
        uninstall_files = {p.name for p in TARGET.glob('unins*')}
        note = TARGET / 'data/pause-update-check.json'
        note.parent.mkdir(parents=True, exist_ok=True)
        if not note.exists():
            note.write_text('{"验证": "更新保留用户数据"}', encoding='utf-8')
        preserved = [note]
        preferences = TARGET / 'data/super_resolution.json'
        if preferences.exists():
            preserved.append(preferences)
        before_bytes = [p.read_bytes() for p in preserved]
        asset = TARGET / 'runtime/sr-models/seedvr2/seedvr2_ema_3b_fp8_e4m3fn.safetensors'
        asset_before = (asset.stat().st_size, asset.stat().st_mtime_ns)
        flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-']
        empty = LOGS / 'empty-target'
        run('reject_missing_base_install', [str(INSTALLER), *flags, '/DIR=' + str(empty),
             '/LOG=' + str(LOGS / 'rejected.log')], expected=7)
        assert not (empty / 'DLSS5_App.exe').exists()
        assert registry() == before
        run('update', [str(INSTALLER), *flags, '/DIR=' + str(TARGET), '/LOG=' + str(LOGS / 'install.log')])
        after = registry()
        assert after['DisplayVersion'] == '0.3.1'
        assert after['UninstallString'] == before['UninstallString']
        assert {p.name for p in TARGET.glob('unins*')} == uninstall_files
        assert [p.read_bytes() for p in preserved] == before_bytes
        assert (asset.stat().st_size, asset.stat().st_mtime_ns) == asset_before
        result['same_uninstaller_and_data_preserved'] = True
        run('installed_pause', [str(TARGET / 'DLSS5_App.exe'), '--verify-pause', str(LOGS / 'pause')])
        assert json.loads((LOGS / 'pause/verification.json').read_text(encoding='utf-8'))['status'] == 'passed'
        run('installed_regression', [str(TARGET / 'DLSS5_App.exe'), '--verify-package', str(LOGS / 'regression')])
        assert json.loads((LOGS / 'regression/verification.json').read_text(encoding='utf-8'))['status'] == 'passed'
        result['status'] = 'passed'
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        (LOGS / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result, flush=True)


if __name__ == '__main__':
    main()
