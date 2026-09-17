"""Independent parameter panels for the first and optional second DLSS pass."""
import tkinter as tk
from tkinter import ttk

import dlss_layers
import dlss_runtime
import runtime_session


class LayerSettingsMixin:
    def _build_layer_settings(self, parent):
        self._build_parameter_presets(parent)
        versions = dlss_runtime.load_preferences()
        gpu = dlss_runtime.gpu_info()
        self._note(parent, '当前显卡：' + gpu['name'] + '。30 系兼容库为实验版本，请先运行模型自检。')
        self.v_second_enabled = tk.BooleanVar(value=False)
        self.v_overall_weight = tk.DoubleVar(value=100)
        self.v_edit_layer = tk.StringVar(value='第一层 DLSS')
        self.layer_vars = []
        self.layer_panels = []
        self.denoise_vars = {}
        self.denoise_scales = {}
        self.denoise_checks = {}
        grid = self._section(parent, '多重 DLSS')
        self.second_layer_check = grid.add(ttk.Checkbutton(grid, text='启用第二层 DLSS',
            variable=self.v_second_enabled, command=self.on_settings_change))
        self._scale(grid, '整体权重（%）', self.v_overall_weight, 0, 100, 1, self.on_settings_change)
        self.layer_selector = self._choice(grid, '编辑独立参数', self.v_edit_layer,
            ['第一层 DLSS', '第二层 DLSS'], self._show_selected_layer)
        self._note(parent, '处理顺序：原图 → 前置降噪 → 第一层 → 第二层（可选）→ 后置降噪 → 整体权重 → 局部遮罩。整体权重 0% 保留原图。')
        self._build_denoise_settings(parent, 'input_denoise', 'DLSS 前降噪',
            '先清理原素材的颗粒和色噪，再进入第一层 DLSS。')
        self.layer_container = ttk.Frame(parent)
        self.layer_container.pack(fill='x')
        for index in range(2):
            variables = {}
            variables['runtime_version'] = tk.StringVar(value=dlss_runtime.LABELS[versions[index]])
            for name, value in [('preset', 'Preset #1'), ('style', '默认'), ('guidance', '关闭'),
                                ('depthConv', '强制反转(0远)')]:
                variables[name] = tk.StringVar(value=value)
            for name in ('intensity', 'localTone', 'localStruct', 'skinStruct', 'motionSX', 'motionSY'):
                variables[name] = tk.DoubleVar(value=1.0)
            variables['autoMask'] = tk.IntVar(value=1)
            variables['uiCorr'] = tk.IntVar(value=0)
            self.layer_vars.append(variables)
            if index == 0:
                for name, variable in variables.items():
                    setattr(self, 'v_' + name, variable)
            panel = ttk.Frame(self.layer_container)
            self.layer_panels.append(panel)
            self._note(panel, ('第一层：处理输入画面，启用前置降噪时先清理素材。' if index == 0 else
                              '第二层：继续处理第一层的结果。参数始终独立保存，启用第二层后生效。'))
            grid = self._section(panel, 'DLSS 模型版本')
            self._choice(grid, '运行库版本（两层可独立选择）', variables['runtime_version'],
                         list(dlss_runtime.LABELS.values()), self._runtime_changed)
            self._button(grid, '自检此层模型', lambda i=index: self._test_runtime(i))
            self._note(panel, '自动：30 系选择 SF-v2，40 系选择原版。版本切换会重建渲染进程；模型预设仍独立控制画面风格。')
            grid = self._section(panel, '模型预设、风格与强度')
            self._choice(grid, '模型预设', variables['preset'], ['Preset #1', 'Preset #2', 'Preset #3'], self.on_settings_change)
            self._choice(grid, '风格', variables['style'], ['默认', '自然', '电影', '风格3'], self.on_settings_change)
            for label, name, high, step in [('处理强度', 'intensity', 1, .05),
                ('局部色调', 'localTone', 5, .1), ('局部结构', 'localStruct', 5, .1),
                ('皮肤结构', 'skinStruct', 5, .1)]:
                self._scale(grid, label, variables[name], 0, high, step, self.on_settings_change)
            grid = self._section(panel, '自动修正')
            grid.add(ttk.Checkbutton(grid, text='自动遮罩', variable=variables['autoMask'], command=self.on_settings_change))
            grid.add(ttk.Checkbutton(grid, text='界面元素校正', variable=variables['uiCorr'], command=self.on_settings_change))
            grid = self._section(panel, '视频引导与运动')
            self._choice(grid, '引导模式', variables['guidance'], ['深度+光流', '仅光流', '仅深度', '关闭'], self.on_settings_change)
            self._choice(grid, '深度约定', variables['depthConv'], ['使用输入标志', '强制正常(0近)', '强制反转(0远)'], self.on_settings_change)
            self._scale(grid, '水平运动缩放', variables['motionSX'], 0, 2, .1, self.on_settings_change)
            self._scale(grid, '垂直运动缩放', variables['motionSY'], 0, 2, .1, self.on_settings_change)
            self._note(panel, '视频两层共享原视频的深度和光流数据，各层的引导开关、约定及运动缩放独立。单图与批量图片均使用无引导模式。')
        self._build_denoise_settings(parent, 'output_denoise', 'DLSS 后降噪',
            '全部 DLSS 层处理完成后统一降噪一次，再应用整体权重和局部遮罩。')
        self._show_selected_layer()

    def _build_denoise_settings(self, parent, key, title, note):
        variables = {'enabled': tk.BooleanVar(value=False), 'luma': tk.DoubleVar(value=3),
                     'chroma': tk.DoubleVar(value=3), 'weight': tk.DoubleVar(value=100)}
        self.denoise_vars[key] = variables
        grid = self._section(parent, title)
        self.denoise_checks[key] = grid.add(ttk.Checkbutton(grid, text='启用' + title,
            variable=variables['enabled'], command=self.on_settings_change))
        self.denoise_scales[key] = [self._scale(grid, label, variables[name], 0, high, 1,
            self.on_settings_change) for label, name, high in
            [('亮度降噪强度', 'luma', 30), ('色彩降噪强度', 'chroma', 30), ('降噪权重（%）', 'weight', 100)]]
        variables['enabled'].trace_add('write', lambda *_: self._update_denoise_controls(key))
        self._update_denoise_controls(key)
        self._note(parent, note + ' 强度越高越容易损失细节；权重 0% 跳过、100% 完整应用。视频逐帧降噪会增加耗时。')

    def _update_denoise_controls(self, key):
        state = 'normal' if self.denoise_vars[key]['enabled'].get() and not self._busy else 'disabled'
        for scale in self.denoise_scales[key]:
            scale.configure(state=state)

    def _show_selected_layer(self, event=None):
        index = int(self.v_edit_layer.get() == '第二层 DLSS')
        for number, panel in enumerate(self.layer_panels):
            if number == index:
                panel.pack(fill='x')
            else:
                panel.pack_forget()
        self.pages['参数'].canvas.yview_moveto(0)

    @staticmethod
    def _read_single_layer(variables):
        return {'runtime_version': next(key for key, label in dlss_runtime.LABELS.items()
                                        if label == variables['runtime_version'].get()),
                'preset': int(variables['preset'].get().split('#')[-1]),
                'style': {'默认': 0, '自然': 1, '电影': 2, '风格3': 3}[variables['style'].get()],
                'intensity': float(variables['intensity'].get()),
                'local_tone': float(variables['localTone'].get()),
                'local_struct': float(variables['localStruct'].get()),
                'skin_struct': float(variables['skinStruct'].get()),
                'use_auto_mask': int(variables['autoMask'].get()),
                'ui_correction': int(variables['uiCorr'].get()),
                'guidance_mode': {'深度+光流': 3, '仅光流': 1, '仅深度': 2, '关闭': 0}[variables['guidance'].get()],
                'depth_convention': {'使用输入标志': 0, '强制正常(0近)': 1, '强制反转(0远)': 2}[variables['depthConv'].get()],
                'motion_scale_x': float(variables['motionSX'].get()),
                'motion_scale_y': float(variables['motionSY'].get())}

    @staticmethod
    def _write_single_layer(variables, settings):
        values = {'runtime_version': dlss_runtime.LABELS[settings.get('runtime_version', 'auto')],
                  'preset': 'Preset #' + str(settings['preset']),
                  'style': ['默认', '自然', '电影', '风格3'][settings['style']],
                  'guidance': ['关闭', '仅光流', '仅深度', '深度+光流'][settings['guidance_mode']],
                  'depthConv': ['使用输入标志', '强制正常(0近)', '强制反转(0远)'][settings['depth_convention']]}
        for name, key in [('intensity', 'intensity'), ('localTone', 'local_tone'),
                          ('localStruct', 'local_struct'), ('skinStruct', 'skin_struct'),
                          ('autoMask', 'use_auto_mask'), ('uiCorr', 'ui_correction'),
                          ('motionSX', 'motion_scale_x'), ('motionSY', 'motion_scale_y')]:
            values[name] = settings[key]
        for name, value in values.items():
            variables[name].set(value)

    def _read_layer_settings(self):
        result = self._read_single_layer(self.layer_vars[0])
        result['second_layer'] = self._read_single_layer(self.layer_vars[1]) if self.v_second_enabled.get() else None
        result['overall_weight'] = float(self.v_overall_weight.get()) / 100
        for key, variables in self.denoise_vars.items():
            result[key] = {'enabled': bool(variables['enabled'].get()),
                           'luma': float(variables['luma'].get()),
                           'chroma': float(variables['chroma'].get()),
                           'weight': float(variables['weight'].get()) / 100}
        return dlss_layers.normalize_settings(result)

    def _runtime_changed(self, event=None):
        if self._busy:
            return
        self.pause()
        self._close_live()
        try:
            versions = [self._read_single_layer(group)['runtime_version'] for group in self.layer_vars]
            dlss_runtime.save_preferences(*versions)
            info = dlss_runtime.resolve(versions[int(self.v_edit_layer.get() == '第二层 DLSS')])
            self.set_status('已选择模型：' + info['id'] + '；可运行自检或生成')
        except (OSError, ValueError) as error:
            self.set_status(str(error))
            self.logln(str(error))
        self.image_dlss = None
        self._split_frame = -1
        self.on_settings_change()

    def _test_runtime(self, index):
        if self._busy:
            return
        version = self._read_single_layer(self.layer_vars[index])['runtime_version']
        self.pause()
        self._close_live()
        def work():
            import json
            result = runtime_session.selftest(version)
            self.logln(json.dumps(result, ensure_ascii=False, indent=2))
            self.set_status(f'模型自检通过：{result["runtime"]["id"]}，3 帧耗时 {result["seconds"]} 秒（当前显卡）')
        self._in_thread(work)
