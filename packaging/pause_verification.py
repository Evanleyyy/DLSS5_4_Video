"""Exercise GUI pause/resume and real model subprocesses without desktop capture."""
import json
import os
from pathlib import Path
import threading
import time


def verify(directory):
    import cv2
    import numpy as np
    import tkinter as tk
    import gui
    import pipeline
    import sr_settings
    import task_control
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_DATA_ROOT'] = str(directory / 'app-data')
    results = {'status': 'running', 'models': {}}
    root = tk.Tk()
    app = gui.App(root)
    app._auto_enabled = False  # This specialized check schedules explicit jobs.

    def pump(predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while not predicate():
            root.update()
            if time.monotonic() > deadline:
                raise AssertionError('等待任务状态超时：' + str(app.status.cget('text')))
            time.sleep(.01)
        root.update()

    def wait_paused():
        pump(lambda: app._task_control is not None and app._task_control.state == 'paused'
             and str(app.status.cget('text')).startswith('已暂停'))
        assert str(app.task_pause_btn.cget('state')) == 'normal'
        assert app.task_pause_btn.cget('text') == '继续生成'
        assert str(app.status.cget('text')).startswith('已暂停')
        value = app.pbar['value']
        until = time.monotonic() + .4
        while time.monotonic() < until:
            root.update()
            time.sleep(.01)
        assert app.pbar['value'] == value and app.thread.is_alive()

    try:
        assert str(app.task_pause_btn.cget('state')) == 'disabled'
        values = []
        started, gate = threading.Event(), threading.Event()
        def work():
            started.set()
            gate.wait(5)
            for index in range(4):
                task_control.checkpoint()
                values.append(index)
                app.set_progress(index + 1, 4, '验证生成')
        app._in_thread(work)
        pump(started.is_set)
        app.toggle_generation_pause()
        app.toggle_generation_pause()
        app.toggle_generation_pause()
        gate.set()
        wait_paused()
        assert values == []
        for width, height in ((680, 520), (1240, 820)):
            root.geometry(f'{width}x{height}')
            root.update()
            assert app.task_pause_btn.winfo_width() > 80
        app.toggle_generation_pause()
        pump(lambda: not app._busy)
        assert values == [0, 1, 2, 3]
        assert str(app.task_pause_btn.cget('state')) == 'disabled'
        results['gui_sequence_and_layout'] = True

        def failure():
            raise RuntimeError('预期的失败恢复验证')
        app._in_thread(failure)
        pump(lambda: not app._busy)
        assert str(app.task_pause_btn.cget('state')) == 'disabled'
        app._in_thread(lambda: None, pausable=False)
        assert app._task_control is None and str(app.task_pause_btn.cget('state')) == 'disabled'
        pump(lambda: not app._busy)
        results['error_and_non_generation_tasks'] = True

        random = np.random.default_rng(42)
        picture = random.integers(0, 256, (192, 256, 3), dtype=np.uint8)
        app.current_is_image = True
        app.image_bgr = picture
        app.image_path = str(directory / '输入.png')
        pipeline.imwrite(app.image_path, picture)
        app._reset_image_editor()
        app._update_export_btn()
        for engine in ('pisa', 'seedvr2', 'vosr'):
            app.v_sr_engine.set(sr_settings.ENGINES[engine])
            app._sr_engine_changed()
            app.sr_vars['tile'].set(256)
            app.sr_vars['scale'].set(2)
            for _ in range(8):
                root.update()
                time.sleep(.05)
            app.image_dlss = None
            started_at = time.monotonic()
            app.run_worker('dlss')
            control = app._task_control
            app.toggle_generation_pause()
            wait_paused()  # An early request also works before the subprocess is started.
            app.toggle_generation_pause()
            pump(lambda: app._busy and '正在超分当前图片' in app._task_status)
            app.toggle_generation_pause()
            wait_paused()  # Actual GPU work acknowledges a safe boundary.
            app.toggle_generation_pause()
            pump(lambda: not app._busy)
            assert control.state == 'finished'
            assert app.image_dlss is not None and app.image_dlss.shape == (384, 512, 3)
            reference = app.image_dlss.copy()
            # Fixed seeds and unchanged settings must survive pause/resume exactly.
            app.run_worker('dlss')
            pump(lambda: not app._busy)
            assert np.array_equal(reference, app.image_dlss), engine
            pipeline.imwrite(str(directory / (engine + '-结果.png')), app.image_dlss)
            results['models'][engine] = {'pause_resume': True, 'identical_to_unpaused': True,
                'seconds': round(time.monotonic() - started_at, 3)}

        # Cross-chunk SeedVR2 video keeps its temporal state and every output frame.
        video = directory / '连续帧.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 6, (96, 64))
        assert writer.isOpened()
        for index in range(11):
            frame = np.full((64, 96, 3), 40, np.uint8)
            cv2.rectangle(frame, (index * 4, 12), (index * 4 + 25, 42), (90, 160, 220), -1)
            writer.write(frame)
        writer.release()
        app._close_live()
        app.current_is_image = False
        app.video = str(video)
        app.nframes, app.fps = 11, 6
        app.v_sr_engine.set(sr_settings.ENGINES['seedvr2'])
        app._sr_engine_changed()
        app._update_export_btn()
        for _ in range(8):
            root.update()
            time.sleep(.05)
        app.pbar.configure(value=0)
        app.run_worker('dlss')
        pump(lambda: app._busy and app.pbar['value'] >= 1)
        app.toggle_generation_pause()
        wait_paused()
        app.toggle_generation_pause()
        pump(lambda: not app._busy)
        frames_path = Path(pipeline.out_dirs(str(video))[2])
        paused_frames = [pipeline.imread(str(frames_path / f'{i:06d}.png')) for i in range(11)]
        assert all(frame is not None and frame.shape == (128, 192, 3) for frame in paused_frames)
        app.run_worker('dlss')
        pump(lambda: not app._busy)
        assert all(np.array_equal(frame, pipeline.imread(str(frames_path / f'{i:06d}.png')))
                   for i, frame in enumerate(paused_frames))
        results['seed_video_11_frames_identical'] = True

        # Closing a paused task must release its wait, allowing normal shutdown.
        gate.clear()
        started.clear()
        app._in_thread(work)
        pump(started.is_set)
        app.toggle_generation_pause()
        gate.set()
        wait_paused()
        app._on_close()
        root.mainloop()
        app.thread.join(3)
        assert not app.thread.is_alive()
        results['close_while_paused'] = True
        results['status'] = 'passed'
    except Exception:
        import traceback
        results.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        if app._task_control is not None:
            app._task_control.resume()
        try:
            app._close_live()
            root.destroy()
        except tk.TclError:
            pass
        (directory / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results
