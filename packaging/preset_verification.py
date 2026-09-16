"""Exercise named presets through the real Tk controls, including a restart."""
import json
import os
from pathlib import Path
import time


def verify(directory):
    import tkinter as tk
    from unittest.mock import patch
    from PIL import ImageGrab
    import gui
    import sr_settings
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Never touch the real user's presets, including when run from source.
    os.environ['DLSS5_DATA_ROOT'] = str(directory / ('app-data-' + str(time.time_ns())))
    result = {'status': 'running'}
    root = None
    errors = []

    def pump(seconds=.12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.005)

    def start():
        nonlocal root
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
        app = gui.App(root)
        app.tabs.select(app.pages['参数'])
        pump()
        return app

    def close():
        nonlocal root
        if root is not None:
            for identifier in root.tk.call('after', 'info'):
                root.after_cancel(identifier)
            root.destroy()
            root = None

    def name(app, text):
        app.preset_name_entry.delete(0, 'end')
        app.preset_name_entry.insert(0, text)

    try:
        app = start()
        assert app._preset_items == []
        app.layer_vars[0]['preset'].set('Preset #2')
        app.layer_vars[0]['style'].set('电影')
        app.layer_vars[0]['intensity'].set(.45)
        app.layer_vars[0]['guidance'].set('仅深度')
        app.layer_vars[1]['preset'].set('Preset #3')
        app.layer_vars[1]['localStruct'].set(3.2)
        app.layer_vars[1]['motionSY'].set(.6)
        app.v_second_enabled.set(False)
        app.v_overall_weight.set(65)
        app.denoise_vars['input_denoise']['luma'].set(13)
        app.denoise_vars['input_denoise']['weight'].set(45)
        app.denoise_vars['output_denoise']['enabled'].set(True)
        app.denoise_vars['output_denoise']['chroma'].set(7)
        app.sr_vars['engine'].set('seedvr2')
        app.v_sr_engine.set(sr_settings.ENGINES['seedvr2'])
        app.sr_vars['scale'].set(4)
        app.sr_vars['batch'].set(9)
        app.sr_vars['pisa_pixel'].set(.75)
        app._show_sr_panels()
        pump()
        expected = app._capture_preset_parameters()
        name(app, '人像 柔和')
        app.preset_add_btn.invoke()
        identifier = app._preset_id
        assert app._preset_store.load()['presets'][0]['parameters'] == expected
        result['named_create_full_snapshot_disabled_values'] = True

        app.layer_vars[0]['intensity'].set(.7)
        app.v_second_enabled.set(True)
        name(app, '人像 精修')
        pump()
        app.preset_save_btn.invoke()
        assert app._preset_id == identifier and len(app._preset_items) == 1
        expected = app._capture_preset_parameters()
        assert app._preset_store.load()['presets'][0]['parameters'] == expected
        result['save_changes_and_rename'] = True

        app.layer_vars[0]['intensity'].set(.1)
        app.v_second_enabled.set(False)
        app.sr_vars['engine'].set('dlss')
        app.denoise_vars['output_denoise']['enabled'].set(False)
        name(app, '另一套')
        pump()
        app.preset_add_btn.invoke()
        other = app._preset_id
        assert len(app._preset_items) == 2
        old_key = app._settings_hash()
        class Live:
            closed = False
            def close(self):
                self.closed = True
        live = Live()
        app._live = live
        app._live_cache = ('stale',)
        app.image_dlss = object()
        app.preset_selector.current(0)
        app.preset_selector.event_generate('<<ComboboxSelected>>')
        pump()
        assert app._preset_id == identifier
        assert app._capture_preset_parameters() == expected
        assert app.v_sr_engine.get() == sr_settings.ENGINES['seedvr2']
        assert app.sr_seed.winfo_manager() == 'pack'
        assert live.closed and app._live_cache is None and app.image_dlss is None
        assert app._settings_hash() != old_key
        assert app.denoise_scales['input_denoise'][0].cget('state') == 'disabled'
        assert app.denoise_scales['output_denoise'][0].cget('state') == 'normal'
        result['select_apply_updates_engine_and_invalidates_result'] = True

        app.layer_vars[0]['intensity'].set(.2)
        pump()
        assert '已修改' in app.preset_note.cget('text')
        app.preset_apply_btn.invoke()
        pump()
        assert app._capture_preset_parameters() == expected
        app._set_busy(True)
        assert str(app.preset_save_btn.cget('state')) == 'disabled'
        assert str(app.preset_name_entry.cget('state')) == 'disabled'
        before = app._preset_store.path.read_bytes()
        app._save_parameter_preset(new=True)
        assert app._preset_store.path.read_bytes() == before
        app._set_busy(False)
        result['reload_and_busy_guard'] = True

        with patch('preset_ui.messagebox.showerror') as warning:
            name(app, '另一套')
            app.preset_save_btn.invoke()
            assert warning.call_count == 1
            assert app._preset_store.path.read_bytes() == before
        name(app, '人像 精修')
        # Text-entry spaces remain text, preserving the video shortcut exception.
        app.preset_name_entry.focus_force()
        root.update()
        app.preset_name_entry.icursor('end')
        app.preset_name_entry.event_generate('<KeyPress-space>')
        app.preset_name_entry.event_generate('<KeyRelease-space>')
        root.update()
        assert app.v_preset_name.get().endswith(' ')
        name(app, '人像 精修')
        result['duplicate_protection_and_text_spaces'] = True

        for width, height in ((1240, 820), (680, 520)):
            root.geometry(f'{width}x{height}')
            pump(.3)
            app.pages['参数'].canvas.yview_moveto(0)
            pump()
            for widget in (app.preset_selector, app.preset_name_entry, app.preset_add_btn,
                           app.preset_save_btn, app.preset_apply_btn, app.preset_delete_btn):
                parent = widget.master
                assert widget.winfo_x() >= 0 and widget.winfo_width() > 20
                assert widget.winfo_x() + widget.winfo_width() <= parent.winfo_width(), str(widget)
            # Capture only our test window, even if another app is in front.
            ImageGrab.grab(window=root.winfo_id()).save(directory / f'presets-{width}.png')
        result['responsive_layout'] = True
        close()
        app = start()
        assert app._preset_id == identifier
        assert app.v_preset_name.get() == '人像 精修'
        assert len(app._preset_items) == 2
        assert app._capture_preset_parameters() == expected
        result['restart_restores_saved_preset'] = True
        with patch('preset_ui.messagebox.askyesno', return_value=False):
            app.preset_delete_btn.invoke()
        assert len(app._preset_items) == 2
        with patch('preset_ui.messagebox.askyesno', return_value=True):
            app.preset_delete_btn.invoke()
        assert [item['id'] for item in app._preset_items] == [other]
        assert app._capture_preset_parameters() == expected
        assert app._preset_store.load()['active_id'] is None
        result['delete_confirmation_preserves_current_parameters'] = True
        assert not errors, errors
        result['status'] = 'passed'
    except Exception:
        import traceback
        result.update(status='failed', error=traceback.format_exc(), callback_errors=errors)
        raise
    finally:
        close()
        (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
