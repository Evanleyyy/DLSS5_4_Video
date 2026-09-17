"""Verify two-layer controls, weighted images, batch processing and temporal video."""
from pathlib import Path
import multiprocessing
import time
from unittest.mock import patch

import cv2
import numpy as np
from tkinter import ttk

import dlss_engine
import dlss_layers
import pipeline


def verify_layers(app, directory, video, screenshots=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = app.root
    errors = []
    previous_handler = root.report_callback_exception
    root.report_callback_exception = lambda *error: errors.append(str(error))
    original_vars = [{key: value.get() for key, value in variables.items()} for variables in app.layer_vars]
    old_enabled, old_weight = app.v_second_enabled.get(), app.v_overall_weight.get()
    app._close_live()
    app.pause()
    app.current_is_image = False
    app.video = None
    app.view_var.set('原图')

    def settle():
        deadline = time.monotonic() + 120
        stable = None
        while time.monotonic() < deadline:
            root.update()
            assert not errors, errors
            if not app._busy and not (app.thread and app.thread.is_alive()) and app._ui_events.empty():
                stable = stable or time.monotonic()
                if time.monotonic() - stable > .2:
                    return
            else:
                stable = None
            time.sleep(.01)
        raise AssertionError('双层 DLSS 界面任务超时：' + app.status.cget('text'))

    def refresh():
        app.on_settings_change()
        app.confirm_processing()
        settle()
        assert app.image_dlss is not None, app.status.cget('text')
        assert not multiprocessing.active_children(), '图片任务完成后仍有第二层进程'
        return app.image_dlss.copy()

    def export(fmt, channels):
        app.last_export = None
        app.v_export_format.set(fmt)
        app._update_export_options()
        for name, variable in app.v_export_channels.items():
            variable.set(int(name in channels))
        with patch('tkinter.filedialog.askdirectory', return_value=str(directory)), \
             patch('tkinter.messagebox.showwarning') as warning:
            app.export()
            assert not warning.called, warning.call_args
        settle()
        assert app.last_export, app.status.cget('text')
        return app.last_export

    try:
        root.deiconify()
        app.layer_vars[0]['localTone'].set(1.3)
        app.layer_vars[1]['preset'].set('Preset #2')
        app.layer_vars[1]['style'].set('电影')
        app.layer_vars[1]['localStruct'].set(1.7)
        app.v_second_enabled.set(True)
        app.v_overall_weight.set(100)
        app.tabs.select(app.pages['参数'])
        # Inspect both parameter sets at each layout breakpoint.
        import tkinter as tk
        for width, height in [(1240, 820), (1000, 720), (900, 720), (680, 520), (1600, 950)]:
            root.geometry(f'{width}x{height}+20+20')
            for layer in ('第一层 DLSS', '第二层 DLSS'):
                app.v_edit_layer.set(layer)
                app.layer_selector.event_generate('<<ComboboxSelected>>')
                settle()
                body = app.pages['参数'].body
                def inspect(widget):
                    for child in widget.winfo_children():
                        if not child.winfo_viewable():
                            continue
                        if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Combobox, tk.Scale)):
                            left = child.winfo_rootx() - body.winfo_rootx()
                            assert left >= 0 and left + child.winfo_width() <= body.winfo_width() + 2
                        inspect(child)
                inspect(body)
        root.geometry('1240x820+20+20')
        first_before = app._collect_settings()['preset']
        app.layer_vars[1]['preset'].set('Preset #3')
        assert app._collect_settings()['preset'] == first_before
        app.layer_vars[1]['preset'].set('Preset #2')
        settle()
        yy, xx = np.indices((239, 317))
        original = np.stack(((xx * 3 + yy) % 256, (yy * 2 + xx) % 256,
                             (xx // 16 % 2) * 100 + 60), axis=-1).astype(np.uint8)
        source_image = directory / '原始图片.png'
        pipeline.imwrite(str(source_image), original)
        app.current_is_image = True
        app.image_path = str(source_image)
        app.image_bgr = original
        app.image_dlss = None
        app._reset_image_editor()
        app._update_export_btn()
        full = refresh()
        app.v_overall_weight.set(35)
        weighted = refresh()
        expected = np.rint(original.astype(np.float32) * .65 + full.astype(np.float32) * .35).astype(np.uint8)
        assert np.abs(weighted.astype(int) - expected.astype(int)).max() <= 1
        app.v_overall_weight.set(0)
        np.testing.assert_array_equal(refresh(), original)
        app.v_overall_weight.set(100)
        app.second_layer_check.invoke()
        single = refresh()
        assert not app.v_second_enabled.get() and np.any(single != full)
        app.second_layer_check.invoke()
        assert app.layer_vars[1]['preset'].get() == 'Preset #2'
        app.v_overall_weight.set(35)
        weighted = refresh()
        app.selection.data[50:180, 60:260] = 255
        app.selection.revision += 1
        app.v_mask_enabled.set(1)
        app.v_feather.set(4)
        app.confirm_processing()
        settle()
        masked = app._image_output().copy()
        app.view_var.set('DLSS')
        app.display_view()
        if screenshots:
            from PIL import ImageGrab
            import ctypes
            settle()
            handle = ctypes.windll.user32.GetParent(root.winfo_id())
            ImageGrab.grab(window=handle).save(directory / '双层参数界面.png')
        image_export = export('图片', ['original', 'dlss', 'mask'])
        saved = [path for path in image_export['outputs'] if Path(path).stem.endswith('_dlss')][0]
        np.testing.assert_array_equal(pipeline.imread(saved), masked)
        app.v_output_duration.set('.25')
        app.v_output_fps.set('12')
        static_export = export('视频', ['dlss'])
        assert pipeline.video_info(static_export['outputs'][0]) == (3, 12., 318, 240)

        app.current_is_image = False
        app.video = None
        app.view_var.set('原图')
        batch = directory / '批量图片'
        batch.mkdir(exist_ok=True)
        pipeline.imwrite(str(batch / '第一张.png'), original)
        pipeline.imwrite(str(batch / '第二张.png'), cv2.resize(original, (320, 240)))
        app._in_thread(lambda: app._do_images(str(batch), ['第一张.png', '第二张.png']))
        settle()
        batch_image = pipeline.imread(str(batch.with_name(batch.name + '_dlss') / '第一张.png'))
        assert batch_image is not None
        assert np.abs(batch_image.astype(int) - weighted.astype(int)).max() <= 1

        app._close_live()
        app.video = str(video)
        if getattr(app, '_cap', None):
            app._cap.release()
        app._cap = cv2.VideoCapture(str(video))
        app.nframes, app.fps, width, height = pipeline.video_info(str(video))
        app.fslider.configure(to=max(1, app.nframes - 1))
        app.fslider.set(0)
        app.layer_vars[0]['guidance'].set('关闭')
        app.layer_vars[1]['guidance'].set('深度+光流')
        app.v_overall_weight.set(60)
        app._update_export_btn()
        settle()
        settings = app._collect_settings()
        with patch.object(pipeline, 'generate_depth', wraps=pipeline.generate_depth) as generate_depth, \
             patch.object(pipeline, 'generate_flow', wraps=pipeline.generate_flow) as generate_flow:
            app.confirm_processing()
            settle()
            video_export = export('视频', ['dlss'])
            assert generate_depth.called and generate_flow.called, '必须读取第二层的引导需要'
        assert pipeline.video_info(video_export['outputs'][0])[:2] == (app.nframes, app.fps)
        # Compare every cached video frame to two whole native passes with separate histories.
        originals = [cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA) for _, frame in pipeline.iter_frames(str(video))]
        values = originals
        depth_dir, flow_dir, result_dir = pipeline.out_dirs(str(video))
        for setting in ({key: settings[key] for key in dlss_layers.DEFAULTS}, settings['second_layer']):
            live = dlss_engine.Live(width, height, setting)
            try:
                values = [live.process(frame, pipeline.read_flo(str(Path(flow_dir) / f'{index:06d}.flo')),
                          pipeline.read_depth(pipeline.find_depth(depth_dir, index)), reset=index == 0)
                          for index, frame in enumerate(values)]
            finally:
                live.close()
        max_error = 0
        for index, (source, value) in enumerate(zip(originals, values)):
            reference = dlss_layers.blend_result(source, value, .6)[..., :3][..., ::-1]
            actual = pipeline.imread(str(Path(result_dir) / f'{index:06d}.png'))
            max_error = max(max_error, int(np.abs(actual.astype(int) - reference.astype(int)).max()))
        assert max_error <= 1, ('双层视频连续帧与两次完整处理不一致', max_error)
        assert pipeline.dlss_cache_matches(str(video), settings)
        assert not multiprocessing.active_children()
        return {'status': 'passed', 'independent_parameters': True, 'overall_weights': [0, 35, 60, 100],
                'masked_png': saved, 'static_video': static_export['outputs'][0],
                'batch_images': True, 'video': video_export['outputs'][0],
                'temporal_reference_max_error': max_error, 'second_layer_guidance': True}
    finally:
        app._close_live()
        app.current_is_image = False
        app.video = None
        for variables, values in zip(app.layer_vars, original_vars):
            for name, value in values.items():
                variables[name].set(value)
        app.v_second_enabled.set(old_enabled)
        app.v_overall_weight.set(old_weight)
        app.v_edit_layer.set('第一层 DLSS')
        app._show_selected_layer()
        root.report_callback_exception = previous_handler
