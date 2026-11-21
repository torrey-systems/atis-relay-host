#!/usr/bin/env python3
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _install_gi_stubs():
    if "gi" in sys.modules and "atis_relay_gui" in sys.modules:
        return

    class DummyWindow:
        def __init__(self, *args, **kwargs):
            pass

    gi = types.ModuleType("gi")
    gi.require_version = lambda *args, **kwargs: None
    repository = types.ModuleType("gi.repository")
    gtk = types.ModuleType("gi.repository.Gtk")
    gtk.Window = DummyWindow
    gtk.main = MagicMock()
    gtk.main_quit = MagicMock()
    gtk.main_level = MagicMock(return_value=0)
    gdkpixbuf = types.ModuleType("gi.repository.GdkPixbuf")
    glib = types.ModuleType("gi.repository.GLib")
    repository.Gtk = gtk
    repository.GdkPixbuf = gdkpixbuf
    repository.GLib = glib
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    sys.modules["gi.repository.Gtk"] = gtk
    sys.modules["gi.repository.GdkPixbuf"] = gdkpixbuf
    sys.modules["gi.repository.GLib"] = glib


class GuiLauncherTest(unittest.TestCase):
    def test_desktop_and_atis_relay_entry_point_reach_gui_main(self):
        with open(os.path.join(ROOT, "packaging", "atis-relay")) as launcher_file:
            launcher = launcher_file.read()

        with open(os.path.join(ROOT, "packaging", "atis-relay.desktop")) as desktop_file:
            desktop = desktop_file.read()

        self.assertIn("from atis_relay_gui import main", launcher)
        self.assertIn("sys.exit(main())", launcher)
        self.assertIn('if argv and argv[0] in ("--worker", "worker"):', launcher)
        self.assertIn('if argv and argv[0] in ("set-key", "set_key", "status"):', launcher)
        self.assertIn("Exec=atis-relay", desktop)
        self.assertIn("Terminal=false", desktop)


class GuiMainTest(unittest.TestCase):
    def test_window_source_connects_destroy_to_main_quit(self):
        with open(os.path.join(ROOT, "atis_relay_gui.py")) as gui_file:
            gui = gui_file.read()

        self.assertIn('self.connect("destroy"', gui)
        self.assertIn("Gtk.main_quit", gui)

    def test_main_shows_window_and_runs_gtk_loop(self):
        _install_gi_stubs()
        import atis_relay_gui

        window = MagicMock()

        with patch.object(atis_relay_gui, "AtisRelayWindow", return_value=window):
            with patch.object(atis_relay_gui, "apply_runtime_paths"):
                with patch.object(atis_relay_gui, "Gtk") as gtk:
                    code = atis_relay_gui.main()

        self.assertEqual(code, 0)
        window.show_all.assert_called_once()
        gtk.main.assert_called_once()


class CaptureTestPauseTest(unittest.TestCase):
    def setUp(self):
        _install_gi_stubs()
        import atis_relay_gui

        self.gui = atis_relay_gui
        self.events = []

    def _record(self, name, result):
        def call(*args, **kwargs):
            self.events.append(name)

            if isinstance(result, Exception):
                raise result

            return result

        return call

    def _run(self, active, stopped=True, outcome=(True, 0.5, "")):
        with patch.object(self.gui, "is_worker_active", side_effect=self._record("active", active)):
            with patch.object(self.gui, "stop_worker", side_effect=self._record("stop", stopped)):
                with patch.object(self.gui, "start_worker", side_effect=self._record("start", True)):
                    with patch.object(self.gui, "test_capture", side_effect=self._record("test", outcome)):
                        return self.gui.run_capture_test({"id": "sdr", "kind": "sdr"}, "126.9")

    def test_running_recorder_is_stopped_for_the_test_then_started_again(self):
        self.assertEqual(self._run(True), (True, 0.5, ""))
        self.assertEqual(self.events, ["active", "stop", "test", "start"])

    def test_stopped_recorder_stays_stopped(self):
        self.assertEqual(self._run(False), (True, 0.5, ""))
        self.assertEqual(self.events, ["active", "test"])

    def test_recorder_that_could_not_be_stopped_is_not_started(self):
        self._run(True, stopped=False)
        self.assertEqual(self.events, ["active", "stop", "test"])

    def test_recorder_is_started_again_when_the_test_raises(self):
        with self.assertRaises(OSError):
            self._run(True, outcome=OSError("boom"))

        self.assertEqual(self.events, ["active", "stop", "test", "start"])


