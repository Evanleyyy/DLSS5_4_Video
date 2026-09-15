"""Exercise the real cache controls using isolated, explicitly generated fixtures."""
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
import uuid


def verify_cache(app, directory, request_runtime_cleanup=False, screenshots=False):
    import cv2
    import numpy as np
    import pipeline
    directory = Path(directory) / uuid.uuid4().hex
    directory.mkdir(parents=True)
    root = app.root
    errors = []
    previous_handler = root.report_callback_exception
    root.report_callback_exception = lambda *error: errors.append(str(error))

    def pump():
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
            if not app._busy and not (app.thread and app.thread.is_alive()):
                root.update()
                assert not errors, errors
                return
        raise AssertionError('缓存界面任务超时')

    previous_video = app.video
    runtime = directory / 'runtime'
    fake_id = '1234567890abcdefabcd'
    fake = runtime / fake_id
    (fake / 'app').mkdir(parents=True)
    (fake / 'ready.txt').write_text(fake_id, encoding='utf-8-sig')
    (fake / 'app' / 'test.dat').write_bytes(b'cache')
    video = directory / '缓存操作测试.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 12, (320, 240))
    assert writer.isOpened()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :160] = [50, 100, 210]
    frame[:, 160:] = [210, 130, 30]
    writer.write(frame)
    writer.release()
    original_video = video.read_bytes()
    export = directory / '手动导出.png'
    export.write_bytes(b'keep exported file')
    for kind, folder in zip(('depth', 'flow', 'dlss'), pipeline.out_dirs(str(video))):
        folder = Path(folder)
        folder.mkdir()
        extension = {'depth': 'jpg', 'flow': 'flo', 'dlss': 'png'}[kind]
        (folder / ('000000.' + extension)).write_bytes(b'generated')
        (folder / 'cache.json').write_text(json.dumps({'record': {
            'source': str(video), 'options': {'kind': kind}}}), encoding='utf-8')
    try:
        app.video = str(video)
        with patch.dict(os.environ, {'DLSS5_CACHE_ROOT': str(runtime)}):
            app.tabs.select(app.pages['缓存'])
            app.cache_scan_btn.invoke()
            pump()
            assert len(app._cache_rows) == 4
            if screenshots:
                from PIL import ImageGrab
                import ctypes
                handle = ctypes.windll.user32.GetParent(root.winfo_id())
                ImageGrab.grab(window=handle).save(directory / '缓存管理.png')
            for selected, entry in app._cache_rows:
                selected.set(int(entry['type'] == 'depth'))
            app.cache_clean_btn.invoke()
            pump()
            assert not Path(pipeline.out_dirs(str(video))[0]).exists()
            assert Path(pipeline.out_dirs(str(video))[1]).exists()
            assert Path(pipeline.out_dirs(str(video))[2]).exists()
            assert export.read_bytes() == b'keep exported file'
            assert video.read_bytes() == original_video
            if os.environ.get('DLSS5_LAUNCHER_PATH'):
                for selected, entry in app._cache_rows:
                    selected.set(int(entry['type'] == 'runtime'))
                app.cache_clean_btn.invoke()
                pump()
                assert not fake.exists(), '历史版本缓存未清理'
        app.video = previous_video
        if request_runtime_cleanup:
            app.cache_scan_btn.invoke()
            pump()
            for selected, entry in app._cache_rows:
                selected.set(int(entry.get('current', False)))
            assert any(selected.get() for selected, _ in app._cache_rows)
            app.cache_clean_btn.invoke()
            pump()
            current = [entry for _, entry in app._cache_rows if entry.get('current')][0]
            assert current['pending'] and Path(current['path']).is_dir()
            app.cache_cancel_btn.invoke()
            pump()
            current = [entry for _, entry in app._cache_rows if entry.get('current')][0]
            assert not current['pending'], '取消操作未生效'
            for selected, entry in app._cache_rows:
                selected.set(int(entry.get('current', False)))
            app.cache_clean_btn.invoke()
            pump()
            assert (Path(current['path']) / 'cleanup-request.txt').is_file()
        return {'status': 'passed', 'fixture_directory': str(directory),
                'manual_video_cleanup': True, 'exports_preserved': True,
                'runtime_cleanup_requested': request_runtime_cleanup}
    finally:
        app.video = previous_video
        root.report_callback_exception = previous_handler
