"""Responsive, categorized controls for the desktop application."""
import tkinter as tk
from tkinter import ttk, scrolledtext


class FlowGrid(ttk.Frame):
    def __init__(self, parent, minimum=160):
        super().__init__(parent)
        self.minimum = minimum
        self.items = []
        self.columns = 0
        self.bind('<Configure>', self._layout)

    def add(self, widget):
        self.items.append(widget)
        self._layout()
        return widget

    def _layout(self, event=None):
        minimum = max([self.minimum] + [widget.winfo_reqwidth() + 6 for widget in self.items])
        columns = max(1, min(len(self.items) or 1, self.winfo_width() // minimum))
        for column in range(max(self.columns, columns)):
            self.columnconfigure(column, weight=1 if column < columns else 0,
                                 uniform='cells' if column < columns else '')
        self.columns = columns
        for index, widget in enumerate(self.items):
            widget.grid(row=index // columns, column=index % columns, sticky='nsew', padx=3, pady=4)


class ScrollPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.bar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body = ttk.Frame(self.canvas, padding=6)
        self.body.columnconfigure(0, weight=1)
        self.window = self.canvas.create_window(0, 0, anchor='nw', window=self.body)
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=max(1, e.width)))
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))

    def bind_wheel(self):
        def scroll(event):
            if self.canvas.bbox('all') and self.body.winfo_height() > self.canvas.winfo_height():
                delta = -1 if getattr(event, 'num', None) == 4 else 1 if getattr(event, 'num', None) == 5 else -int(event.delta / 120 or (1 if event.delta > 0 else -1))
                self.canvas.yview_scroll(delta * 3, 'units')
            return 'break'
        def visit(widget):
            if not isinstance(widget, (tk.Scale, ttk.Combobox, ttk.Spinbox)):
                for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                    widget.bind(event, scroll)
            for child in widget.winfo_children():
                visit(child)
        visit(self.body)
        self.canvas.bind('<MouseWheel>', scroll)


