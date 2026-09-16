"""Record exact upstream revisions, downloaded assets and runtime dependencies."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
manifest = {'application_version': '0.3.0', 'engines': {}, 'models': []}
for key in ('pisa', 'seedvr2', 'vosr'):
    repo = ROOT / 'runtime/sr-engines' / key
    manifest['engines'][key] = {
        'remote': subprocess.check_output(['git', '-C', str(repo), 'remote', 'get-url', 'origin'], text=True).strip(),
        'commit': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()}
for path in sorted((ROOT / 'runtime/sr-models').rglob('*')):
    if not path.is_file() or path.suffix in ('.complete', '.pyc') or '__pycache__' in path.parts or 'validation' in path.name:
        continue
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    manifest['models'].append({'path': path.relative_to(ROOT / 'runtime/sr-models').as_posix(),
                               'size': path.stat().st_size, 'sha256': digest.hexdigest()})
for name, repo in [('vosr', 'CSWRY/VOSR'), ('seedvr2', 'numz/SeedVR2_comfyUI'), ('sd21', 'Manojb/stable-diffusion-2-1-base')]:
    metadata = json.loads((ROOT / 'downloads' / (name + '-files.json')).read_text())
    manifest.setdefault('weight_repositories', {})[repo] = metadata['sha']
manifest['pisa_weights'] = 'https://drive.google.com/file/d/1PWFM5zUd6lNMOyKX64anvUXhKEOCrf8L/view'
(ROOT / 'packaging/sr-assets-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
for folder in ('sr-packages', 'pisa-packages'):
    rows = subprocess.check_output(['uv', '--cache-dir', str(ROOT / 'downloads/uv-cache'), 'pip', 'freeze', '--python', str(ROOT / 'runtime/python/python.exe'),
                                    '--path', str(ROOT / 'runtime' / folder)], text=True)
    (ROOT / 'packaging' / (folder + '.lock.txt')).write_text(rows, encoding='utf-8')
print(len(manifest['models']), 'assets recorded')
