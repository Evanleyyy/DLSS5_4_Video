"""Setup-local detection starts offline; missing weights require a download click."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import installer_models
import model_assets
import sr_settings


class InstallerModelWindow:
    def __init__(self, root, source, requested=(), auto_close=True):
        self.root, self.source, self.auto_close = root, str(source), auto_close
        self.cfg = sr_settings.load_preferences()
        self.previous_root = sr_settings.model_root(self.cfg)
        self.cfg['model_root'] = ''  # Install into this application's own model directory.
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None
        self.closing = self.busy = False
        self.result = None
        self.return_code = 2
        root.title('检查并安装本地模型')
        root.geometry('780x510')
        root.minsize(620, 470)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        self.wrapped_labels = []
        def label(text, pady=0):
            widget = ttk.Label(frame, text=text, wraplength=730)
            widget.pack(fill='x', pady=pady)
            self.wrapped_labels.append(widget)
            return widget
        label('自动检查安装包旁的模型；本地文件校验通过后直接安装，已有相同文件会跳过。', (0, 8))
        self.source_label = label('安装包位置：' + self.source)
        label('模型安装位置：' + str(sr_settings.model_root(self.cfg)), (0, 12))
        self.status = tk.StringVar(value='正在查找本地模型，此步骤不会联网。')
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=730)
        self.status_label.pack(fill='x', pady=(0, 8))
        self.wrapped_labels.append(self.status_label)
        self.rows = {}
        self.variables = {}
        group = ttk.LabelFrame(frame, text='缺失时可选择下载的模型', padding=10)
        group.pack(fill='both', expand=True)
        for engine, name in installer_models.COMPONENTS.items():
            variable = self.variables[engine] = tk.BooleanVar(value=engine in requested)
            widget = self.rows[engine] = ttk.Checkbutton(group, text=name + '：等待检查',
                variable=variable, command=self._update_download_button, state='disabled')
            widget.pack(anchor='w', fill='x', pady=5)
        self.bar = ttk.Progressbar(frame)
        self.bar.pack(fill='x', pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        self.choose_button = ttk.Button(buttons, text='选择其他本地目录', command=self._choose, state='disabled')
        self.choose_button.pack(side='left')
        self.download_button = ttk.Button(buttons, text='下载所选缺失模型', command=self.download, state='disabled')
        self.download_button.pack(side='left', padx=10)
        self.close_button = ttk.Button(buttons, text='稍后下载', command=self.close)
        self.close_button.pack(side='right')
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame.bind('<Configure>', lambda event: [widget.configure(wraplength=max(400, event.width - 32))
                                                for widget in self.wrapped_labels])
        root.after(50, self.start_local)
        root.after(80, self._poll)

    def _checkpoint(self):
        if self.cancel.is_set():
            raise InterruptedError('已停止模型准备。')

    def _progress(self, done, total, message):
        self.messages.put(('progress', (done, total, message)))

    def _launch(self, work):
        if self.busy or self.closing:
            return
        self.busy = True
        self.cancel.clear()
        self.download_button.configure(state='disabled')
        self.choose_button.configure(state='disabled')
        for row in self.rows.values():
            row.configure(state='disabled')
        def worker():
            try:
                work()
            except InterruptedError:
                self.messages.put(('stopped', None))
            except Exception as error:
                self.messages.put(('error', str(error)))
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def _local(self):
        return installer_models.install_local(self.source, settings=self.cfg, progress=self._progress,
            checkpoint=self._checkpoint, extra_roots=[self.previous_root])

    def start_local(self):
        self.status.set('正在检查并安装本地模型，此步骤不会联网。')
        self._launch(lambda: self.messages.put(('local_done', self._local())))

    def _choose(self):
        path = filedialog.askdirectory(parent=self.root, title='选择已有模型根目录')
        if path:
            self.source = path
            self.source_label.configure(text='模型来源：' + path)
            self.start_local()

    def _update_download_button(self):
        selected = [key for key, variable in self.variables.items() if variable.get() and self.result
                    and self.result['components'][key]['missing']]
        size = sum(self.result['components'][key]['missing_bytes'] for key in selected) if self.result else 0
        self.download_button.configure(
            text=f'下载所选缺失模型（{size / 1024**3:.2f} GiB）' if selected else '下载所选缺失模型',
            state='normal' if selected and not self.busy else 'disabled')

    def download(self):
        if self.busy or self.result is None:
            return
        selected = [key for key, variable in self.variables.items()
                    if variable.get() and self.result['components'][key]['missing']]
        if not selected:
            return
        self.status.set('正在下载已选择的缺失文件，完整的本地模型会继续复用。')
        def work():
            errors = []
            for engine in selected:
                self._checkpoint()
                try:
                    model_assets.prepare([engine], self.cfg, self._progress, checkpoint=self._checkpoint)
                except InterruptedError:
                    raise
                except Exception as error:
                    errors.append(str(error))
            self.messages.put(('download_done', (self._local(), errors)))
        self._launch(work)

    def _show_result(self, result, initial=False, errors=()):
        self.result, self.busy = result, False
        self.return_code = 2 if result['missing'] else 0
        self.close_button.configure(text='稍后下载' if result['missing'] else '完成')
        sr_settings.save_preferences(self.cfg)
        for engine, component in result['components'].items():
            missing = len(component['missing'])
            if missing:
                text = f'{component["name"]}：缺少 {missing} 个文件，下载约 {component["missing_bytes"] / 1024**3:.2f} GiB'
            else:
                text = component['name'] + '：已就绪'
                self.variables[engine].set(False)
            self.rows[engine].configure(text=text, state='normal' if missing else 'disabled')
        self.choose_button.configure(state='normal')
        if errors:
            self.status.set('部分模型未能下载，可重试或稍后下载：\n' + '\n'.join(errors))
        elif result['missing']:
            self.status.set(f'已复用 {result["reused"]} 个文件，安装本地文件 {result["copied"]} 个。'
                            '仍有模型缺失，请勾选需要的模型后下载；也可稍后下载，不影响普通 DLSS 使用。')
        else:
            self.return_code = 0
            self.status.set(f'模型已就绪：复用 {result["reused"]} 个文件，安装本地文件 {result["copied"]} 个。')
            self.close_button.configure(text='完成')
            if initial and self.auto_close:
                self.root.after(500, self.close)
        self._update_download_button()

    def _poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'progress':
                    done, total, message = value
                    if not self.closing:
                        self.bar.configure(maximum=max(1, total), value=done)
                        self.status.set(message)
                else:
                    self.busy = False
                    if self.closing:
                        self.root.destroy()
                        return
                    if kind == 'local_done':
                        self._show_result(value, initial=True)
                    elif kind == 'download_done':
                        self._show_result(value[0], errors=value[1])
                    else:
                        self.status.set('本地模型准备未完成：' + str(value or '已停止'))
                        self.choose_button.configure(state='normal')
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel.set()
            self.close_button.configure(state='disabled')
            self.status.set('正在停止；下载进度会保留，本地原模型不会删除。')
        else:
            self.root.destroy()


def run(source, requested=(), silent=False):
    if silent:
        cfg = sr_settings.load_preferences()
        previous_root = sr_settings.model_root(cfg)
        cfg['model_root'] = ''
        result = installer_models.install_local(source, settings=cfg, extra_roots=[previous_root])
        sr_settings.save_preferences(cfg)
        print(f'本地模型：复用 {result["reused"]}，安装 {result["copied"]}，缺失 {len(result["missing"])}；静默安装不下载。')
        return 0 if not result['missing'] else 2
    root = tk.Tk()
    window = InstallerModelWindow(root, source, requested)
    root.mainloop()
    return window.return_code
