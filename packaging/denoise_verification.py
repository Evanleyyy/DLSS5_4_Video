"""Exercise pre/post denoising through the GUI, native DLSS and real exports."""
from pathlib import Path
import multiprocessing
import time
from unittest.mock import patch

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk

import dlss_engine
import dlss_layers
import pipeline


def reference_denoise(rgba, settings):
    if not settings['enabled'] or settings['weight'] == 0 or settings['luma'] == settings['chroma'] == 0:
        return rgba.copy()
    filtered = cv2.fastNlMeansDenoisingColored(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR), None,
        settings['luma'], settings['chroma'], 7, 21)
    output = rgba.copy()
    weight = settings['weight']
    output[..., :3] = np.rint(rgba[..., :3].astype(np.float32) * (1 - weight) +
        cv2.cvtColor(filtered, cv2.COLOR_BGR2RGB).astype(np.float32) * weight).astype(np.uint8)
    return output


def reference_frames(originals, settings):
    """Run each whole native pass separately, without the layered implementation."""
    if settings['overall_weight'] == 0:
        return [frame.copy() for frame in originals]
    height, width = originals[0].shape[:2]
    values = [reference_denoise(frame, settings['input_denoise']) for frame in originals]
    for layer in [settings] + ([settings['second_layer']] if settings['second_layer'] is not None else []):
        native = dlss_engine.Live(width, height, {key: layer[key] for key in dlss_layers.DEFAULTS})
        try:
            values = [native.process(frame, np.zeros((height, width, 2), np.float32),
                np.zeros((height, width), np.float32), reset=index == 0) for index, frame in enumerate(values)]
        finally:
            native.close()
    weight = settings['overall_weight']
    results = []
    for original, frame in zip(originals, values):
        filtered = reference_denoise(frame, settings['output_denoise'])
        result = np.rint(original.astype(np.float32) * (1 - weight) +
                         filtered.astype(np.float32) * weight).astype(np.uint8)
        result[..., 3] = original[..., 3]
        results.append(result)
    return results


