"""Responsive engine selection and independent restoration controls."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import sr_settings


class SuperResolutionMixin:
    def _build_super_resolution(self, parent):
        cfg = sr_settings.load_preferences()
        self.sr_vars = {}
        for key, value in cfg.items():
            self.sr_vars[key] = (tk.DoubleVar(value=value) if isinstance(value, float) else
                                 tk.IntVar(value=value) if isinstance(value, int) else tk.StringVar(value=value))
        self.v_sr_engine = tk.StringVar(value=sr_settings.ENGINES[cfg['engine']])
        grid = self._section(parent, '处理引擎')
        grid.minimum = 270
        self.sr_selector = self._choice(grid, '选择本地模型', self.v_sr_engine,
            list(sr_settings.ENGINES.values()), self._sr_engine_changed)
        self.sr_info = self._note(parent, '')
        self.sr_common = ttk.Frame(parent)
        grid = self._section(self.sr_common, '输出与显存')
        self._choice(grid, '放大倍率', self.sr_vars['scale'], [2, 4], self.on_settings_change)
        self._choice(grid, '分块大小（像素）', self.sr_vars['tile'], [256, 384, 512, 768, 1024], self.on_settings_change)
        self._choice(grid, '颜色校正', self.sr_vars['color'], ['wavelet', 'adain', 'nofix'], self.on_settings_change)
        cell = ttk.Frame(grid)
        ttk.Label(cell, text='随机种子（固定种子可复现）').pack(fill='x')
        entry = ttk.Spinbox(cell, textvariable=self.sr_vars['seed'], from_=0, to=2147483647,
                            width=1, command=self.on_settings_change)
        entry.pack(fill='x')
        entry.bind('<FocusOut>', self.on_settings_change)
        entry.bind('<Return>', self.on_settings_change)
        grid.add(cell)
        self._note(self.sr_common, '2 倍／4 倍改变实际导出尺寸。分块越小显存占用越低，耗时可能增加。修改参数后自动更新预览，导出视频时处理整段素材。')
        self.sr_pisa = ttk.Frame(parent)
        grid = self._section(self.sr_pisa, 'PiSA-SR 独立参数')
        self._scale(grid, '像素修复权重', self.sr_vars['pisa_pixel'], 0, 2, .05, self.on_settings_change)
        self._scale(grid, '语义细节权重', self.sr_vars['pisa_semantic'], 0, 2, .05, self.on_settings_change)
        self.sr_seed = ttk.Frame(parent)
        grid = self._section(self.sr_seed, 'SeedVR2 连续帧与显存')
        self._choice(grid, '每批连续帧数', self.sr_vars['batch'], [5, 9, 13, 17], self.on_settings_change)
        self._scale(grid, '卸载到内存的模型块数', self.sr_vars['blocks'], 0, 32, 1, self.on_settings_change)
        self._note(self.sr_seed, '使用 3B FP8 权重、原生注意力和 VAE 分块。16 GB 显存建议 5 帧、24 块卸载；跨批次保留连续帧上下文。较长批次更耗显存。')
        self.sr_paths = ttk.Frame(parent)
        grid = self._section(self.sr_paths, '本地模型目录')
        self._button(grid, '选择模型根目录', self._choose_sr_models)
        self._button(grid, '检查模型文件', self._check_sr_models)
        self._button(grid, '检查并补全所选模型', self._prepare_sr_models)
        self._button(grid, '恢复安装目录模型', self._reset_sr_models)
        self.sr_path_label = self._note(self.sr_paths, '')
        self._note(self.sr_paths, '优先复用本地模型；缺失或损坏的文件从官方源下载，支持断点续传。运行引擎前也会自动检查；模型齐全后可离线处理。')
        self._note(parent, '处理顺序：前置降噪 → 所选引擎 → 后置降噪 → 整体权重 → 图片局部遮罩。降噪和整体权重位于“参数”页；双层、引导与原色彩保留仅用于 DLSS。全部推理在本机完成。')
        self._show_sr_panels()

    def _read_sr_settings(self):
        return sr_settings.normalize({key: variable.get() for key, variable in self.sr_vars.items()})

    def _sr_engine_changed(self, event=None):
        self.sr_vars['engine'].set(next(key for key, label in sr_settings.ENGINES.items() if label == self.v_sr_engine.get()))
        self._show_sr_panels()
        self.on_settings_change()

    def _show_sr_panels(self):
        engine = self.sr_vars['engine'].get()
        for panel, visible in [(self.sr_common, engine != 'dlss'), (self.sr_pisa, engine == 'pisa'),
                               (self.sr_seed, engine == 'seedvr2'), (self.sr_paths, engine != 'dlss')]:
            panel.pack_forget()
            if visible:
                panel.pack(fill='x')
        tips = {'dlss': '原尺寸增强：保留已有双层 DLSS 及独立参数。',
                'pisa': '单步图片超分：像素修复与语义细节分别调节。支持单张与批量图片。',
                'seedvr2': '图片及连续帧视频超分：同一模型跨任务复用，切换模型或退出时释放。',
                'vosr': '单步图片超分：VOSR 2.0 1.4B，支持单张与批量图片。'}
        self.sr_info.configure(text=tips[engine])
        self.sr_path_label.configure(text=str(sr_settings.model_root(self._read_sr_settings())))

    def _choose_sr_models(self):
        path = filedialog.askdirectory(title='选择包含 pisa、seedvr2、vosr 子目录的模型根目录')
        if path:
            self.sr_vars['model_root'].set(path)
            self._show_sr_panels()
            self.on_settings_change()

    def _reset_sr_models(self):
        self.sr_vars['model_root'].set('')
        self._show_sr_panels()
        self.on_settings_change()

    def _check_sr_models(self):
        cfg = self._read_sr_settings()
        absent = sr_settings.missing(cfg)
        messagebox.showinfo('本地模型检查', ('缺少文件：\n' + '\n'.join(absent)) if absent else
            '所选引擎的必要文件已就绪。运行时会在本机加载并校验模型结构。')

    def _prepare_sr_models(self):
        import model_assets
        cfg = self._read_sr_settings()
        def prepare():
            result = model_assets.prepare([cfg['engine']], cfg, self.set_progress, force_verify=True)
            self.logln(f'模型准备完成：复用 {result["reused"]} 个文件，下载 {result["downloaded"]} 个文件。')
        self._in_thread(prepare)