class LayoutMixin:
    def _section(self, parent, title):
        section = ttk.LabelFrame(parent, text=title, padding=6)
        section.pack(fill='x', pady=(0, 8))
        grid = FlowGrid(section)
        grid.pack(fill='x')
        return grid

    def _button(self, grid, text, command):
        return grid.add(ttk.Button(grid, text=text, command=command))

    def _choice(self, grid, label, variable, values, command=None):
        cell = ttk.Frame(grid)
        ttk.Label(cell, text=label).pack(fill='x')
        combo = ttk.Combobox(cell, textvariable=variable, values=values, state='readonly', width=1)
        combo.pack(fill='x', pady=(3, 0))
        if command:
            combo.bind('<<ComboboxSelected>>', command)
        grid.add(cell)
        return combo

    def _scale(self, grid, label, variable, low, high, step, command):
        cell = ttk.Frame(grid)
        ttk.Label(cell, text=label).pack(fill='x')
        tk.Scale(cell, variable=variable, from_=low, to=high, resolution=step,
                 orient='horizontal', length=1, highlightthickness=0,
                 command=lambda value: command()).pack(fill='x')
        grid.add(cell)

    def _note(self, parent, text):
        label = ttk.Label(parent, text=text, foreground='#606873', justify='left')
        label.pack(fill='x', pady=(2, 8))
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(80, e.width - 4)))
        return label

    def _build_layout(self):
        self.root.title('DLSS5 离线工具 · 图片与视频工作台')
        self.root.geometry('1240x820')
        self.root.minsize(680, 520)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.workspace = ttk.Frame(self.root, padding=8)
        self.workspace.grid(row=0, column=0, sticky='nsew')
        self.preview_panel = ttk.Frame(self.workspace)
        self.inspector = ttk.Frame(self.workspace)
        self.inspector.pack_propagate(False)
        self.tabs = ttk.Notebook(self.inspector)
        self.tabs.pack(fill='both', expand=True)
        self.pages = {}
        for name in ('素材', '参数', '遮罩', '导出', '缓存'):
            page = ScrollPage(self.tabs)
            self.pages[name] = page
            self.tabs.add(page, text=' ' + name + ' ')
        logs = ttk.Frame(self.tabs)
        self.tabs.add(logs, text=' 日志 ')
        self.log = scrolledtext.ScrolledText(logs, width=1, height=1, state='disabled', font=('Consolas', 9))
        self.log.pack(fill='both', expand=True)
        self._build_media(self.pages['素材'].body)
        self._build_settings(self.pages['参数'].body)
        self._build_mask_controls(self.pages['遮罩'].body)
        self._build_export(self.pages['导出'].body)
        self._build_cache(self.pages['缓存'].body)
        self._build_preview(self.preview_panel)
        for page in self.pages.values():
            page.bind_wheel()
        progress = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        progress.grid(row=1, column=0, sticky='ew')
        self.pbar = ttk.Progressbar(progress, maximum=100)
        self.pbar.pack(fill='x')
        self.status = ttk.Label(progress, text='就绪', anchor='w')
        self.status.pack(fill='x')
        self.status.bind('<Configure>', lambda e: self.status.configure(wraplength=max(100, e.width - 8)))
        self._layout_mode = None
        self.workspace.bind('<Configure>', self._resize_workspace)

    def _resize_workspace(self, event):
        if event.widget is not self.workspace:
            return
        wide = event.width >= 1000
        mode = 'wide' if wide else 'stacked'
        if mode != self._layout_mode:
            self._layout_mode = mode
            for index in (0, 1):
                self.workspace.columnconfigure(index, weight=0, minsize=0)
                self.workspace.rowconfigure(index, weight=0, minsize=0)
            self.preview_panel.grid_forget()
            self.inspector.grid_forget()
            if wide:
                self.inspector.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
                self.preview_panel.grid(row=0, column=1, sticky='nsew')
                self.workspace.columnconfigure(1, weight=1)
                self.workspace.rowconfigure(0, weight=1)
            else:
                self.preview_panel.grid(row=0, column=0, sticky='nsew', pady=(0, 8))
                self.inspector.grid(row=1, column=0, sticky='nsew')
                self.workspace.columnconfigure(0, weight=1)
                self.workspace.rowconfigure(0, weight=1)
        if wide:
            self.inspector.configure(width=max(320, int(event.width * 0.30)), height=1)
        else:
            self.inspector.configure(width=1, height=max(170, int(event.height * 0.43)))

    def _build_media(self, parent):
        grid = self._section(parent, '导入素材')
        self._button(grid, '导入图片', self.import_image)
        self._button(grid, '导入视频', self.import_video)
        self._button(grid, '批量处理图片文件夹', self.process_images)
        self.vlabel = self._note(parent, '尚未导入素材')
        grid = self._section(parent, '生成与处理')
        self.depth_btn = self._button(grid, '生成深度', lambda: self.run_worker('depth'))
        self.flow_btn = self._button(grid, '生成光流', lambda: self.run_worker('flow'))
        self._button(grid, '运行 DLSS', lambda: self.run_worker('dlss'))
        self._note(parent, '图片导入后自动处理；单图使用无引导模式。视频的深度和光流结果会保存为缓存。')
        self._note(parent, '局部遮罩用于当前单张图片。批量图片和视频按“参数”面板设置处理。')

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        toolbar = FlowGrid(parent, minimum=120)
        toolbar.grid(row=0, column=0, sticky='ew')
        self.view_var = tk.StringVar(value='对比')
        self.view_cb = toolbar.add(ttk.Combobox(toolbar, textvariable=self.view_var,
            values=['原图', '光流', '深度', 'DLSS', '对比'], state='readonly', width=1))
        self.view_cb.bind('<<ComboboxSelected>>', lambda e: self.on_view_change())
        self._button(toolbar, '适应窗口', self.fit_image)
        self._button(toolbar, '原始大小 1:1', self.actual_image_size)
        self.zoom_label = toolbar.add(ttk.Label(toolbar, text='缩放 100%', anchor='center'))
        self.canvas = tk.Canvas(parent, bg='#161b22', highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=1, column=0, sticky='nsew', pady=4)
        self.canvas.bind('<Configure>', lambda e: self._schedule_image_render() if self.current_is_image else self.display_view())
        self._bind_image_canvas()
        self.preview_hint = ttk.Label(parent, text='导入图片后：滚轮缩放 · 左键拖动 · 在“遮罩”面板切换画笔', anchor='w')
        self.preview_hint.grid(row=2, column=0, sticky='ew')
        self.preview_hint.bind('<Configure>', lambda e: self.preview_hint.configure(wraplength=max(100, e.width - 4)))
        timeline = ttk.Frame(parent)
        timeline.grid(row=3, column=0, sticky='ew')
        timeline.columnconfigure(0, weight=1)
        self.fslider = tk.Scale(timeline, from_=0, to=1, orient='horizontal', showvalue=False,
            resolution=1, length=1, highlightthickness=0, command=lambda e: self.on_frame())
        self.fslider.grid(row=0, column=0, sticky='ew')
        self.flabel = ttk.Label(timeline, text='0', width=6, anchor='center')
        self.flabel.grid(row=0, column=1)
        self.play_btn = ttk.Button(timeline, text='播放', command=self.play)
        self.play_btn.grid(row=0, column=2, sticky='ew', padx=2)
        self.pause_btn = ttk.Button(timeline, text='暂停', command=self.pause)
        self.pause_btn.grid(row=0, column=3, sticky='ew', padx=2)
        timeline.columnconfigure(2, weight=1, uniform='playback')
        timeline.columnconfigure(3, weight=1, uniform='playback')

    def _build_settings(self, parent):
        for name, value in [('preset', 'Preset #1'), ('style', '默认'), ('guidance', '关闭'),
                            ('depthConv', '强制反转(0远)')]:
            setattr(self, 'v_' + name, tk.StringVar(value=value))
        for name in ('intensity', 'localTone', 'localStruct', 'skinStruct', 'motionSX', 'motionSY'):
            setattr(self, 'v_' + name, tk.DoubleVar(value=1.0))
        self.v_autoMask = tk.IntVar(value=1)
        self.v_uiCorr = tk.IntVar(value=0)
        grid = self._section(parent, '风格与强度')
        self._choice(grid, '预设', self.v_preset, ['Preset #1', 'Preset #2', 'Preset #3'], self.on_settings_change)
        self._choice(grid, '风格', self.v_style, ['默认', '自然', '电影', '风格3'], self.on_settings_change)
        for label, variable, high, step in [('处理强度', self.v_intensity, 1, .05),
            ('局部色调', self.v_localTone, 5, .1), ('局部结构', self.v_localStruct, 5, .1),
            ('皮肤结构', self.v_skinStruct, 5, .1)]:
            self._scale(grid, label, variable, 0, high, step, self.on_settings_change)
        grid = self._section(parent, '自动修正')
        grid.add(ttk.Checkbutton(grid, text='自动遮罩', variable=self.v_autoMask, command=self.on_settings_change))
        grid.add(ttk.Checkbutton(grid, text='界面元素校正', variable=self.v_uiCorr, command=self.on_settings_change))
        grid = self._section(parent, '视频引导与运动')
        self._choice(grid, '引导模式', self.v_guidance, ['深度+光流', '仅光流', '仅深度', '关闭'], self.on_settings_change)
        self._choice(grid, '深度约定', self.v_depthConv, ['使用输入标志', '强制正常(0近)', '强制反转(0远)'], self.on_settings_change)
        self._scale(grid, '水平运动缩放', self.v_motionSX, 0, 2, .1, self.on_settings_change)
        self._scale(grid, '垂直运动缩放', self.v_motionSY, 0, 2, .1, self.on_settings_change)

    def _build_export(self, parent):
        from media_export import CHANNELS
        self.v_export_format = tk.StringVar(value='视频')
        self.v_export_scope = tk.StringVar(value='当前帧')
        self.v_export_channels = {key: tk.IntVar(value=int(key == 'dlss')) for key in CHANNELS}
        self.v_export_crf = tk.IntVar(value=18)
        self.v_export_audio = tk.IntVar(value=1)
        self.v_output_duration = tk.StringVar(value='3')
        self.v_output_fps = tk.StringVar(value='30')
        grid = self._section(parent, '输出形式')
        self.export_format_cb = self._choice(grid, '输出类型', self.v_export_format,
            ['图片', '视频'], self._update_export_options)
        self.export_scope_cb = self._choice(grid, '视频转图片范围', self.v_export_scope,
            ['当前帧', '全部帧'], self._update_export_options)
        grid = self._section(parent, '导出内容（可多选，分别保存）')
        self.export_checks = {}
        for channel, label in CHANNELS.items():
            self.export_checks[channel] = grid.add(ttk.Checkbutton(grid, text=label,
                variable=self.v_export_channels[channel]))
        self.export_hint = self._note(parent, '')
        grid = self._section(parent, '视频参数')
        cell = ttk.Frame(grid)
        ttk.Label(cell, text='压缩质量 CRF（越低越清晰）').pack(fill='x')
        self.crf_input = ttk.Spinbox(cell, from_=0, to=51, textvariable=self.v_export_crf, width=1)
        self.crf_input.pack(fill='x', pady=3)
        grid.add(cell)
        self.audio_check = grid.add(ttk.Checkbutton(grid, text='保留原视频音轨', variable=self.v_export_audio))
        for label, variable, low, high, name in [('静态视频时长（秒）', self.v_output_duration, .1, 3600, 'duration_input'),
                ('静态视频帧率', self.v_output_fps, 1, 120, 'output_fps_input')]:
            cell = ttk.Frame(grid)
            ttk.Label(cell, text=label).pack(fill='x')
            entry = ttk.Spinbox(cell, from_=low, to=high, textvariable=variable, width=1)
            entry.pack(fill='x', pady=3)
            setattr(self, name, entry)
            grid.add(cell)
        grid = self._section(parent, '保存结果')
        self.export_btn = self._button(grid, '导出所选视频', self.export)
        self._note(parent, '选择保存位置后，每次导出新建文件夹，各通道单独命名。图片保存为 PNG，视频保存为 MP4；深度为灰度图，光流为彩色可视化。')
