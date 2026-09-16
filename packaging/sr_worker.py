"""Offline model worker. Each job owns its CUDA context and releases it on exit."""
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from types import SimpleNamespace
import task_control


def offline_connect(self, address):
    raise RuntimeError('离线推理禁止网络连接；请先准备完整的本地模型')


socket.socket.connect = offline_connect
socket.socket.connect_ex = offline_connect
os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'backend:cudaMallocAsync')
os.environ.setdefault('TORCHDYNAMO_DISABLE', '1')

import cv2
import numpy as np
import torch
from PIL import Image


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.dtype != np.uint8:
        raise ValueError('图片必须是可读取的 8 位图片：' + str(path))
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def save_image(path, image):
    task_control.checkpoint()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('无法编码超分结果')
    data.tofile(str(path))


def denoise(image, cfg):
    task_control.checkpoint()
    import image_denoise
    rgba = cv2.cvtColor(image[..., :3], cv2.COLOR_BGR2RGBA)
    return cv2.cvtColor(image_denoise.apply_rgba(rgba, image_denoise.normalize_settings(cfg)), cv2.COLOR_RGBA2BGR)


def color_fix(result, source, method):
    if method == 'nofix':
        return result
    source = cv2.resize(source, (result.shape[1], result.shape[0]), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    target = result.astype(np.float32)
    if method == 'wavelet':
        target = target - cv2.GaussianBlur(target, (0, 0), 5) + cv2.GaussianBlur(source, (0, 0), 5)
    else:
        mean, std = cv2.meanStdDev(target)
        src_mean, src_std = cv2.meanStdDev(source)
        target = (target - mean.ravel()) / (std.ravel() + 1e-5) * (src_std.ravel() + 1e-5) + src_mean.ravel()
    return np.rint(target).clip(0, 255).astype(np.uint8)


class Pisa:
    def __init__(self, root, cfg):
        from pisasr import PiSASR_eval
        args = SimpleNamespace(pretrained_model_path=str(root / 'sd21'), pretrained_path=str(root / 'pisa_sr.pkl'),
            mixed_precision='fp16', default=False, lambda_pix=cfg['pisa_pixel'], lambda_sem=cfg['pisa_semantic'],
            vae_encoder_tiled_size=cfg['tile'], vae_decoder_tiled_size=max(64, cfg['tile'] // 4),
            latent_tiled_size=cfg['tile'] // 8, latent_tiled_overlap=16)
        self.model = PiSASR_eval(args)
        self.model.set_eval()

    def image(self, bgr, cfg):
        torch.manual_seed(cfg['seed'])
        w, h = bgr.shape[1] * cfg['scale'], bgr.shape[0] * cfg['scale']
        rgb = cv2.cvtColor(cv2.resize(bgr, (w, h), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
        padded = cv2.copyMakeBorder(rgb, 0, (-h) % 8, 0, (-w) % 8, cv2.BORDER_REPLICATE)
        # Upstream constructs Gaussian weights before clamping the tile size.
        # Clamp both values here so narrow and odd-size inputs have matching weights.
        tile = min(cfg['tile'] // 8, padded.shape[0] // 8, padded.shape[1] // 8)
        self.model.args.latent_tiled_size = tile
        self.model.args.latent_tiled_overlap = min(16, tile // 4)
        tensor = torch.from_numpy(padded.copy()).permute(2, 0, 1).unsqueeze(0).cuda().float() / 127.5 - 1
        with torch.inference_mode():
            _, result = self.model(False, tensor, prompt='')
        result = ((result[0].float().cpu().permute(1, 2, 0).numpy() + 1) * 127.5).clip(0, 255).astype(np.uint8)
        result = cv2.cvtColor(result[:h, :w], cv2.COLOR_RGB2BGR)
        return color_fix(result, bgr, cfg['color'])


class Vosr:
    def __init__(self, root, cfg):
        import inference_vosr_onestep as impl
        from models.qwenimage_vae2d import AutoencoderKLQwenImage2D
        from safetensors.torch import load_file
        self.impl = impl
        args = json.loads((root / 'VOSR2/args.json').read_text())
        args.update(tile_size=cfg['tile'], tile_overlap=64, vae_tile_size=cfg['tile'], vae_tile_overlap=64,
                    posterior_mode=True, infer_steps=1)
        self.args = SimpleNamespace(**args)
        self.vae = AutoencoderKLQwenImage2D.from_pretrained(str(root / 'Qwen-Image-vae-2d'), local_files_only=True).cuda().eval()
        old_load = torch.hub.load
        def local_dino(repo, model, **kwargs):
            encoder = old_load(str(root / 'torch_cache/facebookresearch_dinov2_main'), model,
                               source='local', pretrained=False)
            encoder.load_state_dict(torch.load(root / 'torch_cache/checkpoints/dinov2_vitl14_pretrain.pth',
                                              map_location='cpu', weights_only=True), strict=True)
            return encoder
        torch.hub.load = local_dino
        try:
            self.venc = impl.load_dinov2(self.args, 'cuda')
        finally:
            torch.hub.load = old_load
        a = self.args
        self.model = impl.LightningDiT(input_size=a.resolution // 8, patch_size=a.patch_size,
            in_channels=32, out_channels=16, hidden_size=a.dim, depth=a.depth, num_heads=a.num_heads,
            mlp_ratio=a.mlp_ratio, z_dims=a.enc_dim, encdim_ratio=a.encdim_ratio,
            auxiliary_time_cond=a.auxiliary_time_cond, use_qknorm=a.use_qknorm,
            use_swiglu=a.use_swiglu, use_rope=a.use_rope, use_rmsnorm=a.use_rmsnorm,
            wo_shift=a.wo_shift, num_fused_layers=len(a.layer_dinov2b_list))
        self.model.load_state_dict(load_file(str(root / 'VOSR2/checkpoints/ema_model.safetensors')), strict=True)
        self.model = self.model.to('cuda', dtype=torch.bfloat16).eval()
        self.model.forward = self.model.forward_flexible

    def image(self, bgr, cfg):
        torch.manual_seed(cfg['seed'])
        w, h = bgr.shape[1] * cfg['scale'], bgr.shape[0] * cfg['scale']
        rgb = cv2.cvtColor(cv2.resize(bgr, (w, h), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
        padded = cv2.copyMakeBorder(rgb, 0, (-h) % 16, 0, (-w) % 16, cv2.BORDER_REPLICATE)
        tensor = torch.from_numpy(padded.copy()).permute(2, 0, 1).unsqueeze(0).cuda().float() / 127.5 - 1
        with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
            output = self.impl.tiled_latent_inference(self.model, self.vae, self.venc, tensor, self.args)
        output = ((output[0].float().cpu().permute(1, 2, 0).numpy() + 1) * 127.5).clip(0, 255).astype(np.uint8)
        return color_fix(cv2.cvtColor(output[:h, :w], cv2.COLOR_RGB2BGR), bgr, cfg['color'])


class Seed:
    def __init__(self, root, cfg):
        import inference_cli as impl
        argv = sys.argv
        sys.argv = ['inference_cli.py', 'offline-input.png', '--model_dir', str(root),
            '--dit_model', 'seedvr2_ema_3b_fp8_e4m3fn.safetensors', '--blocks_to_swap', str(cfg['blocks']),
            '--swap_io_components', '--dit_offload_device', 'cpu', '--vae_offload_device', 'cpu',
            '--vae_encode_tiled', '--vae_decode_tiled', '--vae_encode_tile_size', str(cfg['tile']),
            '--vae_decode_tile_size', str(cfg['tile']), '--vae_encode_tile_overlap', '64',
            '--vae_decode_tile_overlap', '64', '--batch_size', str(cfg['batch']),
            '--uniform_batch_size', '--temporal_overlap', '1', '--seed', str(cfg['seed']),
            '--color_correction', 'none' if cfg['color'] == 'nofix' else cfg['color'],
            '--attention_mode', 'sdpa', '--cache_dit', '--cache_vae']
        try:
            self.args = impl.parse_arguments()
        finally:
            sys.argv = argv
        self.impl, self.cache = impl, {}

    def dimensions(self, bgr, cfg):
        self.args.resolution = min(bgr.shape[:2]) * cfg['scale']
        self.args.max_resolution = max(bgr.shape[:2]) * cfg['scale']

    def image(self, bgr, cfg):
        self.dimensions(bgr, cfg)
        tensor = torch.from_numpy(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).copy()).unsqueeze(0).float() / 255
        result = self.impl._single_gpu_direct_processing(tensor, self.args, '0', self.cache)
        return cv2.cvtColor((result[0].float().cpu().numpy() * 255).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)

    def video(self, cap, count, cfg, pre_cfg):
        class DenoisedCapture:
            def read(self):
                ok, frame = cap.read()
                return (ok, denoise(frame, pre_cfg) if ok else None)
        shape = np.empty((int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), 3), np.uint8)
        self.dimensions(shape, cfg)
        for frames in self.impl._stream_video_chunks(DenoisedCapture(), count, cfg['batch'] * 2 - 1, 1,
                self.args, '0', self.impl.debug, self.cache):
            for frame in frames:
                yield cv2.cvtColor((frame.float().cpu().numpy() * 255).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def finish(original, result, settings, cfg):
    w, h = original.shape[1] * cfg['scale'], original.shape[0] * cfg['scale']
    reference = cv2.resize(original[..., :3], (w, h), interpolation=cv2.INTER_CUBIC)
    weight = settings.get('overall_weight', 1.0)
    if weight == 0:
        output = reference
    else:
        result = cv2.resize(result, (w, h), interpolation=cv2.INTER_CUBIC) if result.shape[:2] != (h, w) else result
        result = denoise(result, settings.get('output_denoise'))
        output = np.rint(reference.astype(np.float32) * (1 - weight) + result.astype(np.float32) * weight).clip(0, 255).astype(np.uint8)
    if original.ndim == 3 and original.shape[2] == 4:
        output = np.dstack([output, cv2.resize(original[..., 3], (w, h), interpolation=cv2.INTER_LINEAR)])
    return output


def execute(job, directory):
    task_control.checkpoint()
    sys.path.insert(0, job['app_source'])
    # Installed builds copy lightweight processing helpers next to this script.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    cfg = job['settings']['super_resolution']
    sys.path.insert(0, str(Path(job['runtime']) / 'sr-engines' / cfg['engine']))
    if job['kind'] == 'video' and cfg['engine'] != 'seedvr2':
        raise ValueError('PiSA-SR 和 VOSR 2.0 为图片引擎；视频请选择 SeedVR2 或 DLSS')
    settings = job['settings']
    total = len(job['inputs']) if job['kind'] == 'images' else job['frames']
    def progress(done, stage):
        temporary = directory / 'progress.tmp'
        temporary.write_text(json.dumps(dict(done=done, total=total, stage=stage), ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, directory / 'progress.json')
    progress(0, '正在加载本地超分模型')
    if not torch.cuda.is_available():
        raise RuntimeError('扩散超分需要可用的 NVIDIA CUDA 显卡')
    torch.set_num_threads(8)
    model = None
    if settings.get('overall_weight', 1.0) > 0:
        model = {'pisa': Pisa, 'vosr': Vosr, 'seedvr2': Seed}[cfg['engine']](Path(job['model_root']) / cfg['engine'], cfg)
    task_control.checkpoint()
    output_dir = Path(job['output'])
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    start = time.monotonic()
    if job['kind'] == 'images':
        # Avoid overwriting two inputs with identical stems in a batch.
        used = set()
        for index, path in enumerate(job['inputs']):
            task_control.checkpoint()
            original = read_image(path)
            source = denoise(original, settings.get('input_denoise'))
            progress(index, '正在超分当前图片')
            result = model.image(source, cfg) if model else source
            task_control.checkpoint()
            output = finish(original, result, settings, cfg)
            name = Path(path).stem + '.png'
            if name.casefold() in used:
                name = Path(path).name.replace('.', '_') + '.png'
            used.add(name.casefold())
            target = output_dir / name
            save_image(target, output)
            outputs.append(str(target))
            progress(index + 1, '图片超分完成')
    else:
        cap, original_cap = cv2.VideoCapture(job['input']), cv2.VideoCapture(job['input'])
        done = 0
        try:
            if model:
                iterator = model.video(cap, total, cfg, settings.get('input_denoise'))
            else:
                iterator = (None for _ in range(total))
            for result in iterator:
                task_control.checkpoint()
                ok, original = original_cap.read()
                if not ok or done >= total:
                    raise RuntimeError('原视频帧数与超分结果不一致')
                output = finish(original, original if result is None else result, settings, cfg)
                save_image(output_dir / f'{done:06d}.png', output)
                done += 1
                progress(done, '连续帧超分')
            if done != total:
                raise RuntimeError(f'视频超分未完成：{done}/{total} 帧')
        finally:
            cap.release()
            original_cap.release()
        outputs = [str(output_dir)]
    return {'outputs': outputs, 'count': total, 'seconds': round(time.monotonic() - start, 3),
            'engine': cfg['engine'], 'offline': True, 'peak_vram': torch.cuda.max_memory_allocated()}


if __name__ == '__main__':
    request = Path(sys.argv[1]).resolve()
    try:
        job = json.loads(request.read_text(encoding='utf-8'))
        control = task_control.FilePauseControl(request.parent,
            synchronize=lambda: torch.cuda.synchronize() if torch.cuda.is_initialized() else None,
            parent_pid=job.get('parent_pid'))
        next_check = [0.]
        def pause_before_module(module, inputs):
            # Keep the check inexpensive even in deep VAE/DiT graphs. Sync CUDA
            # only when a pause is requested, so normal inference stays asynchronous.
            now = time.monotonic()
            if now >= next_check[0]:
                control.checkpoint()
                next_check[0] = time.monotonic() + .05
        with task_control.bind(control), torch.nn.modules.module.register_module_forward_pre_hook(pause_before_module):
            result = execute(job, request.parent)
        (request.parent / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    except Exception as error:
        traceback.print_exc()
        (request.parent / 'error.txt').write_text(str(error), encoding='utf-8')
        raise SystemExit(1)
