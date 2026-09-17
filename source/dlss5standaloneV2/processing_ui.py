"""Debounced automatic previews; one worker consumes only the latest UI snapshot."""
import json
import tkinter as tk
from tkinter import messagebox

import dlss_layers
import dlss_runtime
import pipeline
import sr_settings
import model_sessions
from image_editor import compose_result, feather_selection


class ProcessingMixin:
    def _init_processing(self):
        self._model_sessions = model_sessions.ModelSessions()
        self._confirmed_settings = None
        self._confirmed_source = None
        self._confirmed_mask_key = None
        self._confirmed_image = None
        self._confirmed_image_depth = None
        self._image_model_key = None
        self._pending_batch = None
        self._auto_enabled = True
        self._auto_pending = False
        self._preview_task = False
        self._last_auto_key = None
        self._video_preview = None
        self._video_preview_frame = None

    def _watch_processing_parameters(self):
        variables = [self.v_second_enabled, self.v_overall_weight, self.v_color_preservation,
                     self.v_color_mask_scope, self.v_mask_enabled, self.v_mask_mode, self.v_feather]
        for group in [*self.layer_vars, *self.denoise_vars.values(), self.sr_vars]:
            variables.extend(group.values())
        for variable in variables:
            variable.trace_add('write', self.on_settings_change)
        self.on_settings_change()

    def _source_key(self):
        if self._pending_batch is not None:
            folder, files = self._pending_batch
            return ('batch', folder, tuple(files))
        if self.current_is_image and self.image_bgr is not None:
            return ('image', self.image_path, id(self.image_bgr))
        return ('video', self.video) if self.video else None

    def _mask_key(self):
        return (bool(self.v_mask_enabled.get()), self.v_mask_mode.get(),
                float(self.v_feather.get()), self.selection.revision if self.selection is not None else None)

    def _require_confirmed_result(self):
        if (self._confirmed_source != self._source_key() or self._confirmed_settings is None
                or self._confirmed_settings != self._collect_settings()
                or (self.current_is_image and self._confirmed_mask_key != self._mask_key())):
            raise ValueError('最新参数或遮罩仍在渲染，请等待预览更新后再导出。')
        if self.current_is_image and self._confirmed_image is None:
            raise ValueError('尚无处理结果，请等待自动渲染完成后再导出。')

    def _auto_key(self):
        source = self._source_key()
        if source is None:
            return None
        return (source, json.dumps(self._collect_settings(), sort_keys=True),
                self._mask_key() if source[0] == 'image' else
                int(self.fslider.get()) if source[0] == 'video' else None)

    def _schedule_processing(self, delay=120, replace=True):
        if not self._auto_enabled or self._closing:
            return
        self._auto_pending = True
        if self._busy:
            return
        if self._live_debounce is not None:
            if not replace:
                return
            self.root.after_cancel(self._live_debounce)
        self._live_debounce = self.root.after(delay, self._auto_process)

    def _auto_process(self):
        self._live_debounce = None
        if not self._auto_enabled or self._closing:
            self._auto_pending = False
            return
        if self._busy:
            self._auto_pending = True
            return
        if self.thread and self.thread.is_alive():
            self._live_debounce = self.root.after(10, self._auto_process)
            return
        self._auto_pending = False
        try:
            key = self._auto_key()
            if key is None or key == self._last_auto_key:
                return
            settings = self._collect_settings()
            sr_settings.save_preferences(settings['super_resolution'])
            second = self._read_single_layer(self.layer_vars[1])
            dlss_runtime.save_preferences(settings['runtime_version'], second['runtime_version'])
        except (ValueError, TypeError, tk.TclError, OSError) as error:
            self.processing_note.configure(text='参数尚不完整：' + str(error))
            return
        self._last_auto_key = key
        source = key[0]
        if source[0] == 'video':
            index = key[2]
            self.processing_note.configure(text=f'正在更新第 {index} 帧预览，导出时处理整段视频。')
            self._in_thread(lambda: self._process_video_preview(source, settings, index, key),
                            settings=settings, preview=True)
        else:
            mask_key = self._mask_key() if source[0] == 'image' else None
            mask = self.selection.data.copy() if mask_key and mask_key[0] and self.selection is not None else None
            self.processing_note.configure(text='正在自动渲染，继续调参将只处理最新设置。')
            self._in_thread(lambda: self._process_confirmed(source, settings, mask_key, mask, []),
                            settings=settings, preview=source[0] == 'image')

    def _process_video_preview(self, source, settings, index, key):
        from video_preview import VideoPreview
        if self._video_preview is None:
            self._video_preview = VideoPreview()
        output = self._video_preview.render(source[1], index, settings, self.set_progress)
        self._post(self._publish_video_preview, source, settings, index, key, output)

    def _publish_video_preview(self, source, settings, index, key, output):
        if self._closing or self._source_key() != source:
            return
        try:
            current = self._auto_key() == key
        except (ValueError, TypeError, tk.TclError):
            current = False
        if not current:
            return
        self._video_preview_frame = (source[1], index, settings, output)
        self._split_frame = -1
        self.processing_note.configure(text='当前帧预览已更新；导出时按当前参数处理整段视频。')
        self.set_status('当前帧渲染完成')

    def confirm_processing(self):
        if self._busy or self._closing or (self.thread and self.thread.is_alive()):
            return
        source = self._source_key()
        if source is None:
            messagebox.showinfo('开始处理', '请先导入图片、视频或选择批量图片文件夹。', parent=self.root)
            return
        try:
            settings = self._collect_settings()
            mask_key = self._mask_key() if source[0] == 'image' else None
            mask = self.selection.data.copy() if source[0] == 'image' and self.selection is not None and mask_key[0] else None
            channels = [key for key, variable in self.v_export_channels.items() if variable.get()]
            sr_settings.save_preferences(settings['super_resolution'])
            second = self._read_single_layer(self.layer_vars[1])
            dlss_runtime.save_preferences(settings['runtime_version'], second['runtime_version'])
        except (ValueError, TypeError, tk.TclError, OSError) as error:
            messagebox.showwarning('参数无效', str(error), parent=self.root)
            return
        self.processing_note.configure(text='正在处理；本次任务使用固定的参数和选区。')
        self._in_thread(lambda: self._process_confirmed(source, settings, mask_key, mask, channels), settings=settings)

    def _process_confirmed(self, source, settings, mask_key, mask, channels):
        output = model = image_depth = model_key = None
        if source[0] == 'batch':
            self._do_images(source[1], source[2])
        elif source[0] == 'image':
            core = dlss_layers.image_settings(settings)
            core['color_preservation'] = {'strength': 0.0, 'mask_scope': 'result'}
            model_key = (source, dlss_layers.settings_key(core))
            reusable = settings['super_resolution']['engine'] == 'dlss' and self._image_model_key == model_key
            model = self.image_dlss if reusable else None
            if model is None:
                model = self._image_dlss(self.image_bgr, defer_color=True)
            if model is None:
                raise RuntimeError('模型未返回图片结果')
            alpha = feather_selection(mask, mask_key[2]) if mask is not None else None
            output = compose_result(self.image_bgr, model, settings, alpha,
                                    mask_key[1] == '保护涂抹区域', getattr(self, 'image_alpha', None))
            if self._confirmed_source == source:
                image_depth = self._confirmed_image_depth
            if 'depth' in channels and image_depth is None:
                import numpy as np
                image_depth = np.rint(pipeline.infer_depth_frame(self.image_bgr) * 65535).astype(np.uint16)
        else:
            need_depth, need_flow = dlss_layers.guidance_needs(settings)
            for needed, operation, name in (
                    (need_depth or 'depth' in channels, pipeline.generate_depth, '深度'),
                    (need_flow or 'flow' in channels, pipeline.generate_flow, '光流')):
                if needed:
                    operation(source[1], progress=lambda i, n, info, name=name: self.set_progress(i, n, name))
            pipeline.generate_dlss(source[1], settings=settings,
                progress=lambda i, n, info: self.set_progress(i, n, '处理结果'))
        self._post(self._publish_processed, source, settings, mask_key, output, model, model_key, image_depth)

    def _publish_processed(self, source, settings, mask_key, output, model, model_key, image_depth):
        # Keep reusable model pixels even if only a newer mask/color edit arrived.
        if getattr(self, '_preview_task', False):
            if source != self._source_key():
                return
            self.image_dlss, self._image_model_key = model, model_key
            try:
                current = settings == self._collect_settings() and mask_key == self._mask_key()
            except (ValueError, TypeError, tk.TclError):
                current = False
            if not current:
                return
        self._confirmed_source, self._confirmed_settings = source, settings
        self._confirmed_mask_key = mask_key
        if source[0] == 'image':
            self.image_dlss, self._image_model_key = model, model_key
            self._confirmed_image, self._confirmed_image_depth = output, image_depth
        self._split_frame = -1
        self.processing_note.configure(text='处理完成，修改参数或遮罩后自动更新。')
        self.set_status('批量处理完成，结果已保存到原文件夹旁的 _dlss 文件夹' if source[0] == 'batch' else '处理完成，可预览或导出')

    def _reset_processing_result(self):
        self._confirmed_source = self._confirmed_settings = self._confirmed_mask_key = None
        self._confirmed_image = self._confirmed_image_depth = self._image_model_key = None
        self.image_dlss = None
        self._last_auto_key = None
        self._video_preview_frame = None

    def on_settings_change(self, *args):
        if getattr(self, '_applying_preset', False) or self._closing:
            return
        if getattr(self, '_presets_ready', False):
            self._update_preset_note()
        if hasattr(self, 'processing_note'):
            self.processing_note.configure(text='参数已更新，正在安排自动渲染；预览保留上次结果。')
        self._schedule_processing()
