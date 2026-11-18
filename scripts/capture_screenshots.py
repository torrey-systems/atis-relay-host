#!/usr/bin/env python3
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(ROOT, ".screenshot-home", "config"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(ROOT, ".screenshot-home", "data"))
os.environ.setdefault("PI_ATC_ENV_FILE", os.path.join(os.environ["XDG_CONFIG_HOME"], "atis-relay", "env"))
os.environ.setdefault("PI_ATC_STATE_DIR", os.path.join(os.environ["XDG_DATA_HOME"], "atis-relay"))
os.environ["TZ"] = "UTC"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

import time

time.tzset()

from atis_relay_gui import AtisRelayWindow
from atis_relay_status import write_status


def pump():
    for _i in range(30):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        GLib.usleep(20000)


def capture(window, path):
    pump()
    gdk_win = window.get_window()
    width = gdk_win.get_width()
    height = gdk_win.get_height()
    pixbuf = Gdk.pixbuf_get_from_window(gdk_win, 0, 0, width, height)

    if pixbuf is None:
        raise SystemExit("could not capture %s" % path)

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    pixbuf.savev(path, "png", [], [])
    print("wrote %s" % path)


def main():
    docs = os.path.join(ROOT, "docs", "images")
    artifacts = "/opt/cursor/artifacts"
    os.makedirs(docs, exist_ok=True)
    os.makedirs(artifacts, exist_ok=True)
    win = AtisRelayWindow()
    win.set_default_size(560, 640)
    win.show_all()
    win.icao1.set_text("KMYF")
    win.freq1.set_text("126.8")
    win.key1.set_text("example-key-hidden")
    win.icao2.set_text("KSEE")
    win.freq2.set_text("135.1")
    win.show_step("step1")
    capture(win, os.path.join(docs, "step1-airports.png"))
    win.show_step("step2")
    win.step2_note.set_text("Click Test to hear five seconds from the radio.")
    win.level.set_fraction(0.62)
    win.level.set_text("Heard audio")
    capture(win, os.path.join(docs, "step2-audio.png"))
    if win.poll_id:
        GLib.source_remove(win.poll_id)
        win.poll_id = None

    write_status(state="listening", last_upload="2026-11-18T15:05:00Z")
    win.show_step("step3")
    capture(win, os.path.join(docs, "step3-start.png"))
    names = ("step1-airports.png", "step2-audio.png", "step3-start.png")

    for name in names:
        shutil.copy2(os.path.join(docs, name), os.path.join(artifacts, name))

    win.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
