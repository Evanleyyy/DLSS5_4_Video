import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source/dlss5standaloneV2'))
from playback_shortcuts import PlaybackShortcuts


class RealtimeShortcutsTests(unittest.TestCase):
    def test_preview_allows_playback_but_export_keeps_shortcuts_locked(self):
        for preview, expected in ((True, True), (False, False)):
            with self.subTest(preview=preview):
                app = SimpleNamespace(video='video.mp4', current_is_image=False,
                    _busy=True, _preview_task=preview, _closing=False, playing=False)
                app.play = lambda: setattr(app, 'playing', True)
                app.pause = lambda: setattr(app, 'playing', False)
                shortcut = PlaybackShortcuts.__new__(PlaybackShortcuts)
                shortcut.app, shortcut.root = app, Mock()
                shortcut.root.grab_current.return_value = None
                shortcut._down, shortcut._modifier_mask = False, 0x20005
                event = SimpleNamespace(widget=None, state=0)
                self.assertEqual(shortcut._press(event), 'break')
                self.assertEqual(app.playing, expected)
                shortcut._release(event)
                shortcut._press(event)
                self.assertFalse(app.playing)


if __name__ == '__main__':
    unittest.main()
