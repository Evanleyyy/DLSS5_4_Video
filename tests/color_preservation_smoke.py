"""Real DLSS rendering, GUI slider/masks, preset restart and exported pixels."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import cv2
    import numpy as np
    import tkinter as tk
    from unittest.mock import patch
    from PIL import ImageGrab
    import color_preservation
    import dlss_layers
    import gui
    import pipeline
    import media_export

    directory = ROOT / 'logs' / ('color-preservation-' + str(time.time_ns()))
    directory.mkdir(parents=True)
    os.environ['DLSS5_DATA_ROOT'] = str(directory / 'app-data')
    os.environ['DLSS5_LOG_DIR'] = str(directory)
    source = str(directory / '带音轨测试片.mp4')
    subprocess.run([pipeline._bundle_ffmpeg(), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x240:rate=6:duration=1', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', source], check=True)
    original = next(pipeline.iter_frames(source))[1]
    rgba = cv2.cvtColor(original, cv2.COLOR_BGR2RGBA)
    flow, depth = np.zeros((240, 320, 2), np.float32), np.zeros((240, 320), np.float32)
    report = {'runtimes': {}}
    for version in ('bundled', '310.8.SF-v2', '310.8.SF'):
        live = dlss_layers.LayeredLive(320, 240, {'runtime_version': version})
        try:
            raw = live.process(rgba, flow, depth, reset=True)
            live.update({'runtime_version': version, 'color_preservation': {'strength': 1}})
            corrected = live.process(rgba, flow, depth, reset=True)
        finally:
            live.close()
        def low(array):
            return cv2.GaussianBlur(array[..., :3].astype(np.float32), (0, 0), 6)
        before = float(np.abs(low(raw) - low(rgba)).mean())
        after = float(np.abs(low(corrected) - low(rgba)).mean())
        def detail(array):
            rgb = array[..., :3].astype(np.float32)
            return (rgb - cv2.GaussianBlur(rgb, (0, 0), 1))[8:-8, 8:-8].ravel()
        correlation = float(np.corrcoef(detail(raw), detail(corrected))[0, 1])
        assert after < before * .6, (version, before, after)
        assert correlation > .8, (version, correlation)
        report['runtimes'][version] = {'low_frequency_error_before': before,
                                      'low_frequency_error_after': after,
                                      'detail_correlation': correlation}
        pipeline.imwrite(str(directory / (version + '-模型.png')), cv2.cvtColor(raw, cv2.COLOR_RGBA2BGR))
        pipeline.imwrite(str(directory / (version + '-保留原色.png')), cv2.cvtColor(corrected, cv2.COLOR_RGBA2BGR))
        print('颜色与细节验证通过：', version, report['runtimes'][version], flush=True)
    pipeline.imwrite(str(directory / '原素材.png'), original)
    settings = {'runtime_version': '310.8.SF-v2', 'second_layer': {'runtime_version': 'bundled'},
                'color_preservation': {'strength': .8, 'mask_scope': 'color'}}
    assert pipeline.generate_dlss(source, settings) == 6
    assert pipeline.dlss_cache_matches(source, settings)
    assert not pipeline.dlss_cache_matches(source, {**settings, 'color_preservation': {'strength': .2}})
    video = pipeline.export_video(source, 'dlss', fps=6, with_audio=True)
    subprocess.run([pipeline._bundle_ffmpeg(), '-v', 'error', '-i', video, '-map', '0:a:0', '-f', 'null', '-'], check=True)
    report['two_layer_video_audio_and_cache'] = True

    root = None
    app = None
    errors = []
    def pump(seconds=.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.005)
    def idle():
        end = time.monotonic() + 90
        while time.monotonic() < end:
            pump(.03)
            if not app._busy and not (app.thread and app.thread.is_alive()):
                assert app._confirmed_settings is not None
                return
        raise TimeoutError('单图生成未完成')
    def start():
        nonlocal root, app
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        pump()
    def close():
        if app is not None:
            app._close_live()
        if root is not None:
            for identifier in root.tk.call('after', 'info'):
                root.after_cancel(identifier)
            root.destroy()
    try:
        start()
        image_path = str(directory / '原素材.png')
        with patch('gui.filedialog.askopenfilename', return_value=image_path):
            app.import_image()
        pump()
        assert app.image_dlss is None and not app._busy
        app.confirm_processing()
        idle()
        model = app.image_dlss.copy()
        # Edits are drafts; explicit confirmation reuses the native model result.
        with patch.object(app, '_image_dlss', side_effect=AssertionError('颜色滑块不应重新推理')):
            app.v_color_preservation.set(100)
            app._color_preservation_changed()
            pump()
            app.selection.data[:, :160] = 255
            app.selection.revision += 1
            app.v_mask_enabled.set(1)
            app.v_mask_overlay.set(0)
            app.v_feather.set(0)
            app.v_color_mask_scope.set(color_preservation.MASK_SCOPES['color'])
            app._color_mask_scope_changed()
            pump()
            app.confirm_processing()
            idle()
            output = app._image_output()
            full = color_preservation.apply(original, model, 1)
            np.testing.assert_array_equal(output[:, :160], full[:, :160])
            np.testing.assert_array_equal(output[:, 160:], model[:, 160:])
            app.v_color_mask_scope.set(color_preservation.MASK_SCOPES['result'])
            app._color_mask_scope_changed()
            pump()
            app.confirm_processing()
            idle()
            np.testing.assert_array_equal(app._image_output()[:, 160:], original[:, 160:])
            app.v_color_mask_scope.set(color_preservation.MASK_SCOPES['color'])
            app.v_mask_mode.set('保护涂抹区域')
            app.v_feather.set(14)
            app._schedule_image_render()
            pump()
            app.confirm_processing()
            idle()
            expected = app._image_output().copy()
            app.v_export_channels['dlss'].set(1)
            app.v_export_channels['original'].set(0)
            (directory / 'export').mkdir()
            request = app._export_request(str(directory / 'export'))
            media_export.export_channels(request, app._collect_settings())
            exported = list((directory / 'export').rglob('*_dlss.png'))
            if not exported:
                exported = list((directory / 'export').rglob('*.png'))
            assert exported, '未生成导出图片'
            assert any(np.array_equal(pipeline.imread(str(path)), expected) for path in exported)
            np.testing.assert_array_equal(app.image_dlss, model)
            report['gui_slider_both_masks_inverse_feather_export'] = True
        app.v_preset_name.set('DLSS 原色彩保留验证')
        app._save_parameter_preset(new=True)
        assert app._preset_store.load()['presets'][0]['parameters']['settings']['color_preservation'] == {
            'strength': 1.0, 'mask_scope': 'color'}
        app._set_busy(True)
        assert str(app.color_preservation_scale.cget('state')) == 'disabled'
        assert str(app.color_mask_selector.cget('state')) == 'disabled'
        app._set_busy(False)
        app.tabs.select(app.pages['参数'])
        for width, height in ((1280, 900), (680, 520)):
            root.geometry(f'{width}x{height}')
            pump(.3)
            parent = app.color_preservation_scale.master
            assert app.color_preservation_scale.winfo_width() > 30
            assert app.color_preservation_scale.winfo_x() + app.color_preservation_scale.winfo_width() <= parent.winfo_width()
        root.geometry('1280x900')
        app.pages['参数'].canvas.yview_moveto(.19)
        pump(.3)
        ImageGrab.grab(window=int(root.frame(), 16)).save(directory / '原色彩保留界面.png')
        app.tabs.select(app.pages['遮罩'])
        pump(.2)
        ImageGrab.grab(window=int(root.frame(), 16)).save(directory / '遮罩作用界面.png')
        with patch('gui.filedialog.askopenfilename', return_value=source):
            app.import_video()
        pump()
        assert app._confirmed_settings is None and not app._busy
        app.confirm_processing()
        idle()
        assert app._confirmed_source == ('video', source)
        app._require_confirmed_result()
        app.v_export_format.set('视频')
        request = app._export_request(str(directory / 'export'))
        exported_video = media_export.export_channels(request, app._collect_settings())
        assert pipeline.video_info(exported_video['outputs'][0])[:2] == (6, 6.0)
        report['confirmed_gui_video_and_export'] = True
        close()
        start()
        assert app.v_color_preservation.get() == 100
        assert app.v_color_mask_scope.get() == color_preservation.MASK_SCOPES['color']
        report['preset_restart_busy_guards_and_layout'] = True
        assert not errors, errors
    finally:
        close()
    report['status'] = 'passed'
    report['directory'] = str(directory)
    (directory / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'logs/color-preservation-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
