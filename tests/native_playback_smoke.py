"""Minimal native Windows Space regression using the production shortcut handler."""
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]
from playback_shortcuts import PlaybackShortcuts
from native_keyboard_verification import verify


def main():
    root = tk.Tk()
    canvas = tk.Canvas(root, width=320, height=180)
    canvas.pack()
    app = SimpleNamespace(root=root, canvas=canvas, video=True, playing=False,
                          current_is_image=False, _busy=False, _closing=False)
    app.play = lambda: setattr(app, 'playing', True)
    app.pause = lambda: setattr(app, 'playing', False)
    PlaybackShortcuts(app)

    def pump(seconds=.05):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.003)
    try:
        pump()
        print(verify(app, pump), flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
