"""Real application and GPU: automatic still/video preview and full-video export."""
import json
import os
from pathlib import Path
import sys
import time


def verify(directory):
    import cv2
    import numpy as np
    import psutil
    import tkinter as tk
    from unittest.mock import patch
    import dlss_runtime
    import gui
    import pipeline
    import sr_settings
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_DATA_ROOT'] = str(directory / 'data')
    os.environ['DLSS5_LOG_DIR'] = str(directory)
    report, errors, pids = {'status': 'running', 'frozen': bool(getattr(sys, 'frozen', False))}, [], set()
    source = np.zeros((128, 128, 3), np.uint8)
    source[..., 0] = np.arange(128, dtype=np.uint8) * 2
    source[..., 1] = source[..., 0].T
    source[..., 2] = 70
    cv2.putText(source, 'LIVE', (18, 65), cv2.FONT_HERSHEY_SIMPLEX, .8, (240, 220, 250), 2)
    picture, video = directory / 'input.png', directory / 'video.avi'
    pipeline.imwrite(str(picture), source)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 6, (128, 128))
    assert writer.isOpened()
    for index in range(6):
        writer.write(np.roll(source, index * 4, axis=1))
    writer.release()
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
    app = gui.App(root)
    def idle(timeout=180):
        started = time.monotonic()
        while True:
            root.update()
            assert not errors, errors
            if not app._busy and not (app.thread and app.thread.is_alive()) and app._live_debounce is None and not app._auto_pending:
                assert not getattr(app, '_last_task_failed', False), app._task_status
                return round(time.monotonic() - started, 3)
            assert time.monotonic() - started < timeout, app._task_status
            time.sleep(.01)
    def pid():
        value = app._model_sessions.dlss.first._process.pid
        pids.add(value)
        return value
    try:
        with patch('gui.filedialog.askopenfilename', return_value=str(picture)):
            app.import_image()
        first_seconds = idle()
        assert app._confirmed_image is not None
        assert not hasattr(app, 'confirm_process_btn')
        first_pid = pid()
        before = app._confirmed_image.copy()
        for value in (.2, .4, .6):
            app.layer_vars[0]['intensity'].set(value)
            root.update()
        warm_seconds = idle()
        assert pid() == first_pid
        assert np.any(before != app._confirmed_image)
        report['image'] = dict(first_seconds=first_seconds, changed_seconds=warm_seconds, reused_pid=first_pid)
        for version in ('310.8.SF-v2', '310.8.SF'):
            old = pid()
            app.layer_vars[0]['runtime_version'].set(dlss_runtime.LABELS[version])
            idle()
            assert pid() != old and not psutil.pid_exists(old)
            same = pid()
            app.layer_vars[0]['intensity'].set(.3 if version.endswith('v2') else .5)
            idle()
            assert pid() == same
        app.v_color_preservation.set(65)
        app.v_mask_enabled.set(1)
        app.selection.replace(0)
        app.on_settings_change()
        idle()
        assert np.array_equal(app._confirmed_image[..., :3], app.image_bgr)
        report['runtime_switch_and_mask'] = True

        with patch('gui.filedialog.askopenfilename', return_value=str(video)):
            app.import_video()
        idle()
        assert app._video_preview_frame[1] == 0
        assert not Path(pipeline.out_dirs(str(video))[2]).exists(), '预览写入了整段缓存'
        video_pid = pid()
        app.layer_vars[0]['intensity'].set(.8)
        app.fslider.set(3)
        app.on_frame()
        idle()
        assert app._video_preview_frame[1] == 3 and pid() == video_pid
        assert not Path(pipeline.out_dirs(str(video))[2]).exists()
        report['video_preview_only_current_frame'] = True

        app.layer_vars[0]['guidance'].set('深度+光流')
        idle()
        guided_pid = pid()
        app.layer_vars[0]['intensity'].set(.7)
        idle()
        assert pid() == guided_pid, '同帧调参重复加载引导或渲染模型'
        assert not Path(pipeline.out_dirs(str(video))[0]).exists(), '预览生成了整段引导缓存'
        app.layer_vars[0]['guidance'].set('关闭')
        idle()
        video_pid = pid()
        report['single_frame_guidance_reused'] = True

        request = app._export_request(str(directory))
        assert request['allow_generate']
        app._in_thread(lambda: app._run_export_request(request))
        idle()
        assert app.last_export and pipeline.video_info(app.last_export['outputs'][0])[:2] == (6, 6.)
        assert len(list(Path(pipeline.out_dirs(str(video))[2]).glob('*.png'))) == 6
        assert pid() == video_pid, '整段导出重复加载同一模型'
        report['video_export_6_frames_and_reuse'] = True

        # SeedVR2's automatic preview must stay on the selected frame too.
        app.v_sr_engine.set(sr_settings.ENGINES['seedvr2'])
        app._sr_engine_changed()
        idle()
        sr_pid = app._model_sessions.sr.process.pid
        pids.add(sr_pid)
        assert app._video_preview_frame[3].shape == (256, 256, 3)
        app.sr_vars['seed'].set(43)
        idle()
        assert app._model_sessions.sr.process.pid == sr_pid
        assert not psutil.pid_exists(video_pid)
        request = app._export_request(str(directory))
        app._in_thread(lambda: app._run_export_request(request))
        idle()
        assert pipeline.video_info(app.last_export['outputs'][0]) == (6, 6., 256, 256)
        assert app._model_sessions.sr.process.pid == sr_pid
        report['seed_preview_and_temporal_export'] = True
        root.deiconify()
        root.geometry('1240x820')
        app.tabs.select(app.pages['参数'])
        root.update()
        from PIL import ImageGrab
        ImageGrab.grab(window=int(root.frame(), 16)).save(directory / '即时渲染界面.png')
        report['status'] = 'passed'
    except BaseException:
        import traceback
        report.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        app._auto_enabled = False
        app.pause()
        if app.thread:
            app.thread.join(5)
        app._close_live()
        if getattr(app, '_cap', None):
            app._cap.release()
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(psutil.pid_exists(value) for value in pids):
            time.sleep(.05)
        report['remaining_workers'] = [value for value in pids if psutil.pid_exists(value)]
        if report['remaining_workers']:
            report['status'] = 'failed'
        (directory / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not report['remaining_workers']
    return report


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source/dlss5standaloneV2'))
    verify(sys.argv[1])
