"""Stream selected image/video channels into separate output files."""
import itertools
import math
import os
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np

import pipeline
import task_control

CHANNELS = {'original': '原图', 'dlss': '处理结果（所选引擎）', 'depth': '深度图',
            'flow': '光流可视化', 'mask': '局部遮罩'}


def _video_frame(image):
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError('无法读取导出帧')
    if image.dtype == np.uint16:
        image = np.rint(image.astype(np.float32) / 257).astype(np.uint8)
    if image.dtype != np.uint8:
        raise ValueError('视频帧必须为 8 位或 16 位图像')
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 4:
        image = image[..., :3]
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 1:
        raise ValueError('导出帧尺寸或颜色通道不正确')
    return np.ascontiguousarray(image)


def encode_video(images, count, fps, destination, *, audio_source=None, crf=18,
                 progress=None, ffmpeg=None):
    """Publish only complete encoded videos; retain an existing file on failure."""
    if not math.isfinite(fps) or fps <= 0 or count < 1:
        raise ValueError('视频帧率和帧数必须大于零')
    iterator = iter(images)
    try:
        first = _video_frame(next(iterator))
    except StopIteration:
        raise ValueError('没有可导出的帧') from None
    ffmpeg = ffmpeg or pipeline._bundle_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('缺少 FFmpeg')
    h, w = first.shape[:2]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.dlss-export-', suffix='.mp4', dir=destination.parent)
    os.close(fd)
    process = None
    try:
        with tempfile.TemporaryFile() as errors:
            command = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                       '-s', f'{w}x{h}', '-r', str(fps), '-i', '-']
            if audio_source:
                command += ['-i', str(audio_source), '-map', '0:v:0', '-map', '1:a:0?',
                            '-c:a', 'aac', '-b:a', '192k']
            command += ['-c:v', 'libx264', '-crf', str(max(0, min(51, int(crf)))), '-preset', 'medium',
                        '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p',
                        '-t', str(count / fps), '-movflags', '+faststart', temporary]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            pipe_error = None
            written = 0
            try:
                for frame in itertools.chain([first], iterator):
                    task_control.checkpoint()
                    frame = _video_frame(frame)
                    if written >= count:
                        raise ValueError(f'导出帧数超过预期：需要 {count} 帧，收到第 {written + 1} 帧')
                    if frame.shape != first.shape:
                        raise ValueError(f'导出第 {written + 1} 帧尺寸不一致：'
                                         f'应为 {w}×{h}，实际 {frame.shape[1]}×{frame.shape[0]}')
                    process.stdin.write(frame.tobytes())
                    written += 1
                    if progress:
                        progress(written, count)
                if written != count:
                    raise ValueError(f'缺少导出帧：需要 {count} 帧，实际 {written} 帧')
            except (BrokenPipeError, OSError) as error:
                pipe_error = error
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            code = process.wait(timeout=120)
            errors.seek(0)
            detail = errors.read().decode('utf-8', 'replace')
            if code or pipe_error:
                raise RuntimeError(f'FFmpeg 导出失败（{code}）\n{detail[-2000:]}')
        if not os.path.getsize(temporary):
            raise RuntimeError('导出文件为空')
        os.replace(temporary, destination)
        return str(destination)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if os.path.exists(temporary):
            os.unlink(temporary)
        close = getattr(iterator, 'close', None)
        if close:
            close()


def validate_request(request):
    if request['format'] not in ('图片', '视频'):
        raise ValueError('请选择图片或视频输出')
    channels = request['channels']
    if not channels:
        raise ValueError('请至少勾选一个导出内容')
    allowed = set(CHANNELS) - ({'flow'} if request['is_image'] else {'mask'})
    if not set(channels) <= allowed or len(set(channels)) != len(channels):
        raise ValueError('当前素材不支持所选导出内容')
    if request['format'] == '视频' and request['is_image']:
        if not math.isfinite(request['fps']) or not 1 <= request['fps'] <= 120:
            raise ValueError('静态视频帧率应为 1–120')
        if not math.isfinite(request['duration']) or not .1 <= request['duration'] <= 3600:
            raise ValueError('静态视频时长应为 0.1–3600 秒')
    if not request['is_image']:
        if request['frames'] < 1 or not math.isfinite(request['fps']) or request['fps'] <= 0:
            raise ValueError('视频帧数或帧率无效')
        if request['format'] == '图片' and request['scope'] not in ('当前帧', '全部帧'):
            raise ValueError('请选择当前帧或全部帧')
        if not 0 <= request['frame'] < request['frames']:
            raise ValueError('当前帧超出视频范围')


def _source_frames(video, indices):
    capture = cv2.VideoCapture(video)
    try:
        if indices.start:
            capture.set(cv2.CAP_PROP_POS_FRAMES, indices.start)
        for index in indices:
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f'无法读取原视频第 {index} 帧')
            yield frame
    finally:
        capture.release()


