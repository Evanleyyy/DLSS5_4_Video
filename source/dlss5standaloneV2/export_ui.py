"""Collect export options on the UI thread and dispatch a channel export job."""
import tkinter as tk
from tkinter import filedialog, messagebox

import media_export


class ExportMixin:
    def _update_export_options(self, event=None):
        if self._busy:
            return
        video = self.v_export_format.get() == '视频'
        image = self.current_is_image
        self.export_btn.configure(text='导出所选视频' if video else '导出所选图片')
        self.export_format_cb.configure(state='readonly')
        self.export_scope_cb.configure(state='readonly' if not image and not video else 'disabled')
        self.crf_input.configure(state='normal' if video else 'disabled')
        self.audio_check.configure(state='normal' if video and not image else 'disabled')
        for widget in (self.duration_input, self.output_fps_input):
            widget.configure(state='normal' if video and image else 'disabled')
        for channel, widget in self.export_checks.items():
            allowed = channel != ('flow' if image else 'mask')
            widget.configure(state='normal' if allowed else 'disabled')
            if not allowed:
                self.v_export_channels[channel].set(0)
        if image:
            text = ('将单张图片生成为静态 MP4，可设置时长和帧率；透明度在视频中不保留。' if video else '保存实际输出尺寸 PNG，处理结果包含局部遮罩和羽化。')
            text += ' 单张图片不包含光流信息。'
        else:
            text = '按原视频帧率导出完整 MP4。' if video else '可提取当前帧，也可将全部帧分别保存为 PNG 序列。'
        self.export_hint.configure(text=text + ' 每个勾选内容单独保存，缺少的深度、光流或 DLSS 会自动生成。')

    def _export_request(self, directory):
        image = self.current_is_image
        video = self.v_export_format.get() == '视频'
        if image and video:
            try:
                fps, duration = float(self.v_output_fps.get()), float(self.v_output_duration.get())
            except (ValueError, tk.TclError):
                raise ValueError('请填写有效的静态视频帧率和时长') from None
        else:
            fps, duration = (self.fps if not image else 30), 3
        request = {'source': self.image_path if image else self.video, 'is_image': image,
                   'format': self.v_export_format.get(), 'scope': self.v_export_scope.get(),
                   'channels': [key for key, value in self.v_export_channels.items() if value.get()],
                   'directory': directory, 'fps': fps, 'duration': duration,
                   'audio': bool(self.v_export_audio.get()), 'crf': self._export_crf(),
                   'frame': int(self.fslider.get()), 'frames': self.nframes}
        media_export.validate_request(request)
        if image:
            request['images'] = {'original': self.image_bgr}
            if 'dlss' in request['channels']:
                request['images']['dlss'] = self._image_output()
                if request['images']['dlss'] is None:
                    raise ValueError('还没有可导出的 DLSS 结果，请先运行 DLSS')
            if 'mask' in request['channels']:
                request['images']['mask'] = self.selection.alpha(self.v_feather.get()).copy()
        return request

    def export(self):
        if self._busy or (self.thread and self.thread.is_alive()):
            return
        if not (self.image_bgr is not None if self.current_is_image else self.video):
            messagebox.showwarning('提示', '请先导入图片或视频')
            return
        try:
            request = self._export_request(None)
        except ValueError as error:
            messagebox.showwarning('导出选项', str(error))
            return
        directory = filedialog.askdirectory(title='选择导出位置（每次导出新建独立文件夹）')
        if not directory:
            return
        request['directory'] = directory
        self._in_thread(lambda: self._run_export_request(request))

    def _run_export_request(self, request):
        result = media_export.export_channels(request, self._collect_settings(),
            progress=self.set_progress, log=self.logln)
        self._post(self._export_finished, result)

    def _export_finished(self, result):
        self.last_export = result
        self.set_status(f'导出完成：{len(result["channels"])} 个通道 → {result["directory"]}')
        self.logln('所有勾选内容导出完成')