def verify_denoise(app, directory, screenshots=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = app.root
    original_layers = [{key: variable.get() for key, variable in group.items()} for group in app.layer_vars]
    original_denoise = {key: {name: variable.get() for name, variable in group.items()}
                        for key, group in app.denoise_vars.items()}
    old_second, old_weight = app.v_second_enabled.get(), app.v_overall_weight.get()
    callback_errors = []
    previous_handler = root.report_callback_exception
    root.report_callback_exception = lambda *error: callback_errors.append(str(error))
    app.pause()
    app._close_live()
    app.current_is_image = False
    app.video = None
    app.view_var.set('原图')

    def settle():
        deadline = time.monotonic() + 180
        stable = None
        while time.monotonic() < deadline:
            root.update()
            assert not callback_errors, callback_errors
            if not app._busy and not (app.thread and app.thread.is_alive()) and app._ui_events.empty():
                stable = stable or time.monotonic()
                if time.monotonic() - stable > .2:
                    return
            else:
                stable = None
            time.sleep(.01)
        raise AssertionError('降噪界面任务超时：' + app.status.cget('text'))

    def refresh():
        app.on_settings_change()
        settle()
        assert app.image_dlss is not None, app.status.cget('text')
        assert not multiprocessing.active_children(), '图片任务完成后仍有模型进程'
        return app.image_dlss.copy()

    def compare(actual, expected):
        error = int(np.abs(actual.astype(int) - expected.astype(int)).max())
        assert error <= 1, ('降噪数值不一致', error)
        return error

    def export(fmt, channels):
        app.last_export = None
        app.v_export_format.set(fmt)
        app._update_export_options()
        for key, variable in app.v_export_channels.items():
            variable.set(int(key in channels))
        with patch('tkinter.filedialog.askdirectory', return_value=str(directory)), \
             patch('tkinter.messagebox.showwarning') as warning:
            app.export()
            assert not warning.called, warning.call_args
        settle()
        assert app.last_export, app.status.cget('text')
        return app.last_export

    try:
        root.deiconify()
        app.v_overall_weight.set(100)
        for variables in app.layer_vars:
            variables['guidance'].set('关闭')
        app.layer_vars[1]['preset'].set('Preset #2')
        for key, variables in app.denoise_vars.items():
            variables['enabled'].set(False)
            variables['luma'].set(9 if key == 'input_denoise' else 5)
            variables['chroma'].set(7 if key == 'input_denoise' else 4)
            variables['weight'].set(100)
        app.tabs.select(app.pages['参数'])
        for width, height in [(1240, 820), (1000, 720), (900, 720), (680, 520), (1600, 950)]:
            root.geometry(f'{width}x{height}+20+20')
            settle()
            body = app.pages['参数'].body
            def inspect(widget):
                for child in widget.winfo_children():
                    if child.winfo_viewable() and isinstance(child, (ttk.Checkbutton, ttk.Combobox, tk.Scale)):
                        left = child.winfo_rootx() - body.winfo_rootx()
                        assert left >= 0 and left + child.winfo_width() <= body.winfo_width() + 2
                    inspect(child)
            inspect(body)
        root.geometry('1240x820+20+20')
        yy, xx = np.indices((239, 317))
        clean = np.stack((50 + xx * .4, 60 + yy * .5, 100 + xx * .2), axis=-1).astype(np.uint8)
        cv2.putText(clean, 'DLSS 123', (25, 125), cv2.FONT_HERSHEY_SIMPLEX, .9, (230, 230, 230), 2)
        original = np.clip(clean.astype(float) + np.random.default_rng(42).normal(0, 12, clean.shape),
                           0, 255).astype(np.uint8)
        source = directory / '带噪点原图.png'
        pipeline.imwrite(str(source), original)
        app.current_is_image = True
        app.image_path = str(source)
        app.image_bgr = original
        app.image_dlss = None
        app._reset_image_editor()
        app._update_export_btn()
        rgba = cv2.cvtColor(cv2.copyMakeBorder(original, 0, 1, 1, 2, cv2.BORDER_REPLICATE), cv2.COLOR_BGR2RGBA)
        image_errors = []

        def check_image():
            actual = refresh()
            expected = reference_frames([rgba], dlss_layers.image_settings(app._collect_settings()))[0]
            image_errors.append(compare(actual, cv2.cvtColor(expected[:239, 1:318], cv2.COLOR_RGBA2BGR)))
            return actual

        for second in (False, True):
            app.v_second_enabled.set(second)
            for pre, post in ((False, False), (True, False), (False, True), (True, True)):
                for key, enabled in (('input_denoise', pre), ('output_denoise', post)):
                    if bool(app.denoise_vars[key]['enabled'].get()) != enabled:
                        app.denoise_checks[key].invoke()
                check_image()
                for key, group in app.denoise_vars.items():
                    state = 'normal' if group['enabled'].get() else 'disabled'
                    assert all(str(scale.cget('state')) == state for scale in app.denoise_scales[key])
        for pre, post in ((0, 100), (50, 100), (100, 100), (100, 0), (100, 50)):
            app.denoise_vars['input_denoise']['weight'].set(pre)
            app.denoise_vars['output_denoise']['weight'].set(post)
            check_image()
        app.v_overall_weight.set(0)
        np.testing.assert_array_equal(refresh(), original)
        app.v_overall_weight.set(65)
        app.denoise_vars['input_denoise']['weight'].set(50)
        weighted = check_image()
        assert app.denoise_vars['output_denoise']['luma'].get() == 5
        app.selection.data[40:190, 55:270] = 255
        app.selection.revision += 1
        app.v_mask_enabled.set(1)
        app.v_feather.set(5)
        masked = app._image_output().copy()
        np.testing.assert_array_equal(masked[:20], original[:20])
        app.view_var.set('DLSS')
        app.display_view()
        image_export = export('图片', ['original', 'dlss', 'mask'])
        saved = next(path for path in image_export['outputs'] if Path(path).stem.endswith('_dlss'))
        np.testing.assert_array_equal(pipeline.imread(saved), masked)
        raw_saved = next(path for path in image_export['outputs'] if Path(path).stem.endswith('_original'))
        np.testing.assert_array_equal(pipeline.imread(raw_saved), original)
        app.v_output_duration.set('.25')
        app.v_output_fps.set('12')
        static_export = export('视频', ['dlss'])
        assert pipeline.video_info(static_export['outputs'][0]) == (3, 12., 318, 240)
        if screenshots:
            import ctypes
            from PIL import ImageGrab
            for position, name in [(0, '前置降噪界面.png'), (1, '后置降噪界面.png')]:
                app.pages['参数'].canvas.yview_moveto(position)
                settle()
                ImageGrab.grab(window=ctypes.windll.user32.GetParent(root.winfo_id())).save(directory / name)

        app.current_is_image = False
        app.video = None
        app.view_var.set('原图')
        batch = directory / '批量图片'
        batch.mkdir(exist_ok=True)
        pipeline.imwrite(str(batch / '样本.png'), original)
        app._in_thread(lambda: app._do_images(str(batch), ['样本.png']))
        settle()
        np.testing.assert_array_equal(pipeline.imread(str(batch.with_name(batch.name + '_dlss') / '样本.png')), weighted)

        video = directory / '带噪点视频.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 12, (320, 240))
        assert writer.isOpened()
        try:
            for index in range(3):
                writer.write(cv2.resize(np.roll(original, index * 2, axis=1), (320, 240)))
        finally:
            writer.release()
        app.video = str(video)
        if getattr(app, '_cap', None):
            app._cap.release()
        app._cap = cv2.VideoCapture(str(video))
        app.nframes, app.fps, _, _ = pipeline.video_info(str(video))
        app.fslider.configure(to=app.nframes - 1)
        app.fslider.set(0)
        app._update_export_btn()
        settle()
        video_export = export('视频', ['dlss'])
        assert pipeline.video_info(video_export['outputs'][0])[:2] == (3, 12.)
        settings = app._collect_settings()
        frames = [cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA) for _, frame in pipeline.iter_frames(str(video))]
        expected_frames = reference_frames(frames, settings)
        result_dir = Path(pipeline.out_dirs(str(video))[2])
        video_errors, preview_errors = [], []
        for index, expected in enumerate(expected_frames):
            saved_frame = pipeline.imread(str(result_dir / f'{index:06d}.png'))
            video_errors.append(compare(saved_frame, cv2.cvtColor(expected, cv2.COLOR_RGBA2BGR)))
            preview_errors.append(compare(app._live_dlss_image(index), saved_frame))
        app._close_live()
        assert pipeline.dlss_cache_matches(str(video), settings)
        app.denoise_vars['output_denoise']['weight'].set(70)
        assert not pipeline.dlss_cache_matches(str(video), app._collect_settings())
        assert not multiprocessing.active_children()

        # Measure the actual filter on a deterministic 1080p frame, excluding DLSS.
        from image_denoise import apply_rgba
        hd = cv2.cvtColor(cv2.resize(original, (1920, 1080)), cv2.COLOR_BGR2RGBA)
        start = time.monotonic()
        filtered = apply_rgba(hd, settings['input_denoise'])
        pre_seconds = time.monotonic() - start
        start = time.monotonic()
        apply_rgba(filtered, settings['output_denoise'])
        post_seconds = time.monotonic() - start
        quality = cv2.cvtColor(reference_denoise(cv2.cvtColor(original, cv2.COLOR_BGR2RGBA),
            {**settings['input_denoise'], 'weight': 1}), cv2.COLOR_RGBA2BGR)
        pipeline.imwrite(str(directory / '降噪前后对比.png'), np.hstack((clean, original, quality)))
        return {'status': 'passed', 'image_reference_errors': image_errors,
                'video_reference_errors': video_errors, 'preview_errors': preview_errors,
                'weights_percent': [0, 50, 100], 'overall_zero_original': True,
                'masked_png': saved, 'static_video': static_export['outputs'][0],
                'batch_images': True, 'video': video_export['outputs'][0], 'cache_invalidated': True,
                'hd_filter_seconds': {'pre': round(pre_seconds, 3), 'post': round(post_seconds, 3)},
                'sample_mse': {'noisy': round(float(np.mean((original.astype(float) - clean) ** 2)), 3),
                               'denoised': round(float(np.mean((quality.astype(float) - clean) ** 2)), 3)}}
    finally:
        app._close_live()
        app.current_is_image = False
        app.video = None
        if getattr(app, '_cap', None):
            app._cap.release()
        for variables, values in zip(app.layer_vars, original_layers):
            for key, value in values.items():
                variables[key].set(value)
        for key, values in original_denoise.items():
            for name, value in values.items():
                app.denoise_vars[key][name].set(value)
        app.v_second_enabled.set(old_second)
        app.v_overall_weight.set(old_weight)
        root.report_callback_exception = previous_handler
