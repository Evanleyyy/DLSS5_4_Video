"""Download the selected inference assets to F-drive project storage."""
import concurrent.futures
import json
import hashlib
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'runtime' / 'sr-models'
PROXY = os.environ.get('HTTPS_PROXY', '')


def download(item):
    url, path, expected = item
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = path.with_suffix(path.suffix + '.complete')
    if path.is_file() and expected:
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() == expected:
                marker.write_text(expected)
                return str(path.relative_to(ROOT)) + ' 校验通过'
    args = ['curl.exe', '-fL', '--retry', '5', '--retry-delay', '2',
            '--connect-timeout', '30', '--speed-limit', '1024', '--speed-time', '120',
            '-C', '-', '-o', str(path), url]
    if PROXY:
        args[1:1] = ['--proxy', PROXY]
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(str(path) + ': ' + result.stderr.decode(errors='replace')[-800:])
    if expected:
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != expected:
                raise RuntimeError('文件校验失败，请手动删除此损坏文件后重试：' + str(path))
    marker.write_text(expected or str(path.stat().st_size), encoding='ascii')
    return str(path.relative_to(ROOT)) + ' 完成'


def assets():
    manifest_file = ROOT / 'packaging/sr-assets-manifest.json'
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        items = []
        repositories = manifest['weight_repositories']
        for entry in manifest['models']:
            name = entry['path']
            if name == 'pisa/pisa_sr.pkl':
                url = 'https://drive.usercontent.google.com/download?id=1PWFM5zUd6lNMOyKX64anvUXhKEOCrf8L&export=download&confirm=t'
            else:
                repo, relative = (('Manojb/stable-diffusion-2-1-base', name.removeprefix('pisa/sd21/')) if name.startswith('pisa/') else
                                  ('CSWRY/VOSR', name.removeprefix('vosr/')) if name.startswith('vosr/') else
                                  ('numz/SeedVR2_comfyUI', name.removeprefix('seedvr2/')))
                url = f'https://huggingface.co/{repo}/resolve/{repositories[repo]}/{relative}'
            items.append((url, MODEL / name, entry['sha256']))
        return items
    items = []
    def hf(repo, name, target):
        items.append((f'https://huggingface.co/{repo}/resolve/main/{name}', target, None))
    vosr_files = json.loads((ROOT / 'downloads/vosr-files.json').read_text())['siblings']
    for file in vosr_files:
        name = file['rfilename']
        if (name.startswith(('VOSR2/', 'Qwen-Image-vae-2d/')) or
            name == 'torch_cache/checkpoints/dinov2_vitl14_pretrain.pth' or
            (name.startswith('torch_cache/facebookresearch_dinov2_main/') and
             (name.endswith('.py') or '/LICENSE' in name))):
            hf('CSWRY/VOSR', name, MODEL / 'vosr' / name)
    for name in ('seedvr2_ema_3b_fp8_e4m3fn.safetensors', 'ema_vae_fp16.safetensors'):
        hf('numz/SeedVR2_comfyUI', name, MODEL / 'seedvr2' / name)
    sd_files = json.loads((ROOT / 'downloads/sd21-files.json').read_text())['siblings']
    for file in sd_files:
        name = file['rfilename']
        if (name.startswith(('tokenizer/', 'scheduler/')) or name.endswith('/config.json') or
            name in ('text_encoder/model.safetensors', 'unet/diffusion_pytorch_model.safetensors',
                     'vae/diffusion_pytorch_model.safetensors', 'model_index.json', 'README.md')):
            hf('Manojb/stable-diffusion-2-1-base', name, MODEL / 'pisa' / 'sd21' / name)
    items.append(('https://drive.usercontent.google.com/download?id=1PWFM5zUd6lNMOyKX64anvUXhKEOCrf8L&export=download&confirm=t',
                  MODEL / 'pisa' / 'pisa_sr.pkl', None))
    return items


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(download, item) for item in assets()]
        failures = []
        for job in concurrent.futures.as_completed(jobs):
            try:
                print(job.result(), flush=True)
            except Exception as error:
                failures.append(str(error))
                print('失败: ' + str(error), flush=True)
    if failures:
        raise SystemExit(1)
