"""Verify the installed SR worker, isolated publication and mixed-cache recovery."""
import json
import os
from pathlib import Path
from unittest.mock import patch


def verify(directory):
    import cv2
    import numpy as np
    import dlss_layers
    import media_export
    import pipeline
    import sr_settings

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running'}
    try:
        video = str(directory / '缓存恢复测试.mp4')
        frame = np.full((48, 64, 3), 80, np.uint8)
        media_export.encode_video([frame] * 3, 3, 12, video)
        # Exercise the real installed worker without spending time on model inference.
        settings = dlss_layers.normalize_settings({'guidance_mode': 0, 'overall_weight': 0,
                                                   'super_resolution': {'engine': 'seedvr2', 'scale': 2}})
        assert pipeline.generate_dlss(video, settings) == 3
        pipeline.validate_dlss_frames(video, settings, 3)
        cache = Path(pipeline.out_dirs(video)[2])
        assert pipeline.dlss_cache_matches(video, settings, 2)
        pipeline.imwrite(str(cache / '000001.png'), frame)
        try:
            pipeline.validate_dlss_frames(video, settings, 3)
        except ValueError as error:
            assert '000001.png' in str(error) and '128×96' in str(error) and '64×48' in str(error)
            result['mixed_cache_detected'] = str(error)
        else:
            raise AssertionError('未识别被覆盖的缓存帧')
        request = dict(is_image=False, source=video, format='视频', scope='全部帧',
                       channels=['original', 'dlss'], directory=str(directory), fps=12,
                       duration=1, audio=False, crf=18, frame=0, frames=3)
        with patch.object(pipeline, 'generate_dlss', wraps=pipeline.generate_dlss) as regenerate:
            exported = media_export.export_channels(request, settings)
            assert regenerate.call_count == 1
        sizes = [pipeline.video_info(path) for path in exported['outputs']]
        assert sizes == [(3, 12., 64, 48), (3, 12., 128, 96)], sizes
        pipeline.validate_dlss_frames(video, settings, 3)
        jobs = list((sr_settings.data_root() / 'sr-cache').glob('job-*/request.json'))
        assert len(jobs) == 2
        for path in jobs:
            job = json.loads(path.read_text(encoding='utf-8'))
            staged = Path(job['output'])
            assert staged.is_relative_to(sr_settings.data_root()) and staged != cache
            assert not staged.exists()  # Frames were published and the empty stage removed.
            assert job['parent_pid'] == os.getpid()
            assert not (path.parent / 'active').exists()
        result.update(status='passed', real_worker_and_isolated_publication=True,
                      automatic_mixed_cache_regeneration=True, outputs=exported['outputs'], video_info=sizes)
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
