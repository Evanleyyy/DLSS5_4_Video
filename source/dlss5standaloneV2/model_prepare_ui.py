"""Setup's optional model preparation window; no GPU dependencies required."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import model_assets
import sr_settings


def run(engines):
    root = tk.Tk()
    root.title('准备本地模型')
    root.geometry('720x400')
    root.minsize(520, 320)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='先检查本地模型，仅下载缺失或损坏的文件。模型就绪后可断网使用。',
              wraplength=660).pack(fill='x', pady=(0, 10))
    cfg = sr_settings.load_preferences()
    path = tk.StringVar(value=str(sr_settings.model_root(cfg)))
    field = ttk.Entry(frame, textvariable=path)
    field.pack(fill='x')
    status = tk.StringVar(value='请选择已有模型根目录，或直接开始检查。')
    label = ttk.Label(frame, textvariable=status, wraplength=660)
    label.pack(fill='both', expand=True, pady=10)
    frame.bind('<Configure>', lambda e: label.configure(wraplength=max(300, e.width - 32)))
    bar = ttk.Progressbar(frame)
    bar.pack(fill='x', pady=8)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')
    messages = queue.Queue()
    cancel = threading.Event()
    thread = None
    closing = False
    success = False

    def choose():
        selected = filedialog.askdirectory(parent=root, title='选择已有模型根目录')
        if selected:
            path.set(selected)

    choose_button = ttk.Button(buttons, text='选择已有目录', command=choose)
    choose_button.pack(side='left', expand=True, fill='x')

    def checkpoint():
        if cancel.is_set():
            raise InterruptedError('下载已中断，已保留进度。')

    def start():
        nonlocal thread
        cfg['model_root'] = path.get().strip()
        sr_settings.save_preferences(cfg)
        cancel.clear()
        start_button.configure(state='disabled')
        choose_button.configure(state='disabled')
        field.configure(state='disabled')
        def worker():
            errors = []
            totals = {'reused': 0, 'downloaded': 0}
            for engine in engines:
                try:
                    checkpoint()
                    result = model_assets.prepare([engine], cfg,
                        lambda i, total, stage: messages.put(('progress', (i, total, stage))), checkpoint=checkpoint)
                    for key in totals:
                        totals[key] += result[key]
                except InterruptedError:
                    break
                except Exception as error:
                    errors.append(str(error))
            messages.put(('done', (totals, errors)))
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    start_button = ttk.Button(buttons, text='检查并准备模型', command=start)
    start_button.pack(side='left', expand=True, fill='x', padx=8)

    def close():
        nonlocal closing
        if thread and thread.is_alive():
            closing = True
            cancel.set()
            status.set('正在停止下载并保留进度；等待当前网络读取结束，最多约 30 秒。')
        else:
            root.destroy()

    close_button = ttk.Button(buttons, text='稍后准备', command=close)
    close_button.pack(side='left', expand=True, fill='x')
    root.protocol('WM_DELETE_WINDOW', close)

    def poll():
        nonlocal success
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == 'progress' and not closing:
                    i, total, stage = value
                    bar.configure(maximum=max(1, total), value=i)
                    status.set(stage)
                elif kind == 'done':
                    totals, errors = value
                    success = not errors and not cancel.is_set()
                    status.set(('部分模型未就绪，可点击重试或稍后在“超分”页补全：\n' + '\n'.join(errors)) if errors else
                               f'模型已就绪：复用 {totals["reused"]} 个文件，下载 {totals["downloaded"]} 个文件。')
                    start_button.configure(state='normal', text='重新检查')
                    choose_button.configure(state='normal')
                    field.configure(state='normal')
                    close_button.configure(text='完成' if success else '稍后准备')
                    if closing:
                        root.destroy()
                        return
        except queue.Empty:
            pass
        root.after(100, poll)

    poll()
    root.mainloop()
    return 0 if success else 2
