"""Named presets for both DLSS layers, denoising, and local SR engines."""
import tkinter as tk
from tkinter import ttk, messagebox

from gui_layout import FlowGrid
import parameter_presets
import sr_settings
import color_preservation


class PresetMixin:
    def _build_parameter_presets(self, parent):
        self._presets_ready = False
        self._applying_preset = False
        self._preset_items = []
        self._preset_id = None
        self._preset_store = None
        self.v_parameter_preset = tk.StringVar(value='')
        self.v_preset_name = tk.StringVar(value='')
        grid = self._section(parent, '参数预设')
        self.preset_selector = self._choice(grid, '已保存预设（选择即应用）', self.v_parameter_preset,
                                            [], self._apply_selected_preset)
        # Refresh on opening so other application windows can add presets safely.
        self.preset_selector.configure(postcommand=self._refresh_preset_choices)
        cell = ttk.Frame(grid)
        ttk.Label(cell, text='自定义名称').pack(fill='x')
        self.preset_name_entry = ttk.Entry(cell, textvariable=self.v_preset_name, width=1)
        self.preset_name_entry.pack(fill='x', pady=(3, 0))
        grid.add(cell)
        actions = FlowGrid(grid.master, minimum=135)
        actions.pack(fill='x')
        self.preset_add_btn = self._button(actions, '新增预设', lambda: self._save_parameter_preset(new=True))
        self.preset_save_btn = self._button(actions, '保存预设', self._save_parameter_preset)
        self.preset_apply_btn = self._button(actions, '应用预设', self._apply_selected_preset)
        self.preset_delete_btn = self._button(actions, '删除预设', self._delete_parameter_preset)
        self.preset_note = self._note(grid.master, '填写名称后新增；修改参数或名称后，点击保存预设。')
        self._note(parent, '新增：输入新名称；修改参数或改名：保存预设。\n包含双层 DLSS、降噪、原色彩保留、整体权重与超分设置。\n重启恢复最后保存或应用的预设。')
        self.v_preset_name.trace_add('write', lambda *_: self._update_preset_note())

    def _restore_parameter_presets(self):
        try:
            self._preset_store = parameter_presets.PresetStore()
            data = self._preset_store.load()
            self._set_preset_choices(data, data['active_id'])
            if self._preset_id is not None:
                self._apply_preset_parameters(self._current_preset()['parameters'])
        except (OSError, ValueError) as error:
            self.preset_note.configure(text=str(error))
            self.logln(str(error))
        self._presets_ready = True
        variables = [self.v_second_enabled, self.v_overall_weight,
                     self.v_color_preservation, self.v_color_mask_scope]
        for group in [*self.layer_vars, *self.denoise_vars.values(), self.sr_vars]:
            variables.extend(group.values())
        for variable in variables:
            variable.trace_add('write', lambda *_: self._update_preset_note())
        self._update_preset_note()

    def _get_preset_store(self):
        if self._preset_store is None:
            self._preset_store = parameter_presets.PresetStore()
        return self._preset_store

    def _current_preset(self):
        return next((item for item in self._preset_items if item['id'] == self._preset_id), None)

    def _set_preset_choices(self, data, identifier):
        self._preset_items = data['presets']
        self._preset_id = identifier
        self.preset_selector.configure(values=[item['name'] for item in self._preset_items])
        item = self._current_preset()
        self.v_parameter_preset.set(item['name'] if item else '')
        self.v_preset_name.set(item['name'] if item else '')

    def _refresh_preset_choices(self):
        if not self._busy:
            try:
                data = self._get_preset_store().load()
                self._preset_items = data['presets']
                self.preset_selector.configure(values=[item['name'] for item in self._preset_items])
            except (OSError, ValueError) as error:
                self.preset_note.configure(text=str(error))

    def _capture_preset_parameters(self):
        return parameter_presets.normalize_parameters({
            'settings': self._collect_settings(),
            'second_layer_parameters': self._read_single_layer(self.layer_vars[1])})

    def _update_preset_note(self):
        if not self._presets_ready or self._applying_preset:
            return
        item = self._current_preset()
        if item is None:
            return
        try:
            changed = (self.v_preset_name.get().strip() != item['name'] or
                       self._capture_preset_parameters() != item['parameters'])
            self.preset_note.configure(text=('参数或名称已修改，请点击“保存预设”。' if changed else
                                             '已保存：' + item['name']))
        except (ValueError, tk.TclError):
            self.preset_note.configure(text='请填写有效参数后保存预设。')

    def _preset_error(self, error):
        self.preset_note.configure(text=str(error))
        messagebox.showerror('参数预设', str(error), parent=self.root)

    def _save_parameter_preset(self, new=False):
        if self._busy or self._closing:
            return
        try:
            data = self._get_preset_store().save(self.v_preset_name.get(), self._capture_preset_parameters(),
                                           None if new else self._preset_id)
            self._set_preset_choices(data, data['active_id'])
            self._update_preset_note()
            self.set_status('参数预设已保存：' + self._current_preset()['name'])
        except (OSError, ValueError, tk.TclError) as error:
            self._preset_error(error)

    def _apply_selected_preset(self, event=None):
        if (self._busy and not self._preview_task) or self._closing:
            return
        previous = self._current_preset()
        try:
            item = next((item for item in self._preset_items if item['name'] == self.v_parameter_preset.get()), None)
            if item is None:
                raise ValueError('请先选择已保存的预设')
            data = self._get_preset_store().activate(item['id'])
            self._set_preset_choices(data, item['id'])
            self._apply_preset_parameters(self._current_preset()['parameters'])
            self._update_preset_note()
            self.set_status('已应用参数预设：' + self._current_preset()['name'])
        except (OSError, ValueError, tk.TclError) as error:
            self.v_parameter_preset.set(previous['name'] if previous else '')
            self._preset_error(error)

    def _apply_preset_parameters(self, parameters):
        parameters = parameter_presets.normalize_parameters(parameters)
        settings = parameters['settings']
        self.pause()
        self._applying_preset = True
        try:
            self._write_single_layer(self.layer_vars[0], settings)
            self._write_single_layer(self.layer_vars[1], parameters['second_layer_parameters'])
            import dlss_runtime
            dlss_runtime.save_preferences(settings['runtime_version'], parameters['second_layer_parameters']['runtime_version'])
            self.v_second_enabled.set(settings['second_layer'] is not None)
            self.v_overall_weight.set(settings['overall_weight'] * 100)
            self.v_color_preservation.set(settings['color_preservation']['strength'] * 100)
            self.v_color_mask_scope.set(color_preservation.MASK_SCOPES[settings['color_preservation']['mask_scope']])
            for key, variables in self.denoise_vars.items():
                for name, variable in variables.items():
                    variable.set(settings[key][name] * (100 if name == 'weight' else 1))
                self._update_denoise_controls(key)
            for key, variable in self.sr_vars.items():
                variable.set(settings['super_resolution'][key])
            self.v_sr_engine.set(sr_settings.ENGINES[settings['super_resolution']['engine']])
            self._show_sr_panels()
            self._close_live(release_models=False)
            self._split_frame = -1
        finally:
            self._applying_preset = False
        self.on_settings_change()

    def _delete_parameter_preset(self):
        if self._busy or self._closing:
            return
        item = self._current_preset()
        if item is None:
            self._preset_error(ValueError('请先选择要删除的预设'))
            return
        if not messagebox.askyesno('删除参数预设', '确定删除“' + item['name'] + '”？当前参数仍会保留。', parent=self.root):
            return
        try:
            data = self._get_preset_store().delete(item['id'])
            self._set_preset_choices(data, None)
            self.preset_note.configure(text='预设已删除，当前参数保留；可填写新名称重新保存。')
        except (OSError, ValueError) as error:
            self._preset_error(error)
