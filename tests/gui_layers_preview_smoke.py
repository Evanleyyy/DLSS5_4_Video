"""Check read-only video preview after explicit processing confirmation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import json
    import time
    import os
    import multiprocessing
    import tkinter as tk
    from unittest.mock import patch
    import numpy as np
    import gui
    import pipeline
    video = str(ROOT / 'logs/exe-verification/独立程序测试.mp4')
    result_dir = Path(pipeline.out_dirs(video)[2])
    record = json.loads((result_dir / 'cache.json').read_text(encoding='utf-8'))
    settings = record['record']['options']['settings']
    assert settings['second_layer']['guidance_mode'] == 3
    os.environ['DLSS5_DATA_ROOT'] = str(ROOT / 'logs' / ('confirmed-preview-' + str(time.time_ns())))
    root = tk.Tk()
    app = gui.App(root)
    def confirm():
        app.confirm_processing()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            root.update()
            if not app._busy and not (app.thread and app.thread.is_alive()):
                app._require_confirmed_result()
                return
            time.sleep(.01)
        raise TimeoutError('确认处理超时')
    try:
        app.view_var.set('原图')
        app.v_localTone.set(1.3)
        app.v_second_enabled.set(True)
        app.v_overall_weight.set(60)
        app.layer_vars[1]['preset'].set('Preset #2')
        app.layer_vars[1]['style'].set('电影')
        app.layer_vars[1]['localStruct'].set(1.7)
        app.layer_vars[1]['guidance'].set('深度+光流')
        with patch('tkinter.filedialog.askopenfilename', return_value=video):
            app.import_video()
        root.update()
        assert app._live_dlss_image(0) is None
        confirm()
        errors = []
        for index in range(app.nframes):
            actual = app._live_dlss_image(index)
            expected = pipeline.imread(str(result_dir / f'{index:06d}.png'))
            errors.append(int(np.abs(actual.astype(int) - expected.astype(int)).max()))
        assert max(errors) <= 1, errors
        original = next(pipeline.iter_frames(video))[1]
        previous = app._live_dlss_image(0).copy()
        app.v_overall_weight.set(0)
        np.testing.assert_array_equal(app._live_dlss_image(0), previous)
        confirm()
        np.testing.assert_array_equal(app._live_dlss_image(0), original)
        app._close_live()
        assert not multiprocessing.active_children()
        print(json.dumps({'status': 'passed', 'preview_frame_errors': errors,
                          'zero_weight_original': True, 'worker_closed': True}), flush=True)
    finally:
        app._close_live()
        if getattr(app, '_cap', None):
            app._cap.release()
        root.destroy()


if __name__ == '__main__':
    main()
