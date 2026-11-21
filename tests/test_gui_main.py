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


if __name__ == "__main__":
    unittest.main()
