"""Local engine configuration; importing this module never loads a GPU model."""
import json
import math
import os
from pathlib import Path
import sys

ENGINES = {'dlss': 'DLSS（原尺寸增强）', 'pisa': 'PiSA-SR（图片）',
           'seedvr2': 'SeedVR2 3B FP8（图片／视频）', 'vosr': 'VOSR 2.0（图片）'}
DEFAULTS = {'engine': 'dlss', 'scale': 2, 'seed': 42, 'tile': 512,
            'pisa_pixel': 1.0, 'pisa_semantic': 1.0, 'batch': 5, 'blocks': 24,
            'color': 'wavelet', 'model_root': ''}
FILES = {
 'pisa': ['pisa_sr.pkl', 'sd21/unet/diffusion_pytorch_model.safetensors',
          'sd21/vae/diffusion_pytorch_model.safetensors', 'sd21/text_encoder/model.safetensors',
          'sd21/unet/config.json', 'sd21/vae/config.json', 'sd21/text_encoder/config.json',
          'sd21/tokenizer/vocab.json', 'sd21/tokenizer/merges.txt', 'sd21/scheduler/scheduler_config.json'],
 'vosr': ['VOSR2/args.json', 'VOSR2/checkpoints/ema_model.safetensors',
          'Qwen-Image-vae-2d/config.json', 'Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors',
          'torch_cache/checkpoints/dinov2_vitl14_pretrain.pth', 'torch_cache/facebookresearch_dinov2_main/hubconf.py'],
 'seedvr2': ['seedvr2_ema_3b_fp8_e4m3fn.safetensors', 'ema_vae_fp16.safetensors']}


def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


def runtime_root():
    return app_root() / 'runtime'


def data_root():
    root = Path(os.environ.get('DLSS5_DATA_ROOT', str(app_root() / 'data')))
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_root(settings=None):
    return Path((settings or {}).get('model_root') or runtime_root() / 'sr-models').resolve()


def normalize(source=None):
    source = source or {}
    result = {key: source.get(key, value) for key, value in DEFAULTS.items()}
    if result['engine'] not in ENGINES:
        raise ValueError('未知的超分引擎')
    for key, low, high in [('scale', 2, 4), ('seed', 0, 2147483647), ('tile', 256, 1024),
                           ('batch', 5, 17), ('blocks', 0, 32)]:
        value = float(result[key])
        if not math.isfinite(value) or int(value) != value or not low <= value <= high:
            raise ValueError('超分参数超出范围：' + key)
        result[key] = int(value)
    if result['scale'] not in (2, 4) or result['tile'] % 64 or result['batch'] % 4 != 1:
        raise ValueError('超分倍率为 2 或 4；分块为 64 的倍数；视频批次为 4n+1')
    for key in ('pisa_pixel', 'pisa_semantic'):
        result[key] = float(result[key])
        if not math.isfinite(result[key]) or not 0 <= result[key] <= 2:
            raise ValueError('PiSA 权重应为 0～2')
    if result['color'] not in ('wavelet', 'adain', 'nofix'):
        raise ValueError('未知的颜色校正方式')
    result['model_root'] = str(result['model_root'])
    return result


def missing(settings):
    cfg = normalize(settings)
    if cfg['engine'] == 'dlss':
        return []
    root = model_root(cfg) / cfg['engine']
    absent = [str(root / name) for name in FILES[cfg['engine']]
              if not (root / name).is_file() or (root / name).stat().st_size == 0]
    for path in (runtime_root() / 'python/python.exe', runtime_root() / 'sr-packages/torch/__init__.py',
                 runtime_root() / 'sr-engines' / cfg['engine']):
        if not path.exists():
            absent.append(str(path))
    return absent


def fingerprint(settings):
    cfg = normalize(settings)
    root = model_root(cfg) / cfg['engine']
    return [(name, (root / name).stat().st_size, (root / name).stat().st_mtime_ns)
            if (root / name).is_file() else (name, 0, 0) for name in FILES.get(cfg['engine'], [])]


def load_preferences():
    try:
        return normalize(json.loads((data_root() / 'super_resolution.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return normalize()


def save_preferences(settings):
    path = data_root() / 'super_resolution.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(normalize(settings), ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
