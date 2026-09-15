"""Exercise bundled assets, GPU inference and GUI without external Python."""
import json
import subprocess
import sys
import time
from pathlib import Path


def verify(directory, bundle):
    import cv2
    import numpy as np
    import torch
    import torchvision
    import pipeline
    import dlss_engine
    import gui
    import tkinter as tk

    result = {'frozen': bool(getattr(sys, 'frozen', False)), 'bundle': str(bundle),
              'executable': sys.executable, 'python': sys.version,
              'torch': torch.__version__, 'torchvision': torchvision.__version__,
              'gpu': torch.cuda.get_device_name(), 'sys_path': sys.path,
              'stages': {}}
    report = directory / 'verification.json'

    def save():
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    def stage(name, operation):
        print('START', name, flush=True)
        start = time.monotonic()
        value = operation()
        result['stages'][name] = {'result': value, 'seconds': round(time.monotonic() - start, 3)}
        save()
        print('PASS', name, flush=True)
        return value

    try:
        assert result['frozen'], 'Must test the actual executable'
        for module in (cv2, np, torch, torchvision, pipeline, dlss_engine, gui):
            assert Path(module.__file__).resolve().is_relative_to(bundle.resolve()), module.__file__
        assert Path(pipeline.BASE).resolve() == bundle.resolve()
        for asset in (pipeline.DAV2_CKPT, pipeline.RAFT_PTH, dlss_engine.HOST_DLL,
                      dlss_engine.DLSSNR_DLL, pipeline._bundle_ffmpeg()):
            assert Path(asset).is_file(), asset
            assert Path(asset).resolve().is_relative_to(bundle.resolve()), asset

        sample = str(directory / '独立程序测试.mp4')
        ffmpeg = pipeline._bundle_ffmpeg()
        subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x240:rate=12:duration=0.25', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=0.25',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', sample],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        stage('dlss_no_guidance', lambda: pipeline.generate_dlss(sample, {'guidance_mode': 0}))
        stage('depth', lambda: pipeline.generate_depth(sample, edge=240, force=True))
        stage('flow', lambda: pipeline.generate_flow(sample, edge=240, force=True))
        stage('dlss_with_guidance', lambda: pipeline.generate_dlss(sample, {'guidance_mode': 3}))
        output = stage('export_with_audio', lambda: pipeline.export_video(sample, 'dlss', fps=12))
        assert pipeline.video_info(output)[:2] == (3, 12.0)
        subprocess.run([ffmpeg, '-v', 'error', '-i', output, '-map', '0:a:0', '-f', 'null', '-'],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        source_frame = next(pipeline.iter_frames(sample))[1]
        processed = pipeline.imread(str(Path(pipeline.out_dirs(sample)[2]) / '000000.png'))
        assert np.any(source_frame != processed), 'DLSS output must differ from input'

        def verify_gui():
            root = tk.Tk()
            root.withdraw()
            try:
                app = gui.App(root)
                root.update_idletasks()
                picture = np.zeros((239, 317, 3), np.uint8)
                picture[:, :158] = [20, 100, 210]
                picture[:, 158:] = [210, 100, 20]
                output_image = app._image_dlss(picture)
                assert output_image.shape == picture.shape
                pipeline.imwrite(str(directory / '图片测试.png'), output_image)
                from editor_verification import verify_editor
                editor_result = verify_editor(app, directory / 'editor', picture, output_image)
                from export_verification import verify_export
                stage('multi_channel_export', lambda: verify_export(app, directory / 'channels', sample, picture, output_image))
                app._close_live()
                return editor_result
            finally:
                root.destroy()

        stage('gui_and_odd_image', verify_gui)
        result['status'] = 'passed'
    except Exception:
        import traceback
        result['status'] = 'failed'
        result['error'] = traceback.format_exc()
        raise
    finally:
        save()