class NoticeLabelTest(unittest.TestCase):
    def setUp(self):
        _install_gi_stubs()
        import atis_relay_gui

        self.gui = atis_relay_gui
        self.window = MagicMock()
        self.glib = MagicMock()
        self.glib.markup_escape_text.side_effect = lambda text: text.replace("&", "&amp;").replace("<", "&lt;")

    def test_notice_is_shown_in_red_with_the_message_and_what_to_do(self):
        notice = {
            "message": "ATIS Relay asked this Pi to restart.",
            "fix": "Use the Raspberry Pi menu <now> & restart.",
        }

        with patch.object(self.gui, "GLib", self.glib):
            self.gui.AtisRelayWindow._show_notice(self.window, notice)

        markup = self.window.notice_label.set_markup.call_args.args[0]
        self.assertIn('foreground="#c01c28"', markup)
        self.assertIn("<b>ATIS Relay asked this Pi to restart.</b>", markup)
        self.assertIn("Use the Raspberry Pi menu &lt;now> &amp; restart.", markup)
        self.window.notice_label.show.assert_called_once_with()
        self.window.notice_label.hide.assert_not_called()

    def test_notice_is_hidden_when_there_is_nothing_to_report(self):
        with patch.object(self.gui, "GLib", self.glib):
            self.gui.AtisRelayWindow._show_notice(self.window, {"message": "", "fix": "", "boot_id": ""})

        self.window.notice_label.hide.assert_called_once_with()
        self.window.notice_label.show.assert_not_called()
        self.window.notice_label.set_markup.assert_not_called()

    def test_notice_label_is_above_the_steps_and_stays_hidden_until_needed(self):
        with open(os.path.join(ROOT, "atis_relay_gui.py")) as gui_file:
            gui = gui_file.read()

        self.assertIn("self.notice_label.set_no_show_all(True)", gui)
        self.assertLess(gui.index("outer.pack_start(self.notice_label"), gui.index("outer.pack_start(self.stack"))
        self.assertIn("self._show_notice(read_notice())", gui)


class RemoteCheckboxTest(unittest.TestCase):
    def setUp(self):
        _install_gi_stubs()
        import atis_relay_gui

        self.gui = atis_relay_gui
        self.window = MagicMock()
        self.button = MagicMock()

    def _toggle(self, wanted, saved):
        self.button.get_active.return_value = wanted

        with patch.object(self.gui, "set_remote_commands", return_value=saved) as set_remote_commands:
            self.gui.AtisRelayWindow._on_remote_toggled(self.window, self.button)

        set_remote_commands.assert_called_once_with(wanted)

    def test_turning_it_on_saves_and_says_what_is_allowed(self):
        self._toggle(True, True)
        self.window.remote_note.set_text.assert_called_once_with(self.gui.REMOTE_ON_NOTE)
        self.window._show_remote_commands.assert_not_called()

    def test_turning_it_off_saves_and_clears_the_note(self):
        self._toggle(False, True)
        self.window.remote_note.set_text.assert_called_once_with("")

    def test_failed_save_puts_the_checkbox_back(self):
        self._toggle(True, False)
        self.window._show_remote_commands.assert_called_once_with(False)
        self.window.remote_note.set_text.assert_called_once_with(self.gui.REMOTE_SAVE_FAILED)

    def test_loading_the_saved_value_does_not_save_it_again(self):
        self.gui.AtisRelayWindow._show_remote_commands(self.window, True)
        calls = [call[0] for call in self.window.remote.method_calls]
        self.assertEqual(calls, ["handler_block", "set_active", "handler_unblock"])
        self.window.remote.set_active.assert_called_once_with(True)
        self.window.remote_note.set_text.assert_called_once_with(self.gui.REMOTE_ON_NOTE)


class DriverPromptTest(unittest.TestCase):
    def setUp(self):
        _install_gi_stubs()
        import atis_relay_gui

        self.gui = atis_relay_gui
        self.window = MagicMock()

    def test_prompt_appears_for_a_v4_on_an_old_driver(self):
        with patch.object(self.gui, "v4_driver_missing", return_value=True):
            self.assertTrue(self.gui.AtisRelayWindow._show_driver_prompt(self.window))

        self.window.driver_btn.show.assert_called_once_with()
        note = self.window.step2_note.set_text.call_args.args[0]
        self.assertIn("RTL-SDR Blog V4", note)
        self.assertIn("Install driver", note)

    def test_prompt_stays_hidden_otherwise(self):
        with patch.object(self.gui, "v4_driver_missing", return_value=False):
            self.assertFalse(self.gui.AtisRelayWindow._show_driver_prompt(self.window))

        self.window.driver_btn.hide.assert_called_once_with()
        self.window.step2_note.set_text.assert_not_called()

    def test_test_button_asks_for_the_driver_instead_of_recording_static(self):
        self.window._selected_device.return_value = {"id": "sdr", "kind": "sdr"}
        self.window._show_driver_prompt.return_value = True

        with patch.object(self.gui, "threading") as threading:
            self.gui.AtisRelayWindow._on_test(self.window, None)

        threading.Thread.assert_not_called()

    def test_usb_audio_test_ignores_the_radio_driver(self):
        self.window._selected_device.return_value = {"id": "plughw:1,0", "kind": "alsa"}

        with patch.object(self.gui, "threading") as threading:
            self.gui.AtisRelayWindow._on_test(self.window, None)

        self.window._show_driver_prompt.assert_not_called()
        threading.Thread.assert_called_once()

    def test_result_of_the_install_is_reported(self):
        cases = (
            (True, False, self.gui.V4_DRIVER_INSTALLED),
            (True, True, self.gui.V4_DRIVER_FAILED),
            (False, True, self.gui.V4_DRIVER_FAILED),
            (False, False, self.gui.V4_DRIVER_FAILED),
        )

        for ok, missing, expected in cases:
            window = MagicMock()
            window._show_driver_prompt.return_value = missing
            self.assertFalse(self.gui.AtisRelayWindow._driver_done(window, ok))
            window.driver_btn.set_sensitive.assert_called_once_with(True)
            window.step2_note.set_text.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
