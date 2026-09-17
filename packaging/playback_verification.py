"""Verify real Tk key dispatch and video playback without loading GPU models."""
import json
from pathlib import Path
import threading
import time


def verify(directory):
    import cv2
    import numpy as np
    import tkinter as tk
    from tkinter import ttk
    import gui
    import task_control
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running'}
    root = tk.Tk()
    app = gui.App(root)
    app._auto_enabled = False  # This specialized check schedules explicit jobs.
    gate = threading.Event()

    def pump(seconds=.08):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.005)

    def press(widget=None, release=True, state=0):
        widget = widget or app.canvas
        widget.focus_force()
        root.update()
        widget.event_generate('<KeyPress-space>', state=state)
        root.update()
        if release:
            widget.event_generate('<KeyRelease-space>', state=state)
            root.update()

    def click(widget, x=None, y=None):
        root.update()
        x = widget.winfo_width() // 2 if x is None else x
        y = widget.winfo_height() // 2 if y is None else y
        widget.event_generate('<Enter>', x=x, y=y)
        widget.event_generate('<ButtonPress-1>', x=x, y=y)
        widget.event_generate('<ButtonRelease-1>', x=x, y=y)
        root.update()

    def press_current():
        # Do not force focus: keep the exact focus left by the mouse operation.
        focused = root.focus_get()
        assert focused is not None
        focused.event_generate('<KeyPress-space>')
        root.update()
        focused = root.focus_get()
        focused.event_generate('<KeyRelease-space>')
        root.update()

    def toggle_after_click(label, preview_focus=True):
        if preview_focus:
            assert root.focus_get() is app.canvas, (label, root.focus_get())
        assert not app.playing
        press_current()
        assert app.playing, label + ': 点击后空格未播放'
        press_current()
        assert not app.playing, label + ': 再按空格未暂停'

    def select_combo(combo, index):
        click(combo, x=10, y=10)
        popup = root.tk.call('ttk::combobox::PopdownWindow', str(combo))
        items = str(popup) + '.f.l'
        x, y, width, height = root.tk.call(items, 'bbox', index)
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            root.tk.call('event', 'generate', items, event, '-x', x + 2, '-y', y + height // 2)
        root.update()
        assert root.grab_current() is None

    def select_tab(index):
        for x in range(0, app.tabs.winfo_width(), 2):
            try:
                if app.tabs.index('@%d,10' % x) == index:
                    click(app.tabs, x=x + 1, y=10)
                    assert app.tabs.index('current') == index
                    return
            except tk.TclError:
                pass
        raise AssertionError('没有找到分类标签：' + str(index))

    try:
        pump()
        press()
        assert not app.playing
        video = directory / '预览.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 10, (128, 96))
        assert writer.isOpened()
        for index in range(20):
            writer.write(np.full((96, 128, 3), index * 10, np.uint8))
        writer.release()
        app.video, app.nframes, app.fps = str(video), 20, 10
        app.view_var.set('原图')
        app.fslider.configure(to=19)
        app._update_export_btn()
        app.display_view()
        if root.tk.call('tk', 'windowingsystem') == 'win32':
            from native_keyboard_verification import verify as verify_native_keyboard
            result['native_windows_lock_key_states'] = verify_native_keyboard(app, pump)
        press(release=False)
        assert app.playing
        for _ in range(4):
            app.canvas.event_generate('<KeyPress-space>')
            root.update()
            assert app.playing
        app.canvas.event_generate('<KeyRelease-space>')
        first = app.fslider.get()
        pump(.25)
        assert app.fslider.get() > first
        press()
        assert not app.playing
        first = app.fslider.get()
        pump(.2)
        assert app.fslider.get() == first
        result['space_play_pause_and_hold'] = True

        # A focused native button must not run its command in addition to the shortcut.
        press(app.pause_btn)
        assert app.playing
        press(app.play_btn)
        assert not app.playing
        calls = []
        extra = ttk.Button(app.pages['素材'].body, text='测试按钮', command=lambda: calls.append(True))
        extra.pack()
        pump()
        press(extra)
        assert app.playing and not calls
        press(extra)
        assert not app.playing and not calls
        result['button_focus_and_dynamic_controls'] = True

        select_combo(app.view_cb, 0)
        assert app.view_cb.get() == '原图'
        toggle_after_click('只读下拉框')
        result['readonly_selection_then_space'] = True

        # Include native mouse behavior, not just programmatically focused keys.
        fixture = ttk.Frame(app.preview_panel)
        fixture.grid(row=4, column=0, sticky='ew')
        click_calls = []
        chosen = tk.IntVar(value=0)
        controls = [
            ttk.Button(fixture, text='普通按钮', command=lambda: click_calls.append(True)),
            ttk.Checkbutton(fixture, text='勾选项', variable=chosen),
            ttk.Radiobutton(fixture, text='单选项', variable=chosen, value=2),
            tk.Scale(fixture, from_=0, to=10, orient='horizontal'),
        ]
        for control in controls:
            control.pack(side='left', fill='x', expand=True)
        pump()
        for control in controls:
            click(control)
            selected, count = chosen.get(), len(click_calls)
            toggle_after_click(control.winfo_class())
            assert chosen.get() == selected and len(click_calls) == count
        assert click_calls == [True]
        click(app.pause_btn)
        toggle_after_click('暂停按钮')
        click(app.play_btn)
        assert app.playing and root.focus_get() is app.canvas
        press_current()
        assert not app.playing
        click(app.fslider)
        toggle_after_click('进度滑块')
        select_tab(5)
        toggle_after_click('分类标签')
        select_tab(6)
        click(app.log)
        toggle_after_click('只读日志', preview_focus=False)
        select_tab(0)
        fixture.destroy()
        result['mouse_controls_no_sticky_focus_or_duplicate_actions'] = True

        entry = ttk.Entry(app.pages['素材'].body)
        entry.pack()
        text = tk.Text(app.pages['素材'].body, height=1)
        text.pack()
        pump()
        press(entry)
        assert entry.get() == ' ' and not app.playing
        press(text)
        assert text.get('1.0', 'end-1c') == ' ' and not app.playing
        assert app._playback_shortcuts._is_input(app.crf_input)
        assert not app._playback_shortcuts._is_input(app.view_cb)
        editable = ttk.Combobox(app.pages['素材'].body, state='normal')
        editable.pack()
        pump()
        press(editable)
        assert editable.get() == ' ' and not app.playing
        # Clicking an action while editing must leave the input, too.
        click(app.pause_btn)
        toggle_after_click('从输入框点击按钮')
        for modifier in (0x0001, 0x0004, 0x20000):
            press(state=modifier)
            assert not app.playing
        result['editing_and_modifiers_preserved'] = True

        dialog = tk.Toplevel(root)
        dialog_button = ttk.Button(dialog, text='对话框按钮', command=lambda: calls.append(True))
        dialog_button.pack()
        dialog.grab_set()
        pump()
        press(dialog_button)
        pump(.15)
        assert calls == [True] and not app.playing
        dialog.grab_release()
        dialog.destroy()
        result['dialog_keyboard_preserved'] = True

        def work():
            while not gate.wait(.02):
                task_control.checkpoint()
        app._in_thread(work)
        pump()
        press(app.task_pause_btn)
        assert app._task_control.state == 'running' and not app.playing
        app.task_pause_btn.invoke()
        pump()
        assert app._task_control.state == 'paused'
        press(app.task_pause_btn)
        assert app._task_control.state == 'paused' and not app.playing
        app.task_pause_btn.invoke()
        gate.set()
        pump(.15)
        assert not app._busy
        app.current_is_image = True
        press()
        assert not app.playing
        result['generation_pause_and_image_mode_unchanged'] = True
        result['status'] = 'passed'
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        gate.set()
        if app._task_control:
            app._task_control.resume()
        if app.thread:
            app.thread.join(3)
        app.pause()
        app._close_live()
        if getattr(app, '_cap', None):
            app._cap.release()
        root.destroy()
        (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
