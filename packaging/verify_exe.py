"""Run a copied EXE with an empty working directory and no Python environment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
verification_dir = ROOT / 'tests' / 'exe-isolation'
verification_dir.mkdir(parents=True, exist_ok=True)
destination = verification_dir / 'DLSS5_Standalone.exe'
source = ROOT / 'output' / 'DLSS5_Standalone.exe'
shutil.copy2(source, destination)
result_dir = ROOT / 'logs' / 'exe-verification'
result_dir.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ)
for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'TORCH_HOME', 'DLSS5_LOG_DIR'):
    environment.pop(key, None)
environment['PATH'] = os.path.join(os.environ['SystemRoot'], 'System32')
environment['DLSS5_CACHE_ROOT'] = str(ROOT / 'logs' / 'exe-runtime-cache')
start = time.monotonic()
process = subprocess.Popen([str(destination), '--verify-package', str(result_dir)],
                           cwd=verification_dir, env=environment,
                           creationflags=subprocess.CREATE_NO_WINDOW)
print(f'Started isolated executable PID={process.pid}', flush=True)
try:
    return_code = process.wait(timeout=300)
except subprocess.TimeoutExpired:
    for child in psutil.Process(process.pid).children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    process.kill()
    process.wait()
    raise RuntimeError('Packaged executable verification exceeded 300 seconds')
elapsed = round(time.monotonic() - start, 3)
print(f'Executable exit={return_code}, elapsed={elapsed}s', flush=True)
report_path = result_dir / 'verification.json'
if return_code != 0 or not report_path.exists():
    log = result_dir / 'application.log'
    if log.exists():
        print(log.read_text(encoding='utf-8')[-10000:])
    raise SystemExit(return_code or 1)
result = json.loads(report_path.read_text(encoding='utf-8'))
result['total_seconds_including_extraction'] = elapsed
result['executable_size_bytes'] = source.stat().st_size
result['isolated_working_directory'] = str(verification_dir)
report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
if result['status'] != 'passed':
    raise SystemExit(1)
