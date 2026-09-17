"""Window-local playback shortcuts, ahead of native button space bindings."""
import tkinter as tk
from tkinter import ttk


class PlaybackShortcuts:
    _MOUSE_CONTROLS = (tk.Button, ttk.Button, tk.Checkbutton, ttk.Checkbutton,
                       tk.Radiobutton, ttk.Radiobutton, tk.Menubutton, ttk.Menubutton,
                       tk.Scale, ttk.Scale, ttk.Notebook, ttk.Combobox)

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._down = False
        # Tk on Windows uses 0x0008 for Num Lock, while Alt uses 0x20000.
        # On X11, 0x0008 is Alt. Lock keys must not disable playback shortcuts.
        windows = self.root.tk.call('tk', 'windowingsystem') == 'win32'
        self._modifier_mask = 0x0005 | (0x20000 if windows else 0x0008)
        self._tag = 'PlaybackSpace_' + str(self.root.winfo_id())
        self.root.bind_class(self._tag, '<KeyPress-space>', self._press)
        self.root.bind_class(self._tag, '<KeyRelease-space>', self._release)
        self.root.bind_class(self._tag, '<ButtonRelease-1>', self._mouse_release)
        self.root.bind_class(self._tag, '<<ComboboxSelected>>', self._mouse_release)
        self.root.bind('<Map>', self._mapped, add='+')
        self.root.bind('<FocusOut>', self._focus_out, add='+')
        self._attach_tree(self.root)

    def _attach(self, widget):
        # Dialogs keep their own keyboard behavior.
        if widget.winfo_toplevel() is self.root:
            tags = widget.bindtags()
            if self._tag not in tags:
                widget.bindtags((self._tag, *tags))
            if self._is_mouse_control(widget):
                widget.configure(takefocus=False)

    def _attach_tree(self, widget):
        self._attach(widget)
        for child in widget.winfo_children():
            self._attach_tree(child)

    def _mapped(self, event):
        if isinstance(event.widget, tk.Misc):
            self._attach(event.widget)

    def _is_input(self, widget):
        if not isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox,
                                   ttk.Spinbox, ttk.Combobox)):
            return False
        if isinstance(widget, ttk.Widget):
            return widget.instate(['!disabled', '!readonly'])
        return str(widget.cget('state')) == 'normal'

    def _is_mouse_control(self, widget):
        return isinstance(widget, self._MOUSE_CONTROLS) and not self._is_input(widget)

    def _mouse_release(self, event):
        if self._is_mouse_control(event.widget):
            # Let the native click/selection and its command finish first.
            self.root.after_idle(self._focus_preview)

    def _focus_preview(self):
        try:
            focused = self.root.focus_get()
            if (not self.app._closing and focused is not None
                    and focused.winfo_toplevel() is self.root
                    and self.root.grab_current() is None):
                self.app.canvas.focus_set()
        except (tk.TclError, KeyError):
            # A menu/dialog may still own focus or have closed during the callback.
            pass

    def _press(self, event):
        if self._is_input(event.widget) or event.state & self._modifier_mask:
            return None
        if self.root.grab_current() is not None:
            return None
        if not self._down:
            self._down = True
            app = self.app
            if (app.video and not app.current_is_image and not app._closing
                    and (not app._busy or getattr(app, '_preview_task', False))):
                if app.playing:
                    app.pause()
                else:
                    app.play()
        # Do not also activate the focused button (especially a generation button).
        return 'break'

    def _release(self, event):
        handled = self._down
        self._down = False
        return 'break' if handled else None

    def _focus_out(self, event):
        self.root.after_idle(self._reset_if_outside)

    def _reset_if_outside(self):
        try:
            focused = self.root.focus_get()
            if focused is None or focused.winfo_toplevel() is not self.root:
                self._down = False
        except (tk.TclError, KeyError):
            self._down = False
