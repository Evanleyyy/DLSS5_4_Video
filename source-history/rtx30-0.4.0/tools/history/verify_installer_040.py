"""Validate the delivered installer in a separate installation; preserve the original."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import winreg

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT.parent / 'DLSS5OfflineRTX30'
INSTALLER = ROOT / 'output/DLSS5_Setup_0.4.0.exe'
LOGS = ROOT / 'logs/installer-0.4.0-verification'
ORIGINAL_ID = '{7BE891EC-ED7C-4745-9DFE-4523CDF01B9E}_is1'
RTX30_ID = '{FB3F634C-64AE-4E83-A12F-93008D65CC80}_is1'


def registry(identifier):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + identifier,
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return {name: winreg.QueryValueEx(key, name)[0] for name in
                    ('DisplayName', 'DisplayVersion', 'InstallLocation', 'UninstallString')}
    except FileNotFoundError:
        return None


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'installer': str(INSTALLER), 'target': str(TARGET), 'stages': {}}
    before = registry(ORIGINAL_ID)
    assert before, '原版安装登记缺失，需先核对'
    assert registry(RTX30_ID) is None, '30 系已有安装登记，避免覆盖现有安装'
    assert not TARGET.exists(), '目标目录已存在，避免覆盖现有文件'
    original_exe = Path(before['InstallLocation']) / 'DLSS5_App.exe'
    original_hash = digest(original_exe)
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'DLSS5_DATA_ROOT', 'DLSS5_LOG_DIR',
                'DLSS5_LAUNCHER_PATH', 'DLSS5_RUNTIME_ID', 'TORCH_HOME'):
        environment.pop(key, None)
    environment.update(PATH=os.environ['SystemRoot'] + ';' + os.path.join(os.environ['SystemRoot'], 'System32'),
                       PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DIFFUSERS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1')
    common = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TYPE=full',
              '/COMPONENTS=app', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS']
    def save():
        (LOGS / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    def run(name, command, expected=0, timeout=600):
        start = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
        assert completed.returncode == expected, (name, completed.returncode, expected)
        result['stages'][name] = {'passed': True, 'seconds': round(time.monotonic()-start, 3)}
        save()
        print(name, result['stages'][name], flush=True)
    try:
        fake = ROOT / 'build/installer-original-protection-check'
        fake.mkdir(parents=True, exist_ok=False)
        marker = fake / 'DLSS5_App.exe'
        marker.write_bytes(b'ORIGINAL-PROTECTION-CHECK')
        run('refuses_original_directory', [str(INSTALLER), *common, '/TASKS=', '/NOICONS',
            '/DIR=' + str(fake), '/LOG=' + str(LOGS/'refuse-original.log')], expected=7)
        assert marker.read_bytes() == b'ORIGINAL-PROTECTION-CHECK'
        assert len(list(fake.iterdir())) == 1
        assert registry(RTX30_ID) is None
        run('install', [str(INSTALLER), *common, '/TASKS=desktopicon', '/DIR=' + str(TARGET),
                       '/LOG=' + str(LOGS/'install.log')])
        registered = registry(RTX30_ID)
        assert registered['DisplayVersion'] == '0.4.0'
        assert Path(registered['InstallLocation']).resolve() == TARGET.resolve()
        assert Path(registered['UninstallString'].strip('"')).is_file()
        result['registration'] = registered
        for version in ('310.8.SF-v2', '310.8.SF'):
            relative = Path('runtime/dlssnr') / version / 'nvngx_dlssnr.dll'
            assert digest(TARGET / relative) == digest(ROOT / relative)
        assert digest(TARGET/'_internal/nvngx_dlssnr.dll') == digest(ROOT/'source/dlss5standaloneV2/nvngx_dlssnr.dll')
        assert not (TARGET/'runtime/sr-models').exists(), '标准安装不应下载其他模型'
        assert not list((TARGET/'runtime/sr-engines').glob('*/.git'))
        result['three_runtime_hashes_match'] = True
        run('installed_runtimes', [str(TARGET/'DLSS5_App.exe'), '--verify-runtimes', str(LOGS/'runtimes')], timeout=600)
        runtime_report = json.loads((LOGS/'runtimes/verification.json').read_text(encoding='utf-8'))
        assert runtime_report['status'] == 'passed' and runtime_report['frozen']
        result['runtime_checks'] = runtime_report
        run('installed_presets', [str(TARGET/'DLSS5_App.exe'), '--verify-presets', str(LOGS/'presets')], timeout=180)
        assert json.loads((LOGS/'presets/verification.json').read_text(encoding='utf-8'))['status'] == 'passed'
        assert registry(ORIGINAL_ID) == before and digest(original_exe) == original_hash
        result['original_installation_preserved'] = True
        result['no_optional_models_downloaded'] = not (TARGET/'runtime/sr-models').exists()
        result['status'] = 'passed'
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        save()
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()