"""Tk interactions for original-resolution image selection and navigation."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np

import pipeline
from image_editor import Viewport, SelectionMask, blend_selection


class ImageEditorMixin:
    def _init_image_editor(self):
        self.viewport = Viewport()
        self.selection = None
        self._image_render_after = None
        self._interaction = None
        self._pointer = None
        self.v_image_tool = tk.StringVar(value='平移')
        self.v_mask_enabled = tk.IntVar(value=0)
        self.v_mask_overlay = tk.IntVar(value=1)
        self.v_mask_mode = tk.StringVar(value='只处理涂抹区域')
        self.v_brush_size = tk.DoubleVar(value=60)
        self.v_feather = tk.DoubleVar(value=16)

    def _build_mask_controls(self, parent):
        self._note(parent, '对当前图片绘制选区，按原图像素保存。首次落笔会自动启用局部遮罩。')
        grid = self._section(parent, '选区作用')
        grid.add(ttk.Checkbutton(grid, text='启用局部遮罩', variable=self.v_mask_enabled, command=self._schedule_image_render))
        grid.add(ttk.Checkbutton(grid, text='显示红色提示层', variable=self.v_mask_overlay, command=self._schedule_image_render))
        self._choice(grid, '处理范围', self.v_mask_mode, ['只处理涂抹区域', '保护涂抹区域'], self._schedule_image_render)
        grid = self._section(parent, '绘制与导航')
        self._choice(grid, '鼠标左键工具', self.v_image_tool, ['平移', '画笔', '橡皮', '矩形选区', '对比分隔线'], self._tool_changed)
        self._scale(grid, '画笔直径（原图像素）', self.v_brush_size, 2, 600, 1, self._schedule_image_render)
        self._scale(grid, '边缘羽化半径（原图像素）', self.v_feather, 0, 100, 1, self._schedule_image_render)
        self._note(parent, '滚轮以鼠标位置为中心缩放；平移工具用左键拖动。绘制时可用鼠标中键或右键拖动画面。松开画笔后预览羽化；羽化为 0 时保留硬边。')
        grid = self._section(parent, '编辑选区')
        for label, action in [('撤销', 'undo'), ('重做', 'redo'), ('清空选区', 'clear'),
                              ('全选', 'all'), ('反选', 'invert')]:
            self._button(grid, label, lambda name=action: self._mask_action(name))
        self._button(grid, '保存遮罩 PNG', self._save_mask)
        self.mask_status = self._note(parent, '请先导入一张图片。')

    def _bind_image_canvas(self):
        self.canvas.bind('<MouseWheel>', self._on_image_wheel)
        self.canvas.bind('<Button-4>', self._on_image_wheel)
        self.canvas.bind('<Button-5>', self._on_image_wheel)
        self.canvas.bind('<ButtonPress-1>', self._on_canvas_press)
        self.canvas.bind('<B1-Motion>', self._on_canvas_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_canvas_release)
        for button in (2, 3):
            self.canvas.bind(f'<ButtonPress-{button}>', lambda e: self._on_canvas_press(e, force_pan=True))
            self.canvas.bind(f'<B{button}-Motion>', self._on_canvas_drag)
            self.canvas.bind(f'<ButtonRelease-{button}>', self._on_canvas_release)
        self.canvas.bind('<Motion>', self._show_brush_cursor)
        self.canvas.bind('<Leave>', lambda e: self.canvas.delete('brush_cursor'))
        self.canvas.bind('<Control-z>', lambda e: self._mask_action('undo'))
        self.canvas.bind('<Control-y>', lambda e: self._mask_action('redo'))

    def _reset_image_editor(self):
        self.viewport.reset()
        self._interaction = None
        self._pointer = None
        self.v_mask_enabled.set(0)
        self.v_image_tool.set('平移')
        self.selection = SelectionMask(self.image_bgr.shape[1], self.image_bgr.shape[0]) if self.current_is_image else None
        self._schedule_image_render()

    def _image_geometry(self):
        h, w = self.image_bgr.shape[:2]
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale, ox, oy = self.viewport.transform(w, h, cw, ch)
        return w, h, cw, ch, scale, ox, oy

    def _image_point(self, event):
        _, _, _, _, scale, ox, oy = self._image_geometry()
        return (event.x - ox) / scale, (event.y - oy) / scale

    def _schedule_image_render(self, event=None):
        if self._image_render_after is None:
            self._image_render_after = self.root.after(25, self._render_image_later)

    def _render_image_later(self):
        self._image_render_after = None
        if self.current_is_image:
            self._display_image()

    def fit_image(self):
        self.viewport.reset()
        self.display_view()

    def actual_image_size(self):
        if not self.current_is_image or self.image_bgr is None:
            return
        w, h, cw, ch, *_ = self._image_geometry()
        self.viewport.reset()
        self.viewport.zoom = 1.0 / min(cw / w, ch / h)
        self.display_view()

    def _on_image_wheel(self, event):
        if not self.current_is_image or self.image_bgr is None:
            return 'break'
        direction = 1 if getattr(event, 'num', None) == 4 else -1 if getattr(event, 'num', None) == 5 else event.delta / 120
        if not direction:
            return 'break'
        if self._interaction:
            self._on_canvas_release(event)
        w, h, cw, ch, *_ = self._image_geometry()
        self.viewport.zoom_at(1.2 ** max(-4, min(4, direction)), event.x, event.y, w, h, cw, ch)
        self._schedule_image_render()
        return 'break'

    def _tool_changed(self, event=None):
        self._interaction = None
        if self.selection:
            self.selection.end_stroke()
        self._schedule_image_render()

    def _on_canvas_press(self, event, force_pan=False):
        if not self.current_is_image:
            self.on_canvas_motion(event)
            return
        if self.image_bgr is None:
            return
        if self._interaction:
            self._on_canvas_release(event)
        self.canvas.focus_set()
        point = self._image_point(event)
        tool = '平移' if force_pan else self.v_image_tool.get()
        if tool in ('画笔', '橡皮', '矩形选区'):
            if self.thread and self.thread.is_alive():
                return
            w, h, *_ = self._image_geometry()
            if not (0 <= point[0] < w and 0 <= point[1] < h):
                return
            self.selection.begin_stroke()
            self.v_mask_enabled.set(1)
            if tool in ('画笔', '橡皮'):
                self.selection.paint(point, point, self.v_brush_size.get(), tool == '橡皮')
        elif tool == '对比分隔线':
            self.view_var.set('对比')
            self.split_x = max(0.0, min(1.0, point[0] / self.image_bgr.shape[1]))
        self._interaction = {'tool': tool, 'last_screen': (event.x, event.y), 'last': point, 'start': point, 'end': point}
        self._schedule_image_render()

    def _on_canvas_drag(self, event):
        if not self.current_is_image:
            self.on_canvas_motion(event)
            return
        if not self._interaction:
            return
        state = self._interaction
        point = self._image_point(event)
        tool = state['tool']
        if tool == '平移':
            self.viewport.pan_x += event.x - state['last_screen'][0]
            self.viewport.pan_y += event.y - state['last_screen'][1]
        elif tool in ('画笔', '橡皮'):
            self.selection.paint(state['last'], point, self.v_brush_size.get(), tool == '橡皮')
        elif tool == '对比分隔线':
            self.split_x = max(0.0, min(1.0, point[0] / self.image_bgr.shape[1]))
        state.update(last=point, end=point, last_screen=(event.x, event.y))
        self._pointer = (event.x, event.y)
        self._schedule_image_render()

    def _on_canvas_release(self, event):
        if not self.current_is_image or not self._interaction:
            return
        state = self._interaction
        if state['tool'] == '矩形选区':
            w, h, *_ = self._image_geometry()
            end = self._image_point(event)
            x1, x2 = sorted((state['start'][0], end[0]))
            y1, y2 = sorted((state['start'][1], end[1]))
            x1, x2 = max(0, min(w - 1, int(x1))), max(0, min(w - 1, int(x2)))
            y1, y2 = max(0, min(h - 1, int(y1))), max(0, min(h - 1, int(y2)))
            cv2.rectangle(self.selection.data, (x1, y1), (x2, y2), 255, -1)
            self.selection.revision += 1
        if state['tool'] in ('画笔', '橡皮', '矩形选区'):
            self.selection.end_stroke()
        self._interaction = None
        self._schedule_image_render()

    def _mask_action(self, action):
        if not self.current_is_image or self.selection is None or (self.thread and self.thread.is_alive()):
            return 'break'
        self.selection.end_stroke()
        self._interaction = None
        if action == 'undo':
            self.selection.undo()
        elif action == 'redo':
            self.selection.redo()
        elif action == 'clear':
            self.selection.replace(0)
        elif action == 'all':
            self.selection.replace(255)
        elif action == 'invert':
            self.selection.replace(255 - self.selection.data)
        self.v_mask_enabled.set(1)
        self._schedule_image_render()
        return 'break'

    def _show_brush_cursor(self, event=None):
        self.canvas.delete('brush_cursor')
        if event is not None:
            self._pointer = (event.x, event.y)
        if not self.current_is_image or self.image_bgr is None or not self._pointer:
            return
        tool = self.v_image_tool.get()
        self.canvas.configure(cursor='fleur' if tool == '平移' else 'crosshair')
        if tool not in ('画笔', '橡皮'):
            return
        _, _, _, _, scale, _, _ = self._image_geometry()
        radius = self.v_brush_size.get() * scale / 2
        x, y = self._pointer
        self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius, outline='white', width=2, tags='brush_cursor')
        self.canvas.create_oval(x-radius-1, y-radius-1, x+radius+1, y+radius+1, outline='#222222', tags='brush_cursor')

    def _display_image(self):
        if self.image_bgr is None:
            return
        from PIL import Image, ImageTk
        w, h, cw, ch, scale, ox, oy = self._image_geometry()
        matrix = np.float32([[scale, 0, ox], [0, scale, oy]])
        def project(array, border=0):
            return cv2.warpAffine(array, matrix, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=border)
        original = project(self.image_bgr, (34, 27, 22))
        processed = project(self.image_dlss, (34, 27, 22)) if self.image_dlss is not None else original.copy()
        alpha = None
        if self.selection is not None:
            # Keep painting responsive; calculate the exact feathered selection on release.
            drawing = self._interaction and self._interaction['tool'] in ('画笔', '橡皮')
            raw = self.selection.data if drawing else self.selection.alpha(self.v_feather.get())
            alpha = project(raw)
        if self.v_mask_enabled.get() and alpha is not None:
            processed = blend_selection(original, processed, alpha, self.v_mask_mode.get() == '保护涂抹区域')
        view = self.view_var.get()
        if view == '原图':
            shown = original
        elif view == '对比':
            shown = original.copy()
            cut = max(0, min(cw, int(ox + self.split_x * w * scale)))
            shown[:, cut:] = processed[:, cut:]
        else:
            shown = processed
        if alpha is not None and self.v_mask_overlay.get():
            amount = alpha[:, :, None].astype(np.float32) * (0.38 / 255)
            shown = (shown * (1 - amount) + np.array([70, 70, 255]) * amount).clip(0, 255).astype(np.uint8)
        self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(shown, cv2.COLOR_BGR2RGB)))
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self._photo)
        if view == '对比':
            cut = ox + self.split_x * w * scale
            self.canvas.create_line(cut, max(0, oy), cut, min(ch, oy + h * scale), fill='#ffd166', width=2)
            self.canvas.create_text(12, 12, text='原图  /  局部处理结果', fill='white', anchor='nw')
        if self._interaction and self._interaction['tool'] == '矩形选区':
            start, end = self._interaction['start'], self._interaction['end']
            self.canvas.create_rectangle(ox+start[0]*scale, oy+start[1]*scale, ox+end[0]*scale, oy+end[1]*scale,
                                         outline='#ff7373', width=2, dash=(5, 3))
        self.zoom_label.configure(text=f'缩放 {scale * 100:.0f}%')
        self.mask_status.configure(text=('局部遮罩已启用' if self.v_mask_enabled.get() else '局部遮罩未启用，处理整张图片') +
            f'\n撤销 {len(self.selection.undo_stack)} 步 · 羽化 {self.v_feather.get():.0f} 像素' if self.selection else '请先导入一张图片。')
        self._show_brush_cursor()

    def _image_output(self):
        if self.image_dlss is None:
            return None
        if not self.v_mask_enabled.get() or self.selection is None:
            return self.image_dlss
        return blend_selection(self.image_bgr, self.image_dlss, self.selection.alpha(self.v_feather.get()),
                               self.v_mask_mode.get() == '保护涂抹区域')

    def _save_mask(self):
        if not self.current_is_image or self.selection is None:
            return
        path = filedialog.asksaveasfilename(title='保存羽化后的选区遮罩', defaultextension='.png',
            initialfile='选区遮罩.png', filetypes=[('PNG', '*.png')])
        if path:
            pipeline.imwrite(path, self.selection.alpha(self.v_feather.get()))
            self.logln('已保存遮罩（白色为涂抹区域）: ' + path)
