"""Validate a keyboard update at an explicitly chosen existing installation."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

from verify_pause_update import registry

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'#define AppVersion "([^"]+)"',
                    (ROOT / 'packaging/DLSS5_Installer.iss').read_text(encoding='utf-8')).group(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True, type=Path, help='已有软件安装目录')
    parser.add_argument('--check', choices=('playback', 'export-cache', 'presets'), default='playback')
    args = parser.parse_args()
    target = args.target.resolve(strict=True)
    LOGS = ROOT / ('logs/' + args.check + '-update-' + VERSION)
    LOGS.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'target': str(target)}
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'DLSS5_DATA_ROOT', 'DLSS5_LAUNCHER_PATH', 'DLSS5_RUNTIME_ID'):
        environment.pop(name, None)
    environment['PATH'] = os.environ['SystemRoot'] + ';' + str(Path(os.environ['SystemRoot']) / 'System32')
    environment['PYTHONNOUSERSITE'] = '1'
    try:
        before = registry()
        before_uninstallers = {p.name for p in target.glob('unins*')}
        assert any(name.endswith('.exe') for name in before_uninstallers)
        preferences = target / 'data/super_resolution.json'
        saved = preferences.read_bytes() if preferences.exists() else None
        presets = target / 'data/parameter_presets.json'
        saved_presets = presets.read_bytes() if presets.exists() else None
        started = time.monotonic()
        subprocess.run([str(ROOT / ('output/DLSS5_Update_' + VERSION + '.exe')), '/VERYSILENT',
            '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/DIR=' + str(target), '/LOG=' + str(LOGS / 'install.log')],
            cwd=target, env=environment, creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=120)
        after = registry()
        assert after['DisplayVersion'] == VERSION
        assert Path(after['InstallLocation']).resolve() == target
        uninstaller = Path(after['UninstallString'].strip('"')).resolve(strict=True)
        assert uninstaller.parent == target and uninstaller.name in before_uninstallers
        if Path(before['InstallLocation']).resolve() == target:
            assert before['UninstallString'] == after['UninstallString']
        assert before_uninstallers == {p.name for p in target.glob('unins*')}
        assert saved == (preferences.read_bytes() if preferences.exists() else None)
        assert saved_presets == (presets.read_bytes() if presets.exists() else None)
        result['update_seconds'] = round(time.monotonic() - started, 3)
        subprocess.run([str(target / 'DLSS5_App.exe'), '--verify-' + args.check, str(LOGS / 'app')],
            cwd=target, env=environment, creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=120)
        report = json.loads((LOGS / 'app/verification.json').read_text(encoding='utf-8'))
        assert report['status'] == 'passed'
        result.update(status='passed', installed_checks=report, same_uninstaller_files_and_preferences=True,
                      registration_matches_target=True)
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        (LOGS / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result, flush=True)


if __name__ == '__main__':
    main()