def _channel_frames(video, channel, indices):
    if channel == 'original':
        yield from _source_frames(video, indices)
        return
    directories = pipeline.out_dirs(video)
    directory = directories[{'depth': 0, 'flow': 1, 'dlss': 2}[channel]]
    for index in indices:
        if channel == 'depth':
            path = pipeline.find_depth(directory, index)
            if not path:
                raise ValueError(f'缺少第 {index} 帧深度图')
            yield np.rint(pipeline.read_depth(path) * 65535).clip(0, 65535).astype(np.uint16)
        elif channel == 'flow':
            yield pipeline.colorize_flow(pipeline.read_flo(os.path.join(directory, f'{index:06d}.flo')))
        else:
            image = pipeline.imread(os.path.join(directory, f'{index:06d}.png'))
            if image is None:
                raise ValueError(f'缺少第 {index} 帧 DLSS 结果')
            yield image


def export_channels(request, settings, *, progress=None, log=None):
    from cache_manager import video_cache_guard
    with video_cache_guard(None if request['is_image'] else request['source']):
        return _export_channels(request, settings, progress=progress, log=log)


def _export_channels(request, settings, *, progress=None, log=None):
    """A request is a main-thread snapshot. No Tk variables are accessed here."""
    validate_request(request)
    task_control.checkpoint()
    channels = request['channels']
    progress = progress or (lambda *args: None)
    log = log or (lambda text: None)
    source = request['source']
    if request['is_image']:
        images = dict(request['images'])
        if 'dlss' in channels and images.get('dlss') is None:
            raise ValueError('还没有可导出的 DLSS 结果，请先运行 DLSS')
        if 'depth' in channels:
            progress(0, 1, '生成图片深度')
            images['depth'] = np.rint(pipeline.infer_depth_frame(images['original']) * 65535).astype(np.uint16)
        indices = range(1)
    else:
        from dlss_layers import guidance_needs
        layer_depth, layer_flow = guidance_needs(settings)
        need_depth = 'depth' in channels or ('dlss' in channels and layer_depth)
        need_flow = 'flow' in channels or ('dlss' in channels and layer_flow)
        for needed, operation, label in [(need_depth, pipeline.generate_depth, '生成深度'),
                                          (need_flow, pipeline.generate_flow, '生成光流')]:
            if needed:
                operation(source, progress=lambda i, n, status, label=label: progress(i, n, label))
        if 'dlss' in channels:
            cached = pipeline.dlss_cache_matches(source, settings, request['frames'] - 1)
            if cached:
                progress(0, request['frames'], '检查处理结果缓存')
                try:
                    pipeline.validate_dlss_frames(source, settings, request['frames'])
                except ValueError as error:
                    log(str(error) + '；缓存不完整，将重新生成处理结果。')
                    cached = False
            if not cached:
                pipeline.generate_dlss(source, settings=settings,
                    progress=lambda i, n, status: progress(i, n, '生成处理结果'))
                pipeline.validate_dlss_frames(source, settings, request['frames'])
        if request['format'] == '图片' and request['scope'] == '当前帧':
            indices = range(request['frame'], request['frame'] + 1)
        else:
            indices = range(request['frames'])
    # A unique output directory avoids mixing channels from different runs.
    stem = Path(source).stem[:80] or '素材'
    output = Path(tempfile.mkdtemp(prefix=stem + '_导出_', dir=request['directory']))
    log('输出目录: ' + str(output))
    outputs = []
    for channel in channels:
        task_control.checkpoint()
        label = CHANNELS[channel]
        if request['format'] == '视频':
            if request['is_image']:
                count = max(1, int(round(request['fps'] * request['duration'])))
                iterator = itertools.repeat(images[channel], count)
            else:
                count = len(indices)
                iterator = _channel_frames(source, channel, indices)
            path = encode_video(iterator, count, request['fps'], output / f'{stem}_{channel}.mp4',
                audio_source=source if request['audio'] and not request['is_image'] else None,
                crf=request['crf'], progress=lambda i, n, label=label: progress(i, n, '导出 ' + label))
            outputs.append(path)
            log('已导出: ' + path)
        else:
            sequence = not request['is_image'] and request['scope'] == '全部帧'
            folder = output / channel if sequence else output
            folder.mkdir(exist_ok=True)
            iterator = [images[channel]] if request['is_image'] else _channel_frames(source, channel, indices)
            for index, image in zip(indices, iterator, strict=True):
                task_control.checkpoint()
                name = f'{index:06d}.png' if sequence else f'{stem}_{channel}' + ('' if request['is_image'] else f'_{index:06d}') + '.png'
                path = folder / name
                pipeline.imwrite(str(path), image)
                progress(index - indices.start + 1, len(indices), '导出 ' + label)
            outputs.append(str(folder) if sequence else str(path))
            log('已导出: ' + outputs[-1])
    return {'directory': str(output), 'outputs': outputs, 'channels': list(channels), 'format': request['format']}
