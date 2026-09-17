"""Bounded, offline subprocess bridge to optional super-resolution engines."""
import json
import errno
import os
from pathlib import Path
import shutil
import uuid

import cv2
import numpy as np
import sr_settings
import task_control
import model_sessions
from sr_session import ResidentWorker


def job_directory():
    path = sr_settings.data_root() / 'sr-cache' / ('job-' + uuid.uuid4().hex)
    path.mkdir(parents=True)
    (path / 'owner.json').write_text(json.dumps({'application': 'DLSS5Standalone', 'pid': os.getpid()}))
    return path


def run_job(job, progress=None):
    task_control.checkpoint()
    cfg = sr_settings.normalize(job['settings']['super_resolution'])
    absent = sr_settings.missing_runtime(cfg)
    if absent:
        raise FileNotFoundError('超分运行库不完整，请使用完整安装包修复：\n' + '\n'.join(absent[:4]))
    import model_assets
    model_assets.prepare([cfg['engine']], cfg, progress)
    import pipeline
    pipeline.release_guidance_models()
    directory = job_directory()
    (directory / 'active').touch()
    job.update(model_root=str(sr_settings.model_root(cfg)), runtime=str(sr_settings.runtime_root()),
               app_source=str(Path(__file__).resolve().parent), parent_pid=os.getpid())
    request = directory / 'request.json'
    request.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    runtime = sr_settings.runtime_root()
    env = os.environ.copy()
    env.pop('PYTHONHOME', None)
    env['PYTHONPATH'] = str(runtime / 'sr-packages')
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DIFFUSERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', TORCHDYNAMO_DISABLE='1',
               HF_HOME=str(directory / 'hf'), TORCH_HOME=str(directory / 'torch'),
               TMP=str(directory), TEMP=str(directory), MPLCONFIGDIR=str(directory / 'mpl'))
    if cfg['engine'] == 'pisa':
        env['PYTHONPATH'] = str(runtime / 'pisa-packages') + os.pathsep + env['PYTHONPATH']
    # Development uses the current source; installed builds use the deployed helper.
    import sys
    worker = (runtime / 'sr-worker/sr_worker.py' if getattr(sys, 'frozen', False)
              else sr_settings.app_root() / 'packaging/sr_worker.py')
    if not getattr(sys, 'frozen', False):
        env['PYTHONPATH'] += os.pathsep + str(Path(__file__).resolve().parent)
    command = [str(runtime / 'python/python.exe'), '-u', str(worker)]
    owner = model_sessions.current()
    key = (cfg['engine'], str(sr_settings.model_root(cfg)),
           json.dumps(sr_settings.fingerprint(cfg), sort_keys=True))
    def create():
        session_directory = job_directory()
        # The model's library caches outlive a single request directory.
        environment = {**env, 'HF_HOME': str(session_directory / 'hf'),
            'TORCH_HOME': str(session_directory / 'torch'), 'TMP': str(session_directory),
            'TEMP': str(session_directory), 'MPLCONFIGDIR': str(session_directory / 'mpl')}
        return ResidentWorker(command, environment, session_directory)
    session = None
    try:
        session = owner.acquire_sr(key, create) if owner is not None else create()
        total = len(job['inputs']) if job['kind'] == 'images' else job['frames']
        return session.run(request, total, progress)
    except BaseException:
        if owner is not None:
            owner.close_sr()
        raise
    finally:
        (directory / 'active').unlink(missing_ok=True)
        if owner is None and session is not None:
            session.close()



def process_image(image, settings, progress=None):
    directory = job_directory()
    source = directory / 'input.png'
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise ValueError('无法准备超分输入图片')
    encoded.tofile(str(source))
    result = run_job({'kind': 'images', 'inputs': [str(source)], 'output': str(directory / 'output'),
                      'settings': settings}, progress)
    output = cv2.imdecode(np.fromfile(result['outputs'][0], np.uint8), cv2.IMREAD_UNCHANGED)
    if output is None:
        raise RuntimeError('超分模型没有生成有效图片')
    return output


def process_batch(paths, output, settings, progress=None):
    return run_job({'kind': 'images', 'inputs': [str(p) for p in paths], 'output': str(output),
                    'settings': settings}, progress)


def process_video(video, output, settings, frames, progress=None):
    import pipeline
    from frame_sequence import validate_frames
    # Only the parent publishes into the shared video cache while it owns its lock.
    # An interrupted or orphaned worker can write only inside its unique job folder.
    staged = job_directory() / 'video-frames'
    result = run_job({'kind': 'video', 'input': str(video), 'output': str(staged),
                      'settings': settings, 'frames': frames}, progress)
    if result['count'] != frames:
        raise ValueError(f'超分结果帧数不一致：需要 {frames} 帧，实际 {result["count"]} 帧')
    _, _, width, height = pipeline.video_info(str(video))
    scale = sr_settings.normalize(settings['super_resolution'])['scale']
    validate_frames(staged, frames, (width * scale, height * scale))
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    for index in range(frames):
        task_control.checkpoint()
        source = staged / f'{index:06d}.png'
        target = destination / source.name
        try:
            os.replace(source, target)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            # Material and app data can be on different drives. Publish each copied
            # file atomically from a temporary file on the destination drive.
            temporary = destination / ('.dlss-publish-' + uuid.uuid4().hex + '.tmp')
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
                source.unlink()
            finally:
                temporary.unlink(missing_ok=True)
    staged.rmdir()
    result['outputs'] = [str(destination)]
    return result
