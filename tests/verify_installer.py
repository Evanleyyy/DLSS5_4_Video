"""Install and upgrade the release, then run its self-contained offline checks."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'output/DLSS5_Setup_0.3.0.exe'
TARGET = ROOT / 'runtime/installed-app'
LOGS = ROOT / 'logs/installed-verification'


def verify_models(environment, results):
    import cv2
    import numpy as np
    runtime = TARGET / 'runtime'
    y, x = np.indices((121, 159))
    rgb = np.stack([(x * 3 + y) % 256, (y * 5) % 256, (x * 2) % 256], axis=-1).astype(np.uint8)
    alpha = np.where(x < 50, 80, 255).astype(np.uint8)
    fixture = LOGS / '模型验证原图.png'
    cv2.imencode('.png', np.dstack([rgb, alpha]))[1].tofile(str(fixture))
    for engine in ('pisa', 'seedvr2', 'vosr'):
        directory = LOGS / engine
        directory.mkdir(exist_ok=True)
        cfg = dict(engine=engine, scale=2, seed=42, tile=512, pisa_pixel=1.,
                   pisa_semantic=1., batch=5, blocks=24, color='wavelet', model_root='')
        job = dict(kind='images', inputs=[str(fixture)], output=str(directory / 'output'),
                   settings=dict(super_resolution=cfg, overall_weight=1.),
                   runtime=str(runtime), model_root=str(runtime / 'sr-models'),
                   app_source=str(runtime / 'sr-worker'))
        request = directory / 'request.json'
        request.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        env = environment.copy()
        packages = [runtime / 'sr-packages']
        if engine == 'pisa':
            packages.insert(0, runtime / 'pisa-packages')
        env['PYTHONPATH'] = os.pathsep.join(map(str, packages))
        env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DIFFUSERS_OFFLINE='1',
                   HF_HUB_DISABLE_TELEMETRY='1', TORCHDYNAMO_DISABLE='1',
                   HF_HOME=str(directory / 'hf'), TORCH_HOME=str(directory / 'torch'),
                   TEMP=str(directory), TMP=str(directory), MPLCONFIGDIR=str(directory / 'mpl'))
        started = time.monotonic()
        with (directory / 'inference.log').open('w', encoding='utf-8') as log:
            subprocess.run([str(runtime / 'python/python.exe'), '-u', str(runtime / 'sr-worker/sr_worker.py'), str(request)],
                           env=env, cwd=directory, stdout=log, stderr=log, check=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=900)
        report = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
        output = cv2.imdecode(np.fromfile(report['outputs'][0], np.uint8), cv2.IMREAD_UNCHANGED)
        assert report['offline'] and output.shape == (242, 318, 4)
        assert np.array_equal(output[..., 3], cv2.resize(alpha, (318, 242), interpolation=cv2.INTER_LINEAR))
        assert np.any(output[..., :3] != cv2.resize(rgb, (318, 242), interpolation=cv2.INTER_CUBIC))
        results['stages']['installed_' + engine] = dict(passed=True, seconds=round(time.monotonic() - started, 3), offline=True)
        print(engine, results['stages']['installed_' + engine], flush=True)


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    results = {'status': 'running', 'installed_path': str(TARGET), 'stages': {}}
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'DLSS5_DATA_ROOT', 'DLSS5_LAUNCHER_PATH', 'DLSS5_RUNTIME_ID'):
        environment.pop(key, None)
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') + ';' + os.environ['SystemRoot']
    environment['PYTHONNOUSERSITE'] = '1'
    def stage(name, command, timeout=1800):
        started = time.monotonic()
        subprocess.run(command, env=environment, cwd=TARGET if TARGET.exists() else ROOT,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=timeout)
        results['stages'][name] = {'passed': True, 'seconds': round(time.monotonic() - started, 3)}
        print(name, results['stages'][name], flush=True)
    try:
        args = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TYPE=full',
                '/TASKS=desktopicon', '/DIR=' + str(TARGET)]
        stage('install', [str(INSTALLER), *args, '/LOG=' + str(LOGS / 'install.log')])
        assert (TARGET / 'DLSS5_App.exe').is_file()
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7BE891EC-ED7C-4745-9DFE-4523CDF01B9E}_is1',
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            uninstaller = Path(winreg.QueryValueEx(key, 'UninstallString')[0].strip('"'))
        assert uninstaller.parent == TARGET and uninstaller.is_file()
        results['registered_uninstaller'] = str(uninstaller)
        assert not list((TARGET / 'runtime/sr-engines').glob('*/.git'))
        stage('installed_gui_exports', [str(TARGET / 'DLSS5_App.exe'), '--verify-package', str(LOGS / 'app')])
        report = json.loads((LOGS / 'app/verification.json').read_text(encoding='utf-8'))
        assert report['status'] == 'passed'
        verify_models(environment, results)
        note = TARGET / 'data/user-preserved.txt'
        note.parent.mkdir(exist_ok=True)
        note.write_text('升级必须保留的用户数据', encoding='utf-8')
        model_note = TARGET / 'runtime/sr-models/user-preserved.txt'
        model_note.write_text('用户自行导入的模型说明', encoding='utf-8')
        stage('upgrade', [str(INSTALLER), *args, '/LOG=' + str(LOGS / 'upgrade.log')])
        assert note.read_text(encoding='utf-8') == '升级必须保留的用户数据'
        assert model_note.read_text(encoding='utf-8') == '用户自行导入的模型说明'
        script = (ROOT / 'packaging/DLSS5_Installer.iss').read_text(encoding='utf-8')
        model_rules = [line for line in script.splitlines() if line.startswith('Source:') and '\\sr-models\\' in line]
        assert len(model_rules) == 3 and all('uninsneveruninstall' in line for line in model_rules)
        assert '[UninstallDelete]' not in script and 'DestDir: "{app}\\data"' not in script
        results['uninstall_data_retention_rules_checked'] = True
        results['uninstall_executed'] = False
        results['status'] = 'passed'
    except Exception:
        import traceback
        results.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        (LOGS / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(results, flush=True)


if __name__ == '__main__':
    main()
