"""Real GPU checks of cross-task reuse, changed parameters and final cleanup."""
import json
import os
from pathlib import Path
import sys
import time


def verify(directory, engines=('pisa', 'seedvr2', 'vosr')):
    import cv2
    import numpy as np
    import psutil
    import tkinter as tk
    from unittest.mock import patch
    import dlss_runtime
    import gui
    import model_sessions
    import pipeline
    import sr_backend
    import sr_settings
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['DLSS5_DATA_ROOT'] = str(directory / 'data')
    os.environ['DLSS5_LOG_DIR'] = str(directory)
    report = {'status': 'running', 'frozen': bool(getattr(sys, 'frozen', False)), 'dlss': {}, 'sr': {}}
    owner, app, root = model_sessions.ModelSessions(), None, None
    pids = set()
    source = np.zeros((128, 128, 3), np.uint8)
    source[..., 0] = np.arange(128, dtype=np.uint8) * 2
    source[..., 1] = source[..., 0].T
    source[..., 2] = 80
    cv2.putText(source, 'MODEL', (9, 65), cv2.FONT_HERSHEY_SIMPLEX, .65, (245, 235, 250), 2)
    picture = directory / 'input.png'
    pipeline.imwrite(str(picture), source)
    errors = []
    try:
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        app._auto_enabled = False  # This specialized check schedules explicit jobs.
        for name, variable in app.v_export_channels.items():
            variable.set(name == 'dlss')
        with patch('gui.filedialog.askopenfilename', return_value=str(picture)):
            app.import_image()
        def confirm():
            started = time.monotonic()
            app.confirm_processing()
            while app._busy or (app.thread and app.thread.is_alive()):
                root.update()
                assert time.monotonic() - started < 180, '界面处理超时'
                time.sleep(.01)
            root.update()
            assert not errors, errors
            assert not app._last_task_failed, app._task_status
            live = app._model_sessions.dlss.first
            pids.add(live._process.pid)
            assert live._process.is_alive()
            return live._process.pid, time.monotonic() - started
        previous = None
        for version in ('bundled', '310.8.SF-v2', '310.8.SF'):
            app.layer_vars[0]['runtime_version'].set(dlss_runtime.LABELS[version])
            app.layer_vars[0]['intensity'].set(1.)
            cold_pid, cold = confirm()
            if previous is not None:
                assert not psutil.pid_exists(previous), '切换模型未释放旧进程'
            before = app._confirmed_image.copy()
            app.layer_vars[0]['intensity'].set(.25)
            root.update()
            assert np.array_equal(before, app._confirmed_image), '未确认就开始处理'
            warm_pid, warm = confirm()
            assert cold_pid == warm_pid, '同模型改参数重新加载'
            assert np.any(before != app._confirmed_image), '新参数没有生效'
            report['dlss'][version] = dict(pid=cold_pid, first_seconds=round(cold, 3),
                                          reused_seconds=round(warm, 3), changed_pixels=True)
            previous = warm_pid
            print('DLSS 参数复用通过：', version, report['dlss'][version], flush=True)
        app.v_second_enabled.set(True)
        first_pid, _ = confirm()
        second_pid = app._model_sessions.dlss.second.live._process.pid
        pids.add(second_pid)
        app.layer_vars[0]['intensity'].set(.5)
        app.layer_vars[1]['intensity'].set(.4)
        assert confirm()[0] == first_pid
        assert app._model_sessions.dlss.second.live._process.pid == second_pid
        app.v_second_enabled.set(False)
        assert confirm()[0] == first_pid
        assert not psutil.pid_exists(second_pid)
        app.layer_vars[0]['preset'].set(2)
        preset_pid, _ = confirm()
        assert preset_pid != first_pid and not psutil.pid_exists(first_pid)
        report['double_layer_reuse_and_preset_rebuild'] = True
        app._close_live()
        assert not psutil.pid_exists(preset_pid)

        video = directory / 'video.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 6, (128, 128))
        assert writer.isOpened()
        for i in range(3):
            writer.write(np.roll(source, i * 2, axis=1))
        writer.release()
        with model_sessions.bind(owner):
            for intensity in (1., .35):
                assert pipeline.generate_dlss(str(video), {'intensity': intensity}) == 3
                pid = owner.dlss.first._process.pid
                if intensity == 1.:
                    video_pid = pid
                    pids.add(pid)
                else:
                    assert pid == video_pid, '连续视频任务重复加载模型'
            assert owner.dlss.first._process.is_alive()
        report['video_tasks_reuse'] = True

        for engine in engines:
            rows, previous_output = [], None
            for index in range(2):
                cfg = sr_settings.normalize(dict(engine=engine, tile=256 if index == 0 else 320,
                    seed=42 + index, pisa_pixel=1. if index == 0 else .5,
                    pisa_semantic=1. if index == 0 else .5, blocks=24 if index == 0 else 23))
                started = time.monotonic()
                with model_sessions.bind(owner):
                    result = sr_backend.run_job(dict(kind='images', inputs=[str(picture)],
                        output=str(directory / f'{engine}-{index}'), settings={'super_resolution': cfg}))
                output = pipeline.imread(result['outputs'][0])
                assert output.shape == (256, 256, 3)
                assert result['model_loads'] == 1
                assert result['model_reused'] == bool(index)
                assert owner.sr.alive
                pids.add(result['worker_pid'])
                if index:
                    assert rows[0]['worker_pid'] == result['worker_pid']
                    assert np.any(output != previous_output), '修改参数后像素完全没有变化'
                else:
                    assert not psutil.pid_exists(video_pid)
                rows.append({**result, 'wall_seconds': round(time.monotonic() - started, 3)})
                previous_output = output
                print('超分会话验证：', engine, index, rows[-1], flush=True)
            log_text = owner.sr.log_path.read_text(encoding='utf-8', errors='replace')
            (directory / f'{engine}-worker.log').write_text(log_text, encoding='utf-8')
            report['sr'][engine] = rows
            pid = owner.sr.process.pid
            owner.close()
            assert not psutil.pid_exists(pid), '关闭未释放超分进程'
        report['status'] = 'passed'
    except BaseException:
        import traceback
        report.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        owner.close()
        if app is not None:
            app._close_live()
        if root is not None:
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()
        report['remaining_workers'] = [pid for pid in pids if psutil.pid_exists(pid)]
        (directory / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not report['remaining_workers'], report['remaining_workers']
    return report


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source/dlss5standaloneV2'))
    verify(sys.argv[1])
