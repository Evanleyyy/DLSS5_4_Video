from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]

if __name__ == '__main__':
    from pause_verification import verify
    print(verify(ROOT / 'logs/gui-pause'), flush=True)
