"""Verify the installed executable: runtime selection, worker processes and exports."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time



def verify(directory):
    import tkinter as tk
    import cv2
    import numpy as np
    import psutil
    import pipeline
    import dlss_runtime
    import runtime_session
    import gui
    from PIL import ImageGrab
    assert getattr(sys, 'frozen', False), '必须验证打包后的程序'
    bundle = Path(sys._MEIPASS).resolve()
    for module in (cv2, np, pipeline, dlss_runtime, runtime_session, gui):
        assert Path(module.__file__).resolve().is_relative_to(bundle), module.__file__
    for version in ('bundled', '310.8.SF-v2', '310.8.SF'):
        assert dlss_runtime.version_path(version).resolve().is_relative_to(Path(sys.executable).parent.resolve())
    folder = Path(directory).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_DATA_ROOT'] = str(folder / 'app-data')
    os.environ['DLSS5_LOG_DIR'] = str(folder)
    video = str(folder / '带音轨测试片.mp4')
    ffmpeg = pipeline._bundle_ffmpeg()
    subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x240:rate=12:duration=1', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', video], check=True)
    before = next(pipeline.iter_frames(video))[1]
    rgba = cv2.cvtColor(before, cv2.COLOR_BGR2RGBA)
    flow = np.zeros((240, 320, 2), np.float32)
    depth = np.zeros((240, 320), np.float32)
    results = {'frozen': True, 'executable': sys.executable, 'bundle': str(bundle), 'gpu': dlss_runtime.gpu_info(), 'videos': {}}
    live = runtime_session.Live(320, 240, {'runtime_version': 'bundled', 'guidance_mode': 0})
    old_pid = live._process.pid
    try:
        live.process(rgba, flow, depth)
        live.update({'runtime_version': '310.8.SF-v2'})
        assert live._process.pid != old_pid
        assert not psutil.pid_exists(old_pid)
        live.process(rgba, flow, depth)
        results['same_session_switch_restarts_worker'] = True
    finally:
        new_pid = live._process.pid if live._process else None
        live.close()
        assert new_pid is None or not psutil.pid_exists(new_pid)
    for version in ('bundled', '310.8.SF-v2', '310.8.SF'):
        settings = {'runtime_version': version, 'guidance_mode': 0}
        start = time.monotonic()
        count = pipeline.generate_dlss(video, settings)
        output = pipeline.export_video(video, 'dlss', fps=12, with_audio=True)
        assert count == 12 and pipeline.video_info(output)[:2] == (12, 12.0)
        subprocess.run([ffmpeg, '-v', 'error', '-i', output, '-map', '0:a:0', '-f', 'null', '-'], check=True)
        after = pipeline.imread(str(Path(pipeline.out_dirs(video)[2]) / '000000.png'))
        delta = float(np.abs(after.astype(float) - before).mean())
        assert delta > 0
        results['videos'][version] = {'frames': count, 'audio': True, 'mean_pixel_change': delta,
                                      'seconds': round(time.monotonic() - start, 3)}
        print('视频验证通过：', version, flush=True)
    # First pass compatible runtime, second pass original runtime.
    count = pipeline.generate_dlss(video, {'runtime_version': '310.8.SF-v2',
                                   'second_layer': {'runtime_version': 'bundled'}}, frame_limit=3)
    assert count == 3
    results['mixed_runtime_two_pass'] = True
    errors = []
    root = None
    app = None
    def pump(until_idle=False):
        deadline = time.monotonic() + (90 if until_idle else .2)
        while time.monotonic() < deadline:
            root.update()
            if until_idle and not app._busy and not (app.thread and app.thread.is_alive()):
                return
            time.sleep(.01)
        if until_idle:
            raise TimeoutError('界面任务未完成')
    def close():
        if app is not None:
            app._close_live()
        if root is not None:
            for identifier in root.tk.call('after', 'info'):
                root.after_cancel(identifier)
            root.destroy()
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        app.tabs.select(app.pages['参数'])
        app.layer_vars[0]['runtime_version'].set(dlss_runtime.LABELS['310.8.SF-v2'])
        app.layer_vars[1]['runtime_version'].set(dlss_runtime.LABELS['bundled'])
        app._runtime_changed()
        app.v_preset_name.set('30 系兼容模型测试')
        app._save_parameter_preset(new=True)
        app._test_runtime(0)
        pump(until_idle=True)
        assert '模型自检通过' in app._task_status, app._task_status
        assert app._collect_settings()['runtime_version'] == '310.8.SF-v2'
        app.pages['参数'].canvas.yview_moveto(.24)
        root.geometry('1280x900')
        pump()
        ImageGrab.grab(window=int(root.frame(), 16)).save(folder / '模型版本选择.png')
        close()
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        pump()
        assert app._collect_settings()['runtime_version'] == '310.8.SF-v2'
        assert app._read_single_layer(app.layer_vars[1])['runtime_version'] == 'bundled'
        assert not errors, errors
        results['gui_model_selection_selftest_and_restart'] = True
    finally:
        close()
    results['status'] = 'passed'
    results['screenshot'] = str(folder / '模型版本选择.png')
    destination = folder / 'verification.json'
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
