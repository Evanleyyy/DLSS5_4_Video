"""Verify multi-channel exports through the actual GUI command and worker."""
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

import numpy as np

import pipeline


def verify_export(app, directory, video, original, processed, screenshots=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = app.root
    errors = []
    root.report_callback_exception = lambda *error: errors.append(str(error))
    root.deiconify()
    root.geometry('1240x820+20+20')
    app._close_live()
    app.view_var.set('原图')
    app.tabs.select(app.pages['导出'])
    records = []

    def pump():
        root.update()
        assert not errors, errors

    def choose(fmt, channels, scope='当前帧'):
        app.v_export_format.set(fmt)
        app.v_export_scope.set(scope)
        app._update_export_options()
        for key, variable in app.v_export_channels.items():
            variable.set(int(key in channels))

    def export():
        app.last_export = None
        with patch('tkinter.filedialog.askdirectory', return_value=str(directory)), \
             patch('tkinter.messagebox.showwarning') as warning:
            app.export()
            assert not warning.called, warning.call_args
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            pump()
            if (app.thread is None or not app.thread.is_alive()) and app._ui_events.empty():
                break
            time.sleep(.01)
        else:
            raise AssertionError('Export worker timeout')
        assert app.last_export, app.status.cget('text')
        assert all(Path(path).exists() for path in app.last_export['outputs'])
        records.append(app.last_export)
        return app.last_export

    app.current_is_image = True
    app.video = None
    app.image_path = str(directory / '通道图片.png')
    app.image_bgr, app.image_dlss = original.copy(), processed.copy()
    app._reset_image_editor()
    app._update_export_btn()
    h, w = original.shape[:2]
    app.selection.data[h//4:3*h//4, w//4:3*w//4] = 255
    app.selection.revision += 1
    app.v_mask_enabled.set(1)
    app.v_feather.set(5)
    choose('图片', ['original', 'dlss', 'depth', 'mask'])
    expected = app._image_output()
    result = export()
    assert len(result['outputs']) == 4
    for path in map(Path, result['outputs']):
        pixels = pipeline.imread(str(path), -1)
        assert pixels.shape[:2] == original.shape[:2]
        if path.stem.endswith('_dlss'):
            np.testing.assert_array_equal(pixels, expected)
        elif path.stem.endswith('_original'):
            np.testing.assert_array_equal(pixels, original)
        elif path.stem.endswith('_depth'):
            assert pixels.dtype == np.uint16 and pixels.ndim == 2
        elif path.stem.endswith('_mask'):
            assert pixels.ndim == 2 and np.any(pixels == 0) and np.any(pixels == 255)
    assert str(app.export_checks['flow'].cget('state')) == 'disabled'
    # Invalid checkbox/numeric input must be caught before opening an output dialog.
    choose('图片', [])
    with patch('tkinter.messagebox.showwarning') as warning, patch('tkinter.filedialog.askdirectory') as dialog:
        app.export()
        assert warning.called and not dialog.called
    choose('视频', ['original'])
    app.v_output_duration.set('invalid')
    with patch('tkinter.messagebox.showwarning') as warning, patch('tkinter.filedialog.askdirectory') as dialog:
        app.export()
        assert warning.called and not dialog.called
    app.v_output_duration.set('.25')
    app.v_output_fps.set('12')
    choose('视频', ['original', 'dlss', 'mask'])
    result = export()
    for path in result['outputs']:
        assert pipeline.video_info(path) == (3, 12., w + w % 2, h + h % 2)

    app.current_is_image = False
    app.video = str(video)
    app.nframes, app.fps, _, _ = pipeline.video_info(str(video))
    app._reset_image_editor()
    app._update_export_btn()
    app.fslider.configure(to=app.nframes-1)
    app.fslider.set(1)
    all_video_channels = ['original', 'dlss', 'depth', 'flow']
    choose('图片', all_video_channels, '当前帧')
    result = export()
    assert len(result['outputs']) == 4
    expected_frame = list(pipeline.iter_frames(str(video)))[1][1]
    for path in result['outputs']:
        assert Path(path).stem.endswith('_000001')
        if '_original_' in path:
            np.testing.assert_array_equal(pipeline.imread(path), expected_frame)
    choose('图片', all_video_channels, '全部帧')
    result = export()
    for path in result['outputs']:
        assert len(list(Path(path).glob('*.png'))) == app.nframes
    choose('视频', all_video_channels)
    app.v_export_audio.set(1)
    result = export()
    for path in result['outputs']:
        assert pipeline.video_info(path)[:2] == (app.nframes, app.fps)
        subprocess.run([pipeline._bundle_ffmpeg(), '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 'null', '-'],
                       check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert str(app.export_checks['mask'].cget('state')) == 'disabled'
    if screenshots:
        import ctypes
        from PIL import ImageGrab
        pump()
        handle = ctypes.windll.user32.GetParent(root.winfo_id())
        ImageGrab.grab(window=handle).save(directory / '多通道导出界面.png')
    app._close_live()
    return {'status': 'passed', 'jobs': records,
            'checks': 'image channels, static MP4, current frame, PNG sequences, video channels with audio, mask pixels, validation'}
