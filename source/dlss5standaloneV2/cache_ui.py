"""Manual cache controls built with the same responsive layout as the editor."""
import os
import tkinter as tk
from tkinter import ttk, messagebox

import cache_manager


class CacheMixin:
    def _build_cache(self, parent):
        self._cache_rows = []
        self._note(parent, '普通退出会保留缓存。先刷新列表，再勾选需要清理的内容。原始素材、手动导出的图片和视频、日志均保留。')
        grid = self._section(parent, '手动清理')
        self.cache_scan_btn = self._button(grid, '刷新缓存列表', self.refresh_caches)
        self.cache_clean_btn = self._button(grid, '清理勾选缓存', self.clean_selected_caches)
        self.cache_cancel_btn = self._button(grid, '取消待清理任务', self.cancel_cache_cleanup)
        self.cache_summary = self._note(parent, '尚未扫描。运行缓存位置：\n' + str(cache_manager.runtime_root()))
        self.cache_list = ttk.Frame(parent)
        self.cache_list.pack(fill='x')
        self._note(parent, '安装版直接使用安装目录的运行库，安装文件和模型不会列为缓存。旧单 EXE 的解压环境仍由旧启动器管理。超分临时输入与任务日志可以单独清理。')
        self._note(parent, '视频缓存：只列出当前视频的深度、光流和 DLSS 中间帧，清理后需重新生成。正在其他窗口处理的视频会跳过清理。')

    def refresh_caches(self):
        if self._busy:
            return
        video = self.video
        self.set_status('正在统计缓存占用…')
        self._in_thread(lambda: self._scan_cache_worker(video), pausable=False)

    def _scan_cache_worker(self, video):
        entries, warnings = cache_manager.inventory(video)
        self._post(self._show_cache_inventory, entries, warnings)
        self.set_status('缓存列表已更新')

    def _show_cache_inventory(self, entries, warnings):
        for child in self.cache_list.winfo_children():
            child.destroy()
        self._cache_rows = []
        labels = {'runtime': '运行环境', 'depth': '视频深度', 'flow': '视频光流', 'dlss': '视频处理结果', 'sr': '超分临时文件'}
        for entry in entries:
            frame = ttk.LabelFrame(self.cache_list, text=labels[entry['type']], padding=6)
            frame.pack(fill='x', pady=(0, 8))
            selected = tk.IntVar(value=0)
            check = ttk.Checkbutton(frame, text='选择清理 · ' + cache_manager.format_size(entry['size']), variable=selected)
            check.pack(fill='x')
            state = ('当前版本' if entry.get('current') else '历史版本') if entry['type'] == 'runtime' else '可重新生成的中间帧'
            if entry.get('pending'):
                state += ' · 已安排关闭后清理'
            self._note(frame, state + '\n' + entry['path'])
            if entry['type'] == 'runtime' and not os.environ.get('DLSS5_LAUNCHER_PATH'):
                check.configure(state='disabled')
                self._note(frame, '请使用新版独立 EXE 执行清理。')
            self._cache_rows.append((selected, entry))
        total = sum(entry['size'] for entry in entries)
        self.cache_summary.configure(text=f'共 {len(entries)} 项，可清理文件合计 {cache_manager.format_size(total)}' +
                                     ('\n部分目录已跳过，详情见日志。' if warnings else ''))
        for warning in warnings:
            self.logln('缓存扫描：' + warning)
        self.pages['缓存'].bind_wheel()

    def clean_selected_caches(self):
        if self._busy:
            return
        entries = [entry for selected, entry in self._cache_rows if selected.get()]
        if not entries:
            messagebox.showinfo('清理缓存', '请先刷新列表并勾选要清理的缓存。')
            return
        self.set_status('正在清理勾选缓存…')
        self._in_thread(lambda: self._clean_cache_worker(entries), pausable=False)

    def cancel_cache_cleanup(self):
        if self._busy:
            return
        entries = [entry for _, entry in self._cache_rows if entry.get('pending')]
        if not entries:
            messagebox.showinfo('清理缓存', '列表中没有待清理任务，请先刷新确认。')
            return
        self._in_thread(lambda: self._clean_cache_worker(entries, cancel=True), pausable=False)

    def _clean_cache_worker(self, entries, cancel=False):
        if not cancel and any(entry['type'] == 'sr' for entry in entries):
            import model_sessions
            owner = model_sessions.current()
            if owner is not None:
                owner.close_sr()
        freed, deferred, skipped = 0, 0, 0
        for entry in entries:
            try:
                if entry['type'] == 'runtime':
                    code, text = cache_manager.clean_runtime(entry, cancel)
                    if code == 0 and not cancel:
                        freed += entry['size']
                    deferred += int(code == 2)
                    skipped += int(code == 3)
                elif entry['type'] == 'sr':
                    freed += cache_manager.clean_sr_cache(entry)
                    text = '超分临时文件已清理；模型和导出文件保留。'
                else:
                    freed += cache_manager.clean_video(entry['video'], entry['type'])
                    text = '视频缓存已清理；需要时可重新生成。'
                    self._post(self._after_video_cache_cleanup, entry['video'])
                self.logln(entry['path'] + '\n' + text)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                skipped += 1
                self.logln('清理未完成：' + entry['path'] + '\n' + str(error))
        entries, warnings = cache_manager.inventory(self.video)
        self._post(self._show_cache_inventory, entries, warnings)
        summary = ('待清理任务已取消' if cancel else f'已释放约 {cache_manager.format_size(freed)}；{deferred} 项等待关闭后清理')
        if skipped:
            summary += f'；{skipped} 项未清理，详情见日志'
        self.set_status(summary)
        self.logln(summary)

    def _after_video_cache_cleanup(self, video):
        if self.video == video:
            self._live_cache = None
            self._split_frame = -1
            self._last_dlss_frame = -1
            self.view_var.set('原图')
