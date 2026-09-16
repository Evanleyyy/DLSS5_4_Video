# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
app = root / 'source' / 'dlss5standaloneV2'
sys.path.insert(0, str(app / 'models'))

a = Analysis(
    [str(root / 'packaging' / 'standalone_entry.py')],
    pathex=[str(app), str(app / 'models'), str(root / 'packaging')],
    binaries=[
        (str(app / 'dlssnr_host.dll'), '.'),
        (str(app / 'nvngx_dlssnr.dll'), '.'),
        (str(root / 'runtime' / 'ffmpeg.exe'), '.'),
    ],
    datas=[(str(root / 'packaging/sr-assets-manifest.json'), 'model-manifests')],
    hiddenimports=['PIL.Image', 'PIL.ImageTk', 'torchvision.models.optical_flow',
                   *collect_submodules('depth_anything_v2')],
    excludes=['IPython', 'jupyter', 'notebook', 'matplotlib', 'pandas', 'scipy',
              'sklearn', 'pytest', 'tensorboard', 'torchaudio'],
    noarchive=False,
    optimize=0,
)

# Static libraries and development headers are not needed for inference.
a.datas = [entry for entry in a.datas
           if not entry[0].lower().endswith(('.lib', '.a', '.h', '.hpp', '.cuh'))]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='DLSS5_App',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    runtime_tmpdir=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DLSS5_App')
