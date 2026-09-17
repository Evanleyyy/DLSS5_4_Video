#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.py — tkinter frontend for the test3 DLSS tool.

Pipeline: 导入视频 → 生成深度 (DAV2-Large, cached) → 生成光流 (RAFT-Large, cached)
          → 运行 DLSS (Feature 18) → 导出视频 (depth/flow/dlss).

Preview: 自适应分类面板、图片缩放/拖动、原图坐标遮罩、羽化与局部结果导出。

Run:  python3 gui.py
"""
import os
import sys
import threading
import queue
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import cv2
import numpy as np

import pipeline
import dlss_engine
import dlss_layers
from gui_layout import LayoutMixin
from image_editor_ui import ImageEditorMixin
from export_ui import ExportMixin
from cache_ui import CacheMixin
from dlss_settings_ui import LayerSettingsMixin
from sr_ui import SuperResolutionMixin
from preset_ui import PresetMixin
from processing_ui import ProcessingMixin
import sr_backend
import sr_settings
import task_control
import model_sessions
from playback_shortcuts import PlaybackShortcuts

VIEWS = ["原图", "光流", "深度", "DLSS", "对比"]


class App(ProcessingMixin, PresetMixin, SuperResolutionMixin, LayerSettingsMixin, CacheMixin, ExportMixin, ImageEditorMixin, LayoutMixin):
    def __init__(self, root):
        self.root = root
        self.video = None
        self.nframes = 0
        self.fps = 30.0
        self.thread = None
        self._ui_events = queue.Queue()
        self._closing = False
        self._busy = False
        self._task_control = None
        self._task_status = '就绪'
        self.current_is_image = False
        self.image_path = None
        self.image_bgr = None
        self.image_dlss = None
        self.split_x = 0.5
        self.playing = False
        self._live = None
        self._live_cache = None
        self._last_dlss_frame = -1
        self._live_debounce = None
        self.last_export = None
        self._init_processing()
        self._init_image_editor()
        self._build_layout()
        self._restore_parameter_presets()
        self._watch_processing_parameters()
        self._playback_shortcuts = PlaybackShortcuts(self)
        self._update_export_btn()
        root.after(30, self._drain_events)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _post(self, callback, *args):
        self._ui_events.put((callback, args))

    def _drain_events(self):
        for _ in range(100):
            try:
                callback, args = self._ui_events.get_nowait()
            except queue.Empty:
                break
            callback(*args)
        if self._closing and not (self.thread and self.thread.is_alive()):
            self._close_live()
            if getattr(self, '_cap', None):
                self._cap.release()
            self.root.destroy()
            return
        self.root.after(30, self._drain_events)

    def _on_close(self):
        self.pause()
        self._closing = True
        self._auto_pending = False
        if self._live_debounce is not None:
            self.root.after_cancel(self._live_debounce)
            self._live_debounce = None
        if self._task_control is not None:
            self._task_control.resume()
            self._refresh_task_control(self._task_control)
        self.set_status("等待当前任务结束后关闭...")

    def toggle_generation_pause(self):
        control = self._task_control
        if not self._busy or control is None or self._closing:
            return
        if control.state == 'running':
            control.pause()
            self.logln('已请求暂停；当前 GPU 步骤完成后暂停，保留处理进度。')
        elif control.state in ('pausing', 'paused'):
            control.resume()
            self.logln('继续当前生成任务。')
        self._refresh_task_control(control)

    def _refresh_task_control(self, control):
        if control is not self._task_control:
            return
        state = control.state if control is not None else 'finished'
        self.task_pause_btn.configure(
            text='继续生成' if state in ('pausing', 'paused') else '暂停生成',
            state='normal' if self._busy and state != 'finished' and not self._closing else 'disabled')
        prefix = {'pausing': '等待当前步骤结束后暂停… · ', 'paused': '已暂停 · '}.get(state, '')
        self.status.configure(text=prefix + self._task_status)

    def _set_busy(self, busy):
        self._busy = busy
        if busy:
            self._widget_states = []
            def visit(parent):
                for child in parent.winfo_children():
                    if child is self.task_pause_btn:
                        continue
                    if self._preview_task:
                        # Editing remains available while a single preview runs.
                        page = str(child)
                        editable = any(page.startswith(str(self.pages[name]) + '.')
                                       for name in ('参数', '超分', '遮罩'))
                        if (editable and not isinstance(child, ttk.Button)) or child in (
                                self.fslider, self.play_btn, self.pause_btn, self.view_cb):
                            continue
                    if isinstance(child, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Spinbox,
                                          ttk.Checkbutton, tk.Scale)):
                        self._widget_states.append((child, child.cget('state')))
                        child.configure(state='disabled')
                    visit(child)
            visit(self.root)
        else:
            for widget, state in self._widget_states:
                if widget.winfo_exists():
                    widget.configure(state=state)
            for key in self.denoise_vars:
                self._update_denoise_controls(key)
            self._split_frame = -1
            self._update_export_btn()
            self.display_view()
        self._refresh_task_control(self._task_control)

    def _with_audio(self):
        if threading.current_thread() is not threading.main_thread():
            return self._worker_audio
        return bool(self.v_export_audio.get())

    # ---------- helpers ----------
    def set_status(self, msg):
        if threading.current_thread() is not threading.main_thread():
            self._post(self.set_status, msg)
            return
        self._task_status = msg
        self._refresh_task_control(self._task_control)
        self.root.update_idletasks()

    def logln(self, msg):
        if threading.current_thread() is not threading.main_thread():
            self._post(self.logln, msg)
            return
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n"); self.log.see("end")
        self.log.config(state="disabled")

    def set_progress(self, i, total, extra=""):
        if threading.current_thread() is not threading.main_thread():
            self._post(self.set_progress, i, total, extra)
            return
        if total:
            self.pbar["maximum"] = total
            self.pbar["value"] = i
            self.set_status(f"{extra} {i}/{total}")
        self.root.update_idletasks()

    # ---------- preview ----------
    def load_view_img(self, view, frame):
        dm, fm, lm = pipeline.out_dirs(self.video)
        if view == "原图":
            cap = getattr(self, "_cap", None)
            if cap is None:
                cap = cv2.VideoCapture(self.video); self._cap = cap
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, f = cap.read()
            return f if ok else None
        if view == "光流":
            p = os.path.join(fm, f"{frame:06d}.flo")
            if os.path.exists(p):
                return pipeline.colorize_flow(pipeline.read_flo(p))
        if view == "深度":
            p = pipeline.find_depth(dm, frame)
            if p:
                return pipeline.colorize_depth(pipeline.read_depth(p))
        if view == "DLSS":
            return self._live_dlss_image(frame)
        return None

    def display_view(self):
        if self.current_is_image:
            self._display_image(); return
        if not self.video:
            self.canvas.delete('all')
            prompt = ('批量图片已选择，参数变化后自动处理' if self._pending_batch is not None else
                      '从“素材”面板导入图片或视频')
            self.canvas.create_text(max(1, self.canvas.winfo_width()) // 2,
                max(1, self.canvas.winfo_height()) // 2, text=prompt,
                fill='#97a3b5', font=('Microsoft YaHei', 12))
            return
        frame = int(self.fslider.get())
        view = self.view_var.get()
        self.canvas.delete("all")
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        if view == "对比":
            self._draw_split(frame, cw, ch); return
        img = self.load_view_img(view, frame)
        if img is None:
            msg = f"{view}：帧 {frame} 尚未生成 (先点对应按钮)"
            if view == "DLSS":
                msg = '正在自动更新当前帧预览…'
            self.canvas.create_text(cw // 2, ch // 2, text=msg,
                                    fill="#888888", font=("Microsoft YaHei", 11))
            return
        ih, iw = img.shape[:2]
        scale = min(cw / iw, ch / ih)
        nw = max(int(iw * scale), 1); nh = max(int(ih * scale), 1)
        nimg = cv2.resize(img, (nw, nh))
        from PIL import Image, ImageTk
        self._pilimg = Image.fromarray(cv2.cvtColor(nimg, cv2.COLOR_BGR2RGB))
        self._photo = ImageTk.PhotoImage(self._pilimg)
        self.canvas.create_image((cw - nw) // 2, (ch - nh) // 2, anchor="nw", image=self._photo)

    def on_frame(self):
        f = int(self.fslider.get())
        self.flabel.config(text=str(f))
        self.display_view()
        if self.video and not self.current_is_image:
            self._schedule_processing(delay=30, replace=not self.playing)

    def on_view_change(self):
        if self.view_var.get() == "对比":
            self.split_x = 0.5
        self.display_view()

    def _draw_split(self, frame, cw, ch):
        # cache the (letterboxed) original + DLSS per frame so dragging is smooth.
        # keyed by video too, so importing a NEW video invalidates it (else the
        # viewport keeps showing the previous clip when both are left on frame 0).
        if (getattr(self, "_split_frame", -1) != frame
                or getattr(self, "_split_size", None) != (cw, ch)
                or getattr(self, "_split_video", None) != self.video):
            orig = self.load_view_img("原图", frame)
            dlss = self.load_view_img("DLSS", frame)
            if orig is None:
                self.canvas.create_text(cw // 2, ch // 2, text=f"帧 {frame} 原图读取失败", fill="#888")
                return
            if dlss is None:
                self._draw_fit(orig, cw, ch)
                self.canvas.create_text(cw // 2, 16, text="正在更新当前帧预览…", fill="#888")
                return
            ih, iw = orig.shape[:2]
            scale = min(cw / iw, ch / ih)
            self._split_nw, self._split_nh = max(int(iw * scale), 1), max(int(ih * scale), 1)
            self._split_orig = cv2.resize(orig, (self._split_nw, self._split_nh))
            self._split_dlss = cv2.resize(dlss, (self._split_nw, self._split_nh))
            self._split_frame = frame; self._split_size = (cw, ch)
            self._split_video = self.video
        nw, nh = self._split_nw, self._split_nh
        o = self._split_orig.copy()
        sx = int(self.split_x * nw)
        o[:, sx:] = self._split_dlss[:, sx:]                       # right of divider = DLSS
        o[:, max(sx - 1, 0):min(sx + 1, nw)] = [0, 255, 255]       # divider line (BGR yellow)
        ox, oy = (cw - nw) // 2, (ch - nh) // 2
        self._drag_nw = nw; self._drag_offsetx = ox
        from PIL import Image, ImageTk
        self._pilimg = Image.fromarray(cv2.cvtColor(o, cv2.COLOR_BGR2RGB))
        self._photo = ImageTk.PhotoImage(self._pilimg)
        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo)

    def _draw_fit(self, img, cw, ch):
        ih, iw = img.shape[:2]
        scale = min(cw / iw, ch / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        nimg = cv2.resize(img, (nw, nh))
        from PIL import Image, ImageTk
        self._pilimg = Image.fromarray(cv2.cvtColor(nimg, cv2.COLOR_BGR2RGB))
        self._photo = ImageTk.PhotoImage(self._pilimg)
        self.canvas.delete("all")
        self.canvas.create_image((cw - nw) // 2, (ch - nh) // 2, anchor="nw", image=self._photo)

    def on_canvas_motion(self, event):
        if self.view_var.get() != "对比":
            return
        if not hasattr(self, "_drag_nw") or not hasattr(self, "_drag_offsetx"):
            return
        frac = (event.x - self._drag_offsetx) / max(self._drag_nw, 1)
        self.split_x = max(0.0, min(1.0, frac))
        self.display_view()

    # ---------- playback ----------
    def play(self):
        if not self.video:
            messagebox.showwarning("提示", "请先导入视频"); return
        self.playing = True
        if getattr(self, "_play_after", None):
            self.root.after_cancel(self._play_after)
        self._play()

    def _play(self):
        if not self.playing:
            return
        if self._preview_task and self._busy and self.view_var.get() in ('DLSS', '对比'):
            self._play_after = self.root.after(33, self._play)
            return
        nxt = int(self.fslider.get()) + 1
        if nxt > int(self.fslider.cget("to")):
            nxt = 0
        self.fslider.set(nxt)
        self.on_frame()
        interval = min(max(int(1000 / max(self.fps, 1)), 33), 100)
        self._play_after = self.root.after(interval, self._play)

    def pause(self):
        self.playing = False
        if getattr(self, "_play_after", None):
            self.root.after_cancel(self._play_after)
            self._play_after = None

    # ---------- live DLSS preview ----------
    def _settings_hash(self):
        return dlss_layers.settings_key(self._collect_settings())

    def _close_live(self, release_models=True):
        if release_models and hasattr(self, '_model_sessions'):
            self._model_sessions.close()
        if self._live:
            try:
                self._live.close()
            except Exception:
                pass
            self._live = None
        self._live_cache = None
        self._last_dlss_frame = -1

    def _live_dlss_image(self, frame):
        """Repaint only reads pixels; automatic work runs on the worker thread."""
        if self.video and pipeline.dlss_cache_matches(self.video, self._collect_settings(), frame):
            path = os.path.join(pipeline.out_dirs(self.video)[2], f'{frame:06d}.png')
            cached = pipeline.imread(path) if os.path.isfile(path) else None
            if cached is not None:
                return cached
        preview = self._video_preview_frame
        if preview is not None and preview[:2] == (self.video, frame):
            return preview[3]
        return None

    def _refresh_dlss(self):
        # Compatibility for callers that used to trigger live refresh.
        self._live_debounce = None
        self.on_settings_change()

    # ---------- DLSS settings ----------
    def _collect_settings(self):
        if threading.current_thread() is not threading.main_thread():
            return dlss_layers.normalize_settings(self._worker_settings)
        settings = self._read_layer_settings()
        if hasattr(self, 'sr_vars'):
            settings['super_resolution'] = self._read_sr_settings()
        return settings

    # ---------- actions ----------
    def import_video(self):
        p = filedialog.askopenfilename(filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
        if not p:
            return
        self.pause()
        self._close_live(release_models=False)
        self._split_frame = -1          # 换片后清掉旧的对比缓存，强制重建(否则视窗显示旧片)
        self._pending_batch = None
        self._reset_processing_result()
        self.current_is_image = False
        self.v_export_format.set("视频")
        self._reset_image_editor()
        self._update_export_btn()
        self.video = os.path.abspath(p)
        if getattr(self, "_cap", None):
            self._cap.release()
        self._cap = cv2.VideoCapture(self.video)
        n, fps, w, h = pipeline.video_info(self.video)
        self.nframes, self.fps = n, fps
        self.vlabel.config(text=f"{os.path.basename(self.video)}  ({n} 帧 @ {fps:.0f}fps {w}x{h})")
        self.fslider.config(to=max(n - 1, 1))
        self.fslider.set(0)
        self.flabel.config(text="0")
        try:
            self.display_view()
        except Exception as ex:
            self.logln(f"[preview] {ex}")
        self.logln(f"已导入: {self.video}  ({n} 帧)")
        self.on_settings_change()
        self.set_status('视频已导入，正在自动生成当前帧预览')

    def _update_export_btn(self):
        values = ['原图', 'DLSS', '对比'] if self.current_is_image else VIEWS
        self.view_cb.configure(values=values)
        if self.view_var.get() not in values:
            self.view_var.set('原图')
        if self._busy:
            return
        video_state = 'disabled' if self.current_is_image else 'normal'
        for widget in (self.depth_btn, self.flow_btn, self.play_btn, self.pause_btn,
                       self.fslider):
            widget.configure(state=video_state)
        self._update_export_options()
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Combobox, tk.Scale)):
                    child.configure(state=('readonly' if isinstance(child, ttk.Combobox) else 'normal')
                                    if self.current_is_image else 'disabled')
                visit(child)
        visit(self.pages['遮罩'].body)
        if not self.current_is_image:
            self.mask_status.configure(text='局部遮罩用于单张图片，请先导入图片。')
            self.zoom_label.configure(text='适应窗口')
            self.canvas.configure(cursor='')

    # ---------- single-image mode (guidance=off) ----------
    def import_image(self):
        p = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"), ("所有文件", "*.*")])
        if not p:
            return
        self.pause(); self._close_live(release_models=False)
        img = pipeline.imread(p, cv2.IMREAD_UNCHANGED)
        if img is None:
            messagebox.showerror("错误", "无法读取该图片"); return
        self.video = None
        self.current_is_image = True
        self.v_export_format.set("图片")
        self.image_path = os.path.abspath(p)
        self.image_alpha = img[..., 3].copy() if img.ndim == 3 and img.shape[2] == 4 else None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = img[..., :3].copy()
        self._pending_batch = None
        self._reset_processing_result()
        self.image_bgr = img
        self._reset_image_editor()
        self.nframes = 1; self.fps = 1.0
        self.vlabel.config(text=os.path.basename(self.image_path) + "  (图片)")
        self.flabel.config(text="0"); self.fslider.config(to=1); self.fslider.set(0)
        self.view_var.set("DLSS")
        self._update_export_btn()
        self.display_view()
        self.on_settings_change()
        self.set_status('图片已导入，正在自动渲染')

    def _image_dlss(self, img, defer_color=False):
        task_control.checkpoint()
        settings = self._collect_settings()
        if settings['super_resolution']['engine'] != 'dlss':
            return sr_backend.process_image(img, settings, self.set_progress)
        h, w = img.shape[:2]
        tw = max(8, ((w + 7) // 8) * 8); th = max(8, ((h + 7) // 8) * 8)
        pt = (th - h) // 2; pb = (th - h) - pt; pl = (tw - w) // 2; pr = (tw - w) - pl
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if (tw, th) != (w, h):
            rgb = cv2.copyMakeBorder(rgb, pt, pb, pl, pr, cv2.BORDER_REPLICATE)
        rgba = np.dstack([rgb, np.full((th, tw), 255, np.uint8)])
        s = dlss_layers.image_settings(self._collect_settings())
        if defer_color:
            # Confirmed single-image compositing applies color once after inference.
            # Video and batch paths apply it inside LayeredLive.
            s['color_preservation']['strength'] = 0
        live = model_sessions.acquire_dlss(tw, th, s)
        try:
            flow = np.zeros((th, tw, 2), np.float32)
            depth = np.zeros((th, tw), np.float32)
            o = live.process(rgba, flow, depth, reset=True)
            if o is None:
                return None
            bgr = cv2.cvtColor(o[..., :3], cv2.COLOR_RGB2BGR)
            if (tw, th) != (w, h):
                bgr = bgr[pt:pt + h, pl:pl + w]
            return bgr
        finally:
            live.close()

    def _export_image(self):
        try:
            self._require_confirmed_result()
        except ValueError as error:
            messagebox.showinfo('导出', str(error))
            return
        if self.image_dlss is None:
            messagebox.showinfo("提示", "还没有可导出的 DLSS 结果"); return
        base = os.path.splitext(os.path.basename(self.image_path or "image"))[0]
        p = filedialog.asksaveasfilename(title="导出 png", defaultextension=".png",
                                         initialfile=base + "_dlss.png", filetypes=[("PNG", "*.png")])
        if not p:
            return
        pipeline.imwrite(p, self._image_output())
        self.logln("导出图片: " + p)
        messagebox.showinfo("导出", "已导出: " + p)

    def _in_thread(self, fn, pausable=True, settings=None, preview=False):
        if self._closing:
            return
        if self._busy or (self.thread and self.thread.is_alive()):
            messagebox.showinfo("忙", "上一个任务还没结束"); return
        if not preview:
            self.pause()
            self._close_live(release_models=False)
        self._preview_task = preview
        self._worker_settings = dlss_layers.normalize_settings(settings) if settings is not None else self._collect_settings()
        self._worker_crf = self._export_crf()
        self._worker_audio = self._with_audio()
        control = task_control.PauseControl(lambda state: self._post(self._refresh_task_control, control)) if pausable else None
        self._task_control = control
        self._last_task_failed = False
        self._set_busy(True)
        def worker():
            try:
                with task_control.bind(control), model_sessions.bind(self._model_sessions):
                    task_control.checkpoint()
                    fn()
            except Exception as ex:
                self._last_task_failed = True
                self.logln("错误: " + str(ex))
                self.set_status("出错: " + str(ex)[:80])
                traceback.print_exc()
            finally:
                if control is not None:
                    control.finish()
                self._post(self._finish_task, control)
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def _finish_task(self, control):
        if control is self._task_control:
            self._task_control = None
            self._set_busy(False)
            self._preview_task = False
            if self._last_task_failed:
                self.pause()
                self.processing_note.configure(text='处理失败，上次成功结果仍保留。请检查错误，修改参数后会重新渲染。')
            if self._auto_pending:
                self._schedule_processing(delay=0)

    def run_worker(self, kind):
        if kind == 'dlss':
            self.confirm_processing()
            return
        if not self.video:
            messagebox.showwarning("提示", "请先导入视频"); return
        if kind in ("depth", "flow", "dlss"):
            self._in_thread(lambda: self._do(kind))

    def _do(self, kind):
        try:
            self.logln(f"开始: {kind} ...")
            self.set_status(f"{kind} 运行中...")
            if kind == "depth":
                pipeline.generate_depth(self.video, progress=lambda i, t, s: self.set_progress(i, t, "深度"))
                self.logln("深度完成 -> " + pipeline.out_dirs(self.video)[0])
            elif kind == "flow":
                pipeline.generate_flow(self.video, progress=lambda i, t, s: self.set_progress(i, t, "光流"))
                self.logln("光流完成 -> " + pipeline.out_dirs(self.video)[1])
            elif kind == "dlss":
                self._run_dlss_gui()
            self.set_status("完成")
            self._post(lambda: self.pbar.configure(value=0))
            self._post(self.display_view)   # refresh preview after generation
        except Exception as ex:
            self.logln("错误: " + str(ex))
            traceback.print_exc()
            self.set_status("出错: " + str(ex)[:80])

    def _guidance_inputs(self, idx, fr, gm):
        """Return (rgba, motion, depth) for one frame, honouring guidance mode
        gm: 0=关闭 1=仅光流 2=仅深度 3=深度+光流. Returns None if a needed cache is missing."""
        need_flow = gm in (1, 3)
        need_depth = gm in (2, 3)
        dm, fm, _ = pipeline.out_dirs(self.video)
        h, w = fr.shape[:2]
        dpath = pipeline.find_depth(dm, idx) if need_depth else None
        fpath = os.path.join(fm, f"{idx:06d}.flo") if need_flow else None
        if (need_flow and not os.path.exists(fpath)) or (need_depth and not dpath):
            return None
        depth = pipeline.read_depth(dpath) if need_depth else np.zeros((h, w), np.float32)
        flow = pipeline.read_flo(fpath) if need_flow else np.zeros((h, w, 2), np.float32)
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        rgba = np.dstack([rgb, np.full((h, w), 255, np.uint8)])
        return rgba, flow, depth

    def _run_dlss_gui(self):
        self.logln("逐帧处理 DLSS...")
        pipeline.generate_dlss(self.video, settings=self._collect_settings(),
            progress=lambda i, total, status: self.set_progress(i, total, "DLSS"))
        self.logln("DLSS 完成 -> " + pipeline.out_dirs(self.video)[2])
        self._post(self.view_var.set, "DLSS")

    # ---------- image-folder batch processing (guidance=off) ----------
    IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

    def process_images(self):
        """Pick an image folder; process every image with DLSS (no guidance) and write
        PNGs to a sibling '<folder>_dlss' directory next to the picked folder on disk."""
        d = filedialog.askdirectory(title="选择图片文件夹")
        if not d:
            return
        d = os.path.abspath(d)
        files = sorted(f for f in os.listdir(d)
                       if os.path.splitext(f)[1].lower() in self.IMG_EXTS
                       and os.path.isfile(os.path.join(d, f)))
        if not files:
            messagebox.showinfo("提示", "该文件夹没有可处理的图片"); return
        self.pause()
        self._close_live(release_models=False)
        self.current_is_image = False
        self.video = None
        self.image_bgr = self.image_path = None
        self._pending_batch = (d, files)
        self._reset_processing_result()
        self._reset_image_editor()
        self._update_export_btn()
        self.display_view()
        self.vlabel.configure(text=f'批量图片：{os.path.basename(d)}（{len(files)} 张）')
        self.on_settings_change()
        self.set_status('文件夹已选择，正在自动批量处理')

    def _do_images(self, d, files):
        live = None
        try:
            out_dir = os.path.join(os.path.dirname(d.rstrip("/\\")),
                                   os.path.basename(d) + "_dlss")
            os.makedirs(out_dir, exist_ok=True)
            self.logln("处理图片文件夹: " + d)
            self.logln("输出          -> " + out_dir)
            self.set_status("图片批处理 ...")
            s = dlss_layers.image_settings(self._collect_settings())
            if s['super_resolution']['engine'] != 'dlss':
                result = sr_backend.process_batch([os.path.join(d, name) for name in files], out_dir, s, self.set_progress)
                self.logln(f"图片超分完成：{result['count']} 张 -> {out_dir}")
                self.set_status('批量超分完成')
                return
            lw = lh = 0
            total = len(files); done = 0
            for i, fn in enumerate(files):
                task_control.checkpoint()
                p = os.path.join(d, fn)
                img = pipeline.imread(p, cv2.IMREAD_COLOR)
                if img is None:
                    self.logln("跳过(无法读取): " + fn); continue
                h, w = img.shape[:2]
                # pad UP to a multiple of 8 (DLSS texture stability), trim back after
                tw = max(8, ((w + 7) // 8) * 8); th = max(8, ((h + 7) // 8) * 8)
                pt = (th - h) // 2; pb = (th - h) - pt
                pl = (tw - w) // 2; pr = (tw - w) - pl
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                if (tw, th) != (w, h):
                    rgb = cv2.copyMakeBorder(rgb, pt, pb, pl, pr, cv2.BORDER_REPLICATE)
                rgba = np.dstack([rgb, np.full((th, tw), 255, np.uint8)])
                if live is None or lw != tw or lh != th:
                    if live: live.close()
                    live = model_sessions.acquire_dlss(tw, th, s); lw, lh = tw, th
                else:
                    live.update(s)
                flow = np.zeros((th, tw, 2), np.float32)
                depth = np.zeros((th, tw), np.float32)
                o = live.process(rgba, flow, depth, reset=True)
                if o is None:
                    self.logln("DLSS 失败: " + fn); continue
                bgr = cv2.cvtColor(o[..., :3], cv2.COLOR_RGB2BGR)
                if (tw, th) != (w, h):
                    bgr = bgr[pt:pt + h, pl:pl + w]
                pipeline.imwrite(os.path.join(out_dir, os.path.splitext(fn)[0] + ".png"), bgr)
                done += 1
                self.set_progress(i + 1, total, "图片")
            self.logln(f"图片批处理完成: {done}/{total} -> " + out_dir)
            self.set_status(f"完成: 处理 {done}/{total}")
        except Exception as ex:
            self.logln("图片批处理错误: " + str(ex))
            traceback.print_exc()
            self.set_status("出错: " + str(ex)[:80])
            raise
        finally:
            if live:
                live.close()

    def _export_crf(self):
        """Return the user-set x264 CRF (0-51; lower = higher quality)."""
        if threading.current_thread() is not threading.main_thread():
            return self._worker_crf
        try:
            crf = int(self.v_export_crf.get())
        except Exception:
            crf = 18
        return max(0, min(51, crf))



def _selftest():
    """Headless check uses the same isolated, architecture-selected renderer."""
    import json
    import runtime_session
    try:
        result = runtime_session.selftest('auto')
        text = 'DLSS_OK ' + json.dumps(result, ensure_ascii=False)
        passed = True
    except Exception as error:
        text = 'DLSS_ERR ' + str(error)
        passed = False
    with open('_selftest.txt', 'w', encoding='utf-8') as handle:
        handle.write(text)
    if not passed:
        raise SystemExit(1)


def main():
    if "--selftest" in sys.argv:
        _selftest(); return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
