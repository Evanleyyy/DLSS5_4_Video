"""Inspect installed models or render three test frames in an isolated worker."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import dlss_runtime
    import runtime_session
    parser = argparse.ArgumentParser(description='DLSS 离线模型检查')
    parser.add_argument('--version', choices=list(dlss_runtime.LABELS), default='auto')
    parser.add_argument('--render', action='store_true', help='实际处理三帧并检查输出')
    parser.add_argument('--all', action='store_true', help='检查所有已提供版本')
    args = parser.parse_args()
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    os.environ['DLSS5_LOG_DIR'] = str(logs)
    versions = ['bundled', '310.8.SF-v2', '310.8.SF'] if args.all else [args.version]
    results = []
    for version in versions:
        try:
            result = runtime_session.selftest(version) if args.render else dlss_runtime.resolve(version)
            results.append({'selected': version, 'passed': True, 'result': result})
        except Exception as error:
            results.append({'selected': version, 'passed': False, 'error': str(error)})
        print(json.dumps(results[-1], ensure_ascii=False, indent=2), flush=True)
    report = {'gpu': dlss_runtime.gpu_info(), 'rendered': args.render, 'results': results}
    (logs / ('runtime-render.json' if args.render else 'runtime-inventory.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(item['passed'] for item in results) else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
