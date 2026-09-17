"""Run the same offline installer UI checks used by the frozen application."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
from installer_models_verification import verify

if __name__ == '__main__':
    verify(ROOT / 'logs' / ('installer-model-ui-' + str(time.time_ns())))
