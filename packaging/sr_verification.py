"""Integration checks shared by source runs and the installed application."""
import json
import os
from pathlib import Path
import time


def verify(directory, screenshots=False):
    import cv2
    import numpy as np
    import tkinter as tk
    import gui
    import pipeline
    import media_export
    import sr_settings
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = gui.App(root)
    app._auto_enabled = False  # This specialized check schedules explicit jobs.
    # Tests explicitly run jobs; deferred slider callbacks must not invalidate them.
    def settle():
        for _ in range(5):
            root.update()
            time.sleep(.03)
        if app._live_debounce:
            root.after_cancel(app._live_debounce)
            app._live_debounce = None
    results = {'status': 'running', 'engines': {}}
    try:
        image = np.zeros((121, 159, 3), np.uint8)
        image[..., 0] = np.linspace(20, 180, 159, dtype=np.uint8)
        image[..., 1] = np.linspace(30, 215, 121, dtype=np.uint8)[:, None]
        image[..., 2] = 65
        cv2.putText(image, 'SR 2026', (13, 65), cv2.FONT_HERSHEY_SIMPLEX, .63, (250, 245, 235), 1, cv2.LINE_AA)
        rng = np.random.default_rng(12)
        image = np.clip(image.astype(float) + rng.normal(0, 4, image.shape), 0, 255).astype(np.uint8)
        picture = directory / '透明测试.png'
        alpha = np.tile(np.linspace(0, 255, 159, dtype=np.uint8), (121, 1))
        pipeline.imwrite(str(picture), np.dstack([image, alpha]))
        app.current_is_image = True
        app.image_path = str(picture)
        app.image_bgr = image
        app.image_alpha = alpha
        app.nframes, app.fps = 1, 6
        app._reset_image_editor()
        app._update_export_btn()
        app.sr_vars['tile'].set(256)
        for key in ('input_denoise', 'output_denoise'):
            app.denoise_vars[key]['enabled'].set(True)
            app.denoise_vars[key]['weight'].set(50)
        for engine in ('pisa', 'seedvr2', 'vosr'):
            app.v_sr_engine.set(sr_settings.ENGINES[engine])
            app._sr_engine_changed()
            settle()
            started = time.monotonic()
            output = app._image_dlss(image)
            assert output.shape == (242, 318, 3), output.shape
            app.image_dlss = output
            app.selection.replace(0)
            app.selection.paint((28, 60), (120, 60), 40)
            app.v_mask_enabled.set(1)
            app.v_feather.set(8)
            result = app._image_output()
            expected_alpha = cv2.resize(alpha, (318, 242), interpolation=cv2.INTER_LINEAR)
            assert result.shape == (242, 318, 4)
            assert np.array_equal(result[..., 3], expected_alpha)
            original = cv2.resize(image, (318, 242), interpolation=cv2.INTER_CUBIC)
            assert np.array_equal(result[0, 0, :3], original[0, 0])
            assert np.any(result[121, 159, :3] != original[121, 159])
            for key, variable in app.v_export_channels.items():
                variable.set(key in ('dlss', 'mask', 'original'))
            app.v_export_format.set('图片')
            exported = media_export.export_channels(app._export_request(str(directory)), app._collect_settings())
            png = Path(exported['directory']) / (picture.stem + '_dlss.png')
            assert np.array_equal(pipeline.imread(str(png), cv2.IMREAD_UNCHANGED), result)
            app.v_export_format.set('视频')
            app.v_output_duration.set('0.5')
            app.v_output_fps.set('6')
            for key, variable in app.v_export_channels.items():
                variable.set(key == 'dlss')
            exported_video = media_export.export_channels(app._export_request(str(directory)), app._collect_settings())
            assert pipeline.video_info(exported_video['outputs'][0]) == (3, 6, 318, 242)
            pipeline.imwrite(str(directory / (engine + '-comparison.png')), np.concatenate([original, output], axis=1))
            results['engines'][engine] = {'seconds': round(time.monotonic() - started, 3), 'size': [318, 242],
                'mask_alpha_png_video': True}
            app.tabs.select(app.pages['超分'])
            for width, height in ((680, 520), (960, 720), (1400, 900)):
                root.geometry(f'{width}x{height}')
                root.update()
                assert app.sr_selector.winfo_width() > 80
                page = app.pages['超分']
                assert abs(page.body.winfo_width() - page.canvas.winfo_width()) <= 2
            if screenshots:
                import ctypes
                from PIL import ImageGrab
                handle = ctypes.windll.user32.GetParent(root.winfo_id())
                ImageGrab.grab(window=handle).save(directory / (engine + '-界面.png'))
        # PiSA's native two controls must affect the result independently.
        app.v_sr_engine.set(sr_settings.ENGINES['pisa'])
        app._sr_engine_changed()
        settle()
        app.sr_vars['pisa_pixel'].set(.5)
        app.sr_vars['pisa_semantic'].set(.5)
        a = app._image_dlss(image)
        app.sr_vars['pisa_pixel'].set(1)
        b = app._image_dlss(image)
        app.sr_vars['pisa_semantic'].set(1)
        c = app._image_dlss(image)
        assert np.any(a != b) and np.any(b != c)
        app.v_overall_weight.set(50)
        half = app._image_dlss(image)
        expected_half = np.rint(cv2.resize(image, (318, 242), interpolation=cv2.INTER_CUBIC).astype(float) * .5 + c.astype(float) * .5).astype(np.uint8)
        assert np.max(np.abs(half.astype(float) - expected_half.astype(float))) <= 1
        app.v_overall_weight.set(0)
        zero = app._image_dlss(image)
        assert np.array_equal(zero, cv2.resize(image, (318, 242), interpolation=cv2.INTER_CUBIC))
        results['independent_pisa_parameters'] = True
        results['zero_weight'] = True
        results['half_weight'] = True
        results['status'] = 'passed'
    except Exception:
        import traceback
        results.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        app._close_live()
        root.destroy()
        (directory / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results
