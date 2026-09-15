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
from gui_layout import LayoutMixin
from image_editor_ui import ImageEditorMixin
from export_ui import ExportMixin
from cache_ui import CacheMixin

VIEWS = ["原图", "光流", "深度", "DLSS", "对比"]


class App(CacheMixin, ExportMixin, ImageEditorMixin, LayoutMixin):
    def __init__(self, root):
        self.root = root
        self.video = None
        self.nframes = 0
        self.fps = 30.0
        self.thread = None
        self._ui_events = queue.Queue()
        self._closing = False
        self._busy = False
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
        self._init_image_editor()
        self._build_layout()
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
        self.set_status("等待当前任务结束后关闭...")

    def _set_busy(self, busy):
        self._busy = busy
        if busy:
            self._widget_states = []
            def visit(parent):
                for child in parent.winfo_children():
                    if isinstance(child, (ttk.Button, ttk.Combobox, ttk.Spinbox,
                                          ttk.Checkbutton, tk.Scale)):
                        self._widget_states.append((child, child.cget('state')))
                        child.configure(state='disabled')
                    visit(child)
            visit(self.root)
        else:
            for widget, state in self._widget_states:
                if widget.winfo_exists():
                    widget.configure(state=state)
            self._split_frame = -1
            self._update_export_btn()
            self.display_view()

    def _with_audio(self):
        if threading.current_thread() is not threading.main_thread():
            return self._worker_audio
        return bool(self.v_export_audio.get())

    # ---------- helpers ----------
    def set_status(self, msg):
        if threading.current_thread() is not threading.main_thread():
            self._post(self.set_status, msg)
            return
        self.status.config(text=msg); self.root.update_idletasks()

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
            img = self._live_dlss_image(frame)
            if img is not None:
                return img
            p = os.path.join(lm, f"{frame:06d}.png")
            if os.path.exists(p):
                return pipeline.imread(p)
        return None

    def display_view(self):
        if self.current_is_image:
            self._display_image(); return
        if not self.video:
            self.canvas.delete('all')
            self.canvas.create_text(max(1, self.canvas.winfo_width()) // 2,
                max(1, self.canvas.winfo_height()) // 2, text='从“素材”面板导入图片或视频',
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
                msg = "DLSS 预览：请先生成深度/光流，或把【引导模式】设为【关闭】即可实时生成"
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
                self.canvas.create_text(cw // 2, 16, text="先运行 DLSS 才能对比", fill="#888")
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
        s = self._collect_settings()
        return (s['preset'], s['style'], s['intensity'], s['local_tone'], s['local_struct'],
                s['skin_struct'], s['use_auto_mask'], s['ui_correction'], s['guidance_mode'],
                s['depth_convention'], s['motion_scale_x'], s['motion_scale_y'])

    def _ensure_live(self, w, h):
        try:
            if self._live is None:
                self._last_dlss_frame = -1
                self._live = dlss_engine.Live(w, h, self._collect_settings())
            else:
                self._live.update(self._collect_settings())
            return self._live
        except Exception as ex:
            self.logln("[DLSS 实时] " + str(ex))
            return None

    def _close_live(self):
        if self._live:
            try:
                self._live.close()
            except Exception:
                pass
            self._live = None
        self._live_cache = None
        self._last_dlss_frame = -1

    def _live_dlss_image(self, frame):
        if self.thread and self.thread.is_alive():
            return None
        sk = self._settings_hash()
        if self._live_cache and self._live_cache[0] == frame and self._live_cache[1] == sk:
            return self._live_cache[2]
        s = self._collect_settings()
        gm = s['guidance_mode']          # 0=关闭 1=仅光流 2=仅深度 3=深度+光流
        need_flow = gm in (1, 3)
        need_depth = gm in (2, 3)
        dm, fm, _ = pipeline.out_dirs(self.video)
        dpath = pipeline.find_depth(dm, frame) if need_depth else None
        fpath = os.path.join(fm, f"{frame:06d}.flo") if need_flow else None
        if (need_flow and not os.path.exists(fpath)) or (need_depth and not dpath):
            self._live_cache = None
            return None
        cap = getattr(self, '_cap', None)
        if cap is None:
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ok, fr = cap.read()
        if not ok:
            return None
        h, w = fr.shape[:2]
        live = self._ensure_live(w, h)
        if live is None:
            return None
        # guidance off -> pass zeros (the host zeroes them anyway); only load what's needed
        flow = pipeline.read_flo(fpath) if need_flow else np.zeros((h, w, 2), np.float32)
        depth = pipeline.read_depth(dpath) if need_depth else np.zeros((h, w), np.float32)
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        rgba = np.dstack([rgb, np.full((h, w), 255, np.uint8)])
        reset = 0 if frame == self._last_dlss_frame + 1 else 1
        o = live.process(rgba, flow, depth, reset=reset)
        self._last_dlss_frame = frame
        if o is None:
            self._live_cache = None
            return None
        bgr = cv2.cvtColor(o[..., :3], cv2.COLOR_RGB2BGR)
        self._live_cache = (frame, sk, bgr)
        return bgr

    def on_settings_change(self, event=None):
        if self._live_debounce:
            self.root.after_cancel(self._live_debounce)
        self._live_debounce = self.root.after(60, self._refresh_dlss)

    def _refresh_dlss(self):
        if self.thread and self.thread.is_alive():
            return
        self._live_debounce = None
        if self.current_is_image and self.image_bgr is not None:
            self._in_thread(self._image_worker)
            return
        if self._live:
            try:
                self._live.update(self._collect_settings())
            except Exception as ex:
                self.logln("[DLSS 参数] " + str(ex))
        self._live_cache = None
        self._split_frame = -1   # force split re-cache
        if self.view_var.get() in ("DLSS", "对比"):
            self.display_view()

    # ---------- DLSS settings ----------
    def _collect_settings(self):
        if threading.current_thread() is not threading.main_thread():
            return dict(self._worker_settings)
        style_map={"默认":0,"自然":1,"电影":2,"风格3":3}
        guid_map={"深度+光流":3,"仅光流":1,"仅深度":2,"关闭":0}
        dconv_map={"使用输入标志":0,"强制正常(0近)":1,"强制反转(0远)":2}
        try:
            preset=int(self.v_preset.get().split("#")[-1])
        except Exception:
            preset=1
        return {
            'preset': preset,
            'style': style_map.get(self.v_style.get(),0),
            'intensity': float(self.v_intensity.get()),
            'local_tone': float(self.v_localTone.get()),
            'local_struct': float(self.v_localStruct.get()),
            'skin_struct': float(self.v_skinStruct.get()),
            'use_auto_mask': int(self.v_autoMask.get()),
            'ui_correction': int(self.v_uiCorr.get()),
            'guidance_mode': guid_map.get(self.v_guidance.get(),3),
            'depth_convention': dconv_map.get(self.v_depthConv.get(),2),
            'motion_scale_x': float(self.v_motionSX.get()),
            'motion_scale_y': float(self.v_motionSY.get()),
        }

    # ---------- actions ----------
    def import_video(self):
        p = filedialog.askopenfilename(filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
        if not p:
            return
        self.pause()
        self._close_live()
        self._split_frame = -1          # 换片后清掉旧的对比缓存，强制重建(否则视窗显示旧片)
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
        self.set_status("就绪")

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
        self.pause(); self._close_live()
        img = pipeline.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            messagebox.showerror("错误", "无法读取该图片"); return
        self.video = None
        self.current_is_image = True
        self.v_export_format.set("图片")
        self.image_path = os.path.abspath(p)
        self.image_bgr = img
        self.image_dlss = None
        self._reset_image_editor()
        self.nframes = 1; self.fps = 1.0
        self.vlabel.config(text=os.path.basename(self.image_path) + "  (图片)")
        self.flabel.config(text="0"); self.fslider.config(to=1); self.fslider.set(0)
        self.view_var.set("DLSS")
        self._update_export_btn()
        self.display_view()
        self.set_status("图片 DLSS 生成中...")
        self._in_thread(self._image_worker)

    def _image_worker(self):
        try:
            self.image_dlss = self._image_dlss(self.image_bgr)
            if self.image_dlss is None:
                self.logln("图片 DLSS 失败，显示原图")
            self._post(lambda: (self.set_status("就绪"), self.display_view()))
        except Exception as ex:
            self.logln("图片 DLSS 错误: " + str(ex)); traceback.print_exc()
            self.set_status("出错: " + str(ex)[:80])

    def _image_dlss(self, img):
        h, w = img.shape[:2]
        tw = max(8, ((w + 7) // 8) * 8); th = max(8, ((h + 7) // 8) * 8)
        pt = (th - h) // 2; pb = (th - h) - pt; pl = (tw - w) // 2; pr = (tw - w) - pl
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if (tw, th) != (w, h):
            rgb = cv2.copyMakeBorder(rgb, pt, pb, pl, pr, cv2.BORDER_REPLICATE)
        rgba = np.dstack([rgb, np.full((th, tw), 255, np.uint8)])
        s = self._collect_settings(); s['guidance_mode'] = 0   # 无引导
        live = dlss_engine.Live(tw, th, s)
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

    def _in_thread(self, fn):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("忙", "上一个任务还没结束"); return
        self.pause()
        self._close_live()
        self._worker_settings = self._collect_settings()
        self._worker_crf = self._export_crf()
        self._worker_audio = self._with_audio()
        self._set_busy(True)
        def worker():
            try:
                fn()
            except Exception as ex:
                self.logln("错误: " + str(ex))
                self.set_status("出错: " + str(ex)[:80])
                traceback.print_exc()
            finally:
                self._post(self._set_busy, False)
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def run_worker(self, kind):
        if self.current_is_image and kind == 'dlss':
            self._in_thread(self._image_worker)
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
        self._in_thread(lambda: self._do_images(d, files))

    def _do_images(self, d, files):
        live = None
        try:
            out_dir = os.path.join(os.path.dirname(d.rstrip("/\\")),
                                   os.path.basename(d) + "_dlss")
            os.makedirs(out_dir, exist_ok=True)
            self.logln("处理图片文件夹: " + d)
            self.logln("输出          -> " + out_dir)
            self.set_status("图片批处理 ...")
            s = self._collect_settings(); s['guidance_mode'] = 0   # 强制无引导
            lw = lh = 0
            total = len(files); done = 0
            for i, fn in enumerate(files):
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
                    live = dlss_engine.Live(tw, th, s); lw, lh = tw, th
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
    """Headless DLSS smoke test (--selftest); writes result to _selftest.txt for a GUI-less verify."""
    try:
        import dlss_engine
        import numpy as np
        try:
            import torch
            tn = "torch=%s cuda=%s" % (torch.__version__, torch.cuda.is_available())
        except Exception as te:
            tn = "torch=ERR " + repr(te)
        W, H = 320, 240
        live = dlss_engine.Live(W, H, {'preset': 1, 'guidance_mode': 0})
        rgba = np.zeros((H, W, 4), np.uint8); rgba[..., 3] = 255
        o = live.process(rgba, np.zeros((H, W, 2), np.float32),
                         np.zeros((H, W), np.float32), reset=True)
        live.close()
        msg = "DLSS_OK " + (str((o.shape[0], o.shape[1], o.shape[2])) if o is not None else "None")
        msg += " | " + tn
    except Exception as ex:
        import traceback
        msg = "DLSS_ERR " + repr(ex) + "\n" + traceback.format_exc()
    with open("_selftest.txt", "w") as f:
        f.write(msg)


def main():
    if "--selftest" in sys.argv:
        _selftest(); return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
