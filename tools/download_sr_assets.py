"""Check local assets first; fetch only missing or invalid official model files."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import model_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', action='append', choices=['pisa', 'seedvr2', 'vosr', 'guidance'])
    parser.add_argument('--model-root', default='')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--verify', action='store_true', help='重新计算全部文件哈希')
    args = parser.parse_args()
    result = model_assets.prepare(args.engine or ['pisa', 'seedvr2', 'vosr', 'guidance'],
        {'model_root': args.model_root}, lambda i, total, stage: print(f'{i}/{total} {stage}', flush=True),
        check_only=args.check_only, force_verify=args.verify)
    print(json.dumps({key: value for key, value in result.items() if key != 'paths'}, ensure_ascii=False))
    return 2 if result['missing'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
