"""Run the complete local GPU pipeline using generated test media only."""
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import psutil
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import pipeline

folder = ROOT / 'tests' / '本地验证'
folder.mkdir(exist_ok=True)
video = str(folder / '测试片.mp4')
ffmpeg = pipeline._bundle_ffmpeg()
subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x240:rate=24:duration=0.5', '-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=48000:duration=0.5',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', video], check=True)
result = {'python': sys.version, 'torch': torch.__version__,
          'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(),
          'input': pipeline.video_info(video)}

def stage(name, fn):
    start = time.monotonic()
    print('START', name, flush=True)
    value = fn()
    result[name] = {'result': value, 'seconds': round(time.monotonic()-start, 2),
                    'rss_mib': round(psutil.Process().memory_info().rss / 2**20)}
    print('PASS', name, result[name], flush=True)
    return value

stage('dlss_unguided', lambda: pipeline.generate_dlss(video, {'guidance_mode': 0}))
output = stage('export_audio', lambda: pipeline.export_video(video, 'dlss', fps=24))
assert pipeline.video_info(output)[:2] == (12, 24.0)
subprocess.run([ffmpeg, '-v', 'error', '-i', output, '-map', '0:a:0', '-f', 'null', '-'], check=True)
before = next(pipeline.iter_frames(video))[1]
after = pipeline.imread(str(folder / '测试片_dlss' / '000000.png'))
result['mean_pixel_change'] = float(np.abs(before.astype(float) - after.astype(float)).mean())
assert result['mean_pixel_change'] > 0

stage('depth', lambda: pipeline.generate_depth(video, frame_limit=3, edge=240, force=True))
stage('flow', lambda: pipeline.generate_flow(video, frame_limit=3, edge=240, force=True))
stage('dlss_guided', lambda: pipeline.generate_dlss(video, {'guidance_mode': 3}, frame_limit=3))
stage('export_guided', lambda: pipeline.export_video(video, 'dlss', frames=3, fps=24))
stage('depth_cached', lambda: pipeline.generate_depth(video, frame_limit=3, edge=240))
stage('flow_cached', lambda: pipeline.generate_flow(video, frame_limit=3, edge=240))
assert pipeline.video_info(output)[0] == 3
result['status'] = 'passed'
(ROOT / 'logs' / 'smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('ALL PASSED', flush=True)
