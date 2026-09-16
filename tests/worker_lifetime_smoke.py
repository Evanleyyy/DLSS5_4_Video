"""Kill a real supervisor and assert its owned inference child exits as well."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
from process_lifetime import owned_process


def owner(folder):
    code = 'import time; time.sleep(120)'
    with owned_process([sys.executable, '-c', code], creationflags=subprocess.CREATE_NO_WINDOW) as worker:
        (folder / 'child.json').write_text(json.dumps({'pid': worker.pid}), encoding='utf-8')
        time.sleep(120)


def main():
    folder = Path(tempfile.mkdtemp(prefix='worker-lifetime-', dir=ROOT / 'logs'))
    child = None
    supervisor = subprocess.Popen([sys.executable, __file__, '--owner', str(folder)],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 10
        marker = folder / 'child.json'
        while not marker.exists():
            assert supervisor.poll() is None, '任务宿主提前退出'
            assert time.monotonic() < deadline, '任务宿主启动超时'
            time.sleep(.02)
        child = psutil.Process(json.loads(marker.read_text())['pid'])
        assert child.is_running()
        supervisor.kill()  # Deliberately bypass Python finally blocks.
        supervisor.wait(timeout=10)
        child.wait(timeout=10)
        assert not child.is_running()
        result = {'status': 'passed', 'owner_crash_kills_child': True}
        (folder / 'verification.json').write_text(json.dumps(result), encoding='utf-8')
        print(result, flush=True)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=10)
        if child is not None and child.is_running():
            child.kill()
            child.wait(timeout=10)


if __name__ == '__main__':
    if '--owner' in sys.argv:
        owner(Path(sys.argv[-1]))
    else:
        main()
