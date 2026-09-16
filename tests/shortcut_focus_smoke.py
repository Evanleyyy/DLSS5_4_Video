"""Reproduce click -> select -> Space through the real Tk focus owner."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
from playback_shortcuts import PlaybackShortcuts


def main():
    root = tk.Tk()
    root.geometry('420x220')
    canvas = tk.Canvas(root, height=80, highlightthickness=0)
    canvas.pack(fill='x')
    combo = ttk.Combobox(root, state='readonly', values=['原图', '对比'])
    combo.current(0)
    combo.pack(fill='x')
    app = SimpleNamespace(root=root, canvas=canvas, video=True, playing=False,
                          current_is_image=False, _busy=False, _closing=False)
    app.play = lambda: setattr(app, 'playing', True)
    app.pause = lambda: setattr(app, 'playing', False)
    shortcuts = PlaybackShortcuts(app)
    try:
        root.update()
        root.focus_force()
        root.update()
        combo.event_generate('<ButtonPress-1>', x=20, y=10)
        combo.event_generate('<ButtonRelease-1>', x=20, y=10)
        root.update()
        popup = root.tk.call('ttk::combobox::PopdownWindow', str(combo))
        items = str(popup) + '.f.l'
        x, y, width, height = root.tk.call(items, 'bbox', 1)
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            root.tk.call('event', 'generate', items, event, '-x', x + 2, '-y', y + height // 2)
        root.update()
        assert combo.get() == '对比', combo.get()
        assert root.grab_current() is None
        focused = root.focus_get()
        print({'selected': combo.get(), 'focus': focused.winfo_class(),
               'tag_attached': shortcuts._tag in focused.bindtags(),
               'key_down': shortcuts._down}, flush=True)
        focused.event_generate('<KeyPress-space>')
        root.update()
        focused.event_generate('<KeyRelease-space>')
        root.update()
        assert app.playing, '选择只读下拉框后直接按空格，视频没有开始播放'
        print('点击选择后空格播放：通过', flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
