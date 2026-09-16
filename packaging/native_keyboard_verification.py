"""Exercise Tk's Windows key translation, including native lock-key state."""
import ctypes
from ctypes import wintypes


def verify(app, pump):
    root = app.root
    assert root.tk.call('tk', 'windowingsystem') == 'win32'
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    state_type = wintypes.BYTE * 256
    user32.GetKeyboardState.argtypes = [ctypes.POINTER(wintypes.BYTE)]
    user32.SetKeyboardState.argtypes = [ctypes.POINTER(wintypes.BYTE)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    saved = state_type()
    assert user32.GetKeyboardState(saved)
    events = []
    tag = 'NativeSpaceProbe_' + str(root.winfo_id())
    root.bind_class(tag, '<KeyPress-space>', lambda event: events.append(event.state))
    original_tags = app.canvas.bindtags()
    app.canvas.bindtags((tag, *original_tags))
    result = {}

    def key(down, repeat=False):
        focused = root.focus_get()
        assert focused is app.canvas
        # Target only this test widget. No global keyboard injection or toggle changes.
        flags = 0x00390001 | (0x40000000 if repeat else 0) | (0 if down else 0xC0000000)
        assert user32.PostMessageW(focused.winfo_id(), 0x100 if down else 0x101, 0x20, flags)
        pump(.025)

    try:
        app.canvas.focus_force()
        pump()
        cases = [('current_keyboard', None)] + [
            ('locks_' + str(bits), bits) for bits in range(8)]
        for label, locks in cases:
            state = state_type(*saved)
            # Modifier tests are separate; these cases exercise unmodified Space.
            for code in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
                state[code] = 0
            if locks is not None:
                for bit, code in enumerate((0x90, 0x14, 0x91)):
                    state[code] = (locks >> bit) & 1
            assert user32.SetKeyboardState(state)
            app.pause()
            count = len(events)
            key(True)
            assert len(events) == count + 1, 'Windows 空格未到达 Tk'
            result[label] = events[-1]
            assert app.playing, f'{label}: Windows 空格未播放，state={events[-1]}'
            if locks is None:
                for _ in range(3):
                    key(True, repeat=True)
                    assert app.playing, 'Windows 长按空格重复切换了播放'
            key(False)
            assert app.playing
            key(True)
            assert not app.playing, f'{label}: Windows 空格未暂停'
            key(False)
        return result
    finally:
        user32.SetKeyboardState(saved)
        app.canvas.bindtags(original_tags)
        root.unbind_class(tag, '<KeyPress-space>')
        app.pause()
