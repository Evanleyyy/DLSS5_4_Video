"""Exercise HD inference, batch RGBA and temporal-video cache/export contracts."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import cv2
import numpy as np
import pipeline
import dlss_layers
import sr_backend


def main():
    directory = ROOT / 'logs/sr-delivery'
    directory.mkdir(parents=True, exist_ok=True)
    image = np.zeros((540, 960, 3), np.uint8)
    image[..., 0] = np.linspace(15, 200, 960, dtype=np.uint8)
    image[..., 1] = np.linspace(25, 190, 540, dtype=np.uint8)[:, None]
    image[..., 2] = 110
    for x in range(20, 940, 60):
        cv2.circle(image, (x, 270), 24, (180, 150, 60), 2, cv2.LINE_AA)
    cv2.putText(image, 'Local Super Resolution 2026', (80, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.7, (245, 245, 240), 2, cv2.LINE_AA)
    results = {}
    for engine in ('pisa', 'seedvr2', 'vosr'):
        settings = dlss_layers.normalize_settings({'super_resolution': {'engine': engine, 'tile': 512}})
        start = time.monotonic()
        output = sr_backend.process_image(image, settings)
        assert output.shape == (1080, 1920, 3)
        pipeline.imwrite(str(directory / (engine + '-1080p.png')), output)
        results[engine] = {'seconds_540p_to_1080p': round(time.monotonic() - start, 3)}
        print(engine, results[engine], flush=True)
    small = cv2.resize(image, (97, 71))
    alpha = np.tile(np.linspace(0, 255, 97, dtype=np.uint8), (71, 1))
    first, second = directory / '批量.png', directory / '批量.webp'
    pipeline.imwrite(str(first), np.dstack([small, alpha]))
    pipeline.imwrite(str(second), small)
    settings = dlss_layers.normalize_settings({'super_resolution': {'engine': 'pisa', 'tile': 256, 'scale': 4}})
    batch = sr_backend.process_batch([first, second], directory / 'batch-output', settings)
    assert len(set(batch['outputs'])) == 2
    assert pipeline.imread(batch['outputs'][0], cv2.IMREAD_UNCHANGED).shape == (284, 388, 4)
    assert pipeline.imread(batch['outputs'][1]).shape == (284, 388, 3)
    results['batch_rgba_4x'] = True
    video = directory / '连续帧含音频.mp4'
    import subprocess
    subprocess.run([pipeline._bundle_ffmpeg(), '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=192x128:rate=6:duration=1.833333', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=1.833333', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', str(video)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    settings = dlss_layers.normalize_settings({'super_resolution': {'engine': 'seedvr2', 'tile': 256},
        'input_denoise': {'enabled': True, 'weight': .5}, 'output_denoise': {'enabled': True, 'weight': .5}})
    count = pipeline.video_info(str(video))[0]
    assert pipeline.generate_dlss(str(video), settings) == count
    assert pipeline.dlss_cache_matches(str(video), settings, count - 1)
    changed = json.loads(json.dumps(settings))
    changed['super_resolution']['seed'] += 1
    assert not pipeline.dlss_cache_matches(str(video), changed)
    changed = json.loads(json.dumps(settings))
    changed['output_denoise']['weight'] = 1
    assert not pipeline.dlss_cache_matches(str(video), changed)
    exported = pipeline.export_video(str(video), 'dlss', fps=6, with_audio=True)
    assert pipeline.video_info(exported) == (count, 6, 384, 256)
    subprocess.run([pipeline._bundle_ffmpeg(), '-v', 'error', '-i', exported, '-map', '0:a:0', '-f', 'null', '-'],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    results['temporal_video_cache_audio_export'] = True
    results['passed'] = True
    (directory / 'verification.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(results, flush=True)


if __name__ == '__main__':
    main()
