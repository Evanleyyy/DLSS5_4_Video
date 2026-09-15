"""Minimized reproduction of native session shutdown, with a hard timeout."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if '--child' in sys.argv:
    import faulthandler
    import numpy as np
    faulthandler.dump_traceback_later(8, exit=False)
    sys.path.insert(0, str(ROOT / ('original-source' if '--original' in sys.argv else 'source/dlss5standaloneV2')))
    import dlss_engine
    dlss_engine.HOST_DLL = str(ROOT / 'source/dlss5standaloneV2/dlssnr_host.dll')
    dlss_engine.DLSSNR_DLL = str(ROOT / 'source/dlss5standaloneV2/nvngx_dlssnr.dll')
    dlss_engine.LOG_PATH = str(ROOT / 'logs/native-lifecycle.log')
    print('INIT', flush=True)
    session = dlss_engine.Live(320, 240, {'guidance_mode': 0})
    print('PROCESS', flush=True)
    rgba = np.zeros((240, 320, 4), np.uint8)
    rgba[..., 3] = 255
    if '--skip-frame' not in sys.argv:
        session.process(rgba, np.zeros((240, 320, 2), np.float32), np.zeros((240, 320), np.float32), True)
    if '--delay' in sys.argv:
        import time
        time.sleep(2)
    print('CLOSE', flush=True)
    session.close()
    print('CLOSED', flush=True)
else:
    try:
        r = subprocess.run([sys.executable, '-u', __file__, '--child', *sys.argv[1:]],
                           capture_output=True, text=True, timeout=15)
        print(r.stdout, r.stderr, flush=True)
        sys.exit(r.returncode)
    except subprocess.TimeoutExpired as e:
        print('TIMEOUT: native lifecycle exceeded 15 seconds', flush=True)
        print(e.stdout, e.stderr, flush=True)
        sys.exit(1)
