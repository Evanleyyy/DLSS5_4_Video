"""Run a genuine offline model job and validate output dimensions and content."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import cv2
import numpy as np
import sr_backend
import sr_settings

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('engine', choices=['pisa', 'seedvr2', 'vosr'])
    parser.add_argument('--video', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'logs/sr-verification' / args.engine
    directory.mkdir(parents=True, exist_ok=True)
    image = np.zeros((96, 128, 3), np.uint8)
    image[..., 0] = np.linspace(30, 160, 128, dtype=np.uint8)
    image[..., 1] = np.linspace(25, 190, 96, dtype=np.uint8)[:, None]
    image[..., 2] = 60
    cv2.rectangle(image, (14, 12), (95, 77), (210, 185, 110), 2)
    cv2.putText(image, 'SR 2026', (12, 52), cv2.FONT_HERSHEY_SIMPLEX, .48, (245, 245, 245), 1, cv2.LINE_AA)
    cv2.circle(image, (104, 72), 13, (30, 190, 245), -1)
    cfg = sr_settings.normalize({'engine': args.engine, 'scale': 2, 'tile': 256})
    settings = {'super_resolution': cfg, 'overall_weight': 1.0}
    start = time.monotonic()
    if args.video:
        video = directory / 'input.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 6, (128, 96))
        for index in range(11):
            writer.write(np.roll(image, index, axis=1))
        writer.release()
        result = sr_backend.process_video(video, directory / 'video-output', settings, 11,
            lambda i, n, text: print(i, n, text, flush=True))
        for index in range(11):
            output = cv2.imread(str(directory / 'video-output' / f'{index:06d}.png'))
            assert output is not None and output.shape == (192, 256, 3)
    else:
        output = sr_backend.process_image(image, settings, lambda i, n, text: print(i, n, text, flush=True))
        assert output.shape == (192, 256, 3), output.shape
        assert np.std(output) > 5
        ref = cv2.resize(image, (256, 192), interpolation=cv2.INTER_CUBIC)
        delta = float(np.abs(output.astype(float) - ref).mean())
        assert delta > .1, delta
        cv2.imwrite(str(directory / 'result.png'), output)
        cv2.imwrite(str(directory / 'comparison.png'), np.concatenate([ref, output], axis=1))
        result = {'mean_difference': delta}
    result.update(engine=args.engine, seconds=round(time.monotonic()-start, 3), video=args.video, passed=True)
    (directory / ('video.json' if args.video else 'image.json')).write_text(json.dumps(result, indent=2))
    print(result)
