import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
os.environ['DLSS5_DATA_ROOT'] = str(ROOT / 'logs/gui-sr/app-data')

if __name__ == '__main__':
    from sr_verification import verify
    print(verify(ROOT / 'logs/gui-sr'))
