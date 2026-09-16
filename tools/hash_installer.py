"""Create a checksum list for the complete multi-volume Windows installer."""
import hashlib
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--prefix', default='DLSS5_Setup_0.3.0')
prefix = parser.parse_args().prefix
files = sorted((root / 'output').glob(prefix + '*'))
rows = []
for path in files:
    if path.suffix not in ('.exe', '.bin'):
        continue
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    rows.append(digest + '  ' + path.name)
    print(path.name, flush=True)
if not rows:
    raise RuntimeError('没有找到对应安装包：' + prefix)
(root / 'output' / (prefix + '_SHA256.txt')).write_text('\n'.join(rows) + '\n', encoding='ascii')
print('校验清单已生成', flush=True)
