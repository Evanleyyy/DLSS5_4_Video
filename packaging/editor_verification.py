"""Exercise real Tk events and saved pixels; shared by source and EXE checks."""
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk

import pipeline


def verify_editor(app, directory, original, processed, screenshots=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = app.root
    errors = []
    root.report_callback_exception = lambda *error: errors.append(str(error))
    root.deiconify()

    def pump(seconds=.12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.005)
        assert not errors, errors

    pump(.25)
    app.current_is_image = True
    app.image_path = str(directory / '原图.png')
    app.image_bgr, app.image_dlss = original.copy(), processed.copy()
    app._reset_image_editor()
    app._update_export_btn()
    app.vlabel.configure(text='交互验证图片 · 原始尺寸保持不变')
    app.view_var.set('DLSS')
    app.v_mask_overlay.set(0)
    pump()

    def screenshot(name):
        if screenshots:
            from PIL import ImageGrab
            import ctypes
            handle = ctypes.windll.user32.GetParent(root.winfo_id())
            ImageGrab.grab(window=handle).save(directory / name)

    # Each page must fit horizontally at every supported layout breakpoint.
    for width, height in [(1240, 820), (1000, 720), (900, 720), (680, 520), (1600, 950)]:
        root.geometry(f'{width}x{height}+20+20')
        pump()
        for name, page in app.pages.items():
            app.tabs.select(page)
            pump()
            body_width = page.body.winfo_width()
            def inspect(widget):
                for child in widget.winfo_children():
                    if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Combobox, ttk.Spinbox, tk.Scale)):
                        left = child.winfo_rootx() - page.body.winfo_rootx()
                        assert left >= 0 and left + child.winfo_width() <= body_width + 2, (width, name, str(child), left, child.winfo_width(), body_width)
                    inspect(child)
            inspect(page.body)
        assert app.canvas.winfo_height() >= 90, (width, app.canvas.winfo_height())
        if width == 680:
            app.tabs.select(app.pages['遮罩'])
            pump()
            screenshot('界面_窄窗口.png')

    root.geometry('1240x820+20+20')
    app.tabs.select(app.pages['遮罩'])
    pump()
    app.fit_image()
    pump()
    w, h, cw, ch, scale, ox, oy = app._image_geometry()
    center = (int(ox + w * scale * .5), int(oy + h * scale * .5))
    before = app._image_point(SimpleNamespace(x=center[0], y=center[1]))
    app.canvas.event_generate('<MouseWheel>', x=center[0], y=center[1], delta=240)
    pump()
    assert app.viewport.zoom > 1
    np.testing.assert_allclose(app._image_point(SimpleNamespace(x=center[0], y=center[1])), before, atol=1e-6)
    app.canvas.event_generate('<ButtonPress-1>', x=center[0], y=center[1])
    app.canvas.event_generate('<B1-Motion>', x=center[0]+24, y=center[1]+18)
    app.canvas.event_generate('<ButtonRelease-1>', x=center[0]+24, y=center[1]+18)
    pump()
    assert abs(app.viewport.pan_x - 24) < 2 and abs(app.viewport.pan_y - 18) < 2

    # Paint using transformed screen coordinates; the selected source pixel must match.
    app.v_image_tool.set('画笔')
    app.v_brush_size.set(20)
    w, h, cw, ch, scale, ox, oy = app._image_geometry()
    x, y = int(ox + w*.5*scale), int(oy + h*.5*scale)
    app.canvas.event_generate('<ButtonPress-1>', x=x, y=y)
    app.canvas.event_generate('<B1-Motion>', x=x+int(12*scale), y=y)
    app.canvas.event_generate('<ButtonRelease-1>', x=x+int(12*scale), y=y)
    pump()
    assert app.selection.data[h//2, w//2] == 255
    assert app.selection.data[0, 0] == 0
    drawn = app.selection.data.copy()
    app._mask_action('undo')
    assert not app.selection.data.any()
    app._mask_action('redo')
    np.testing.assert_array_equal(app.selection.data, drawn)
    app.v_image_tool.set('橡皮')
    app.canvas.event_generate('<ButtonPress-1>', x=x, y=y)
    app.canvas.event_generate('<ButtonRelease-1>', x=x, y=y)
    assert app.selection.data[h//2, w//2] == 0
    app._mask_action('undo')

    app._mask_action('clear')
    app.fit_image()
    pump()
    app.v_image_tool.set('矩形选区')
    w, h, cw, ch, scale, ox, oy = app._image_geometry()
    start = int(ox+w*.25*scale), int(oy+h*.25*scale)
    end = int(ox+w*.75*scale), int(oy+h*.75*scale)
    app.canvas.event_generate('<ButtonPress-1>', x=start[0], y=start[1])
    app.canvas.event_generate('<B1-Motion>', x=end[0], y=end[1])
    app.canvas.event_generate('<ButtonRelease-1>', x=end[0], y=end[1])
    pump()
    assert app.selection.data[h//2, w//2] == 255
    assert app.selection.data[0, 0] == 0
    app.v_feather.set(12)
    app.v_mask_enabled.set(1)
    app.v_mask_mode.set('只处理涂抹区域')
    expected = app._image_output()
    np.testing.assert_array_equal(expected[0, 0], original[0, 0])
    np.testing.assert_array_equal(expected[h//2, w//2], processed[h//2, w//2])
    assert np.any((app.selection.alpha(12) > 0) & (app.selection.alpha(12) < 255))
    # The visible overlay must not enter the exported PNG.
    app.v_mask_overlay.set(1)
    destination = directory / '局部遮罩导出.png'
    with patch('tkinter.filedialog.asksaveasfilename', return_value=str(destination)), patch('tkinter.messagebox.showinfo'):
        app._export_image()
    np.testing.assert_array_equal(pipeline.imread(str(destination)), expected)
    app.v_mask_mode.set('保护涂抹区域')
    protected = app._image_output()
    np.testing.assert_array_equal(protected[0, 0], processed[0, 0])
    np.testing.assert_array_equal(protected[h//2, w//2], original[h//2, w//2])
    app.v_mask_mode.set('只处理涂抹区域')
    app.view_var.set('对比')
    app._schedule_image_render()
    pump()
    screenshot('界面_遮罩与羽化.png')
    app.actual_image_size()
    assert abs(app._image_geometry()[4] - 1.0) < 1e-6
    app.fit_image()
    # Opening another image must reset the previous image's selection and navigation.
    app._reset_image_editor()
    assert not app.selection.data.any() and not app.selection.undo_stack
    assert app.viewport.zoom == 1 and app.v_mask_enabled.get() == 0
    app.current_is_image = False
    app._reset_image_editor()
    app._update_export_btn()
    assert app.selection is None
    assert str(app.export_format_cb.cget('state')) == 'readonly'
    return 'layout, zoom, pan, brush, eraser, rectangle, undo/redo, feather and PNG export passed'
