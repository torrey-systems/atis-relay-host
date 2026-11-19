#!/usr/bin/env python3
import os
import shutil
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(ROOT, ".screenshot-home", "config"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(ROOT, ".screenshot-home", "data"))
os.environ.setdefault("PI_ATC_ENV_FILE", os.path.join(os.environ["XDG_CONFIG_HOME"], "atis-relay", "env"))
os.environ.setdefault("PI_ATC_STATE_DIR", os.path.join(os.environ["XDG_DATA_HOME"], "atis-relay"))
os.environ.setdefault("PI_ATC_PUBLIC_FILE", os.path.join(os.environ["XDG_DATA_HOME"], "atis-relay", "public.json"))
os.environ["TZ"] = "UTC"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

time.tzset()

from atis_relay_gui import AtisRelayWindow
from atis_relay_status import write_status


def pump():
    for _i in range(40):
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
    win.set_default_size(560, 680)
    win.show_all()
    win.icao1.set_text("KMYF")
    win.freq1.set_text("126.9")
    win.key1.set_text("example-key-hidden")
    win.icao2.set_text("KSEE")
    win.freq2.set_text("125.45")
    win.show_step("step1")
    capture(win, os.path.join(docs, "step1-airports.png"))
    win.key1.set_text("")
    win.key2.set_text("")
    win.key1.set_placeholder_text("Key saved. Leave blank to keep it.")
    win.key2.set_placeholder_text("Key saved. Leave blank to keep it.")
    capture(win, os.path.join(docs, "step1-saved.png"))
    win.key1.set_text("example-key-hidden")
    win.show_step("step2")
    win.step2_note.set_text("Choose the USB radio or USB audio adapter, then click Test.")
    win.level.set_fraction(0)
    win.level.set_text("Level")
    capture(win, os.path.join(docs, "step2-audio.png"))
    win.step2_note.set_text("That clip is what the app will send. If it was silent, check the antenna or try Refresh list.")
    win.level.set_fraction(0.62)
    win.level.set_text("Heard audio")
    capture(win, os.path.join(docs, "step2-test.png"))

    if win.poll_id:
        GLib.source_remove(win.poll_id)
        win.poll_id = None

    write_status(state="stopped")
    win.show_step("step3")
    win.status_label.set_text("Stopped")
    win.upload_label.set_text("Last upload: none yet")
    win.error_label.set_text("")
    win.fix_label.set_text("")
    win._apply_start_stop_buttons("stopped", False)
    capture(win, os.path.join(docs, "step3-start.png"))
    write_status(state="listening", last_upload="2026-11-18T15:05:00Z")
    win.show_step("step3")
    win._apply_start_stop_buttons("listening", True)
    capture(win, os.path.join(docs, "step3-listening.png"))
    write_status(
        state="error",
        last_upload="2026-11-18T15:05:00Z",
        error="The station key is missing.",
        fix="Open ATIS Relay and paste the station key ATIS Relay sent you.",
    )
    win.show_step("step3")
    win._apply_start_stop_buttons("error", False)
    capture(win, os.path.join(docs, "step3-attention.png"))
    write_status(state="stopped", last_upload="2026-11-18T15:05:00Z")
    win.show_step("step3")
    win._apply_start_stop_buttons("stopped", False)
    capture(win, os.path.join(docs, "step3-stopped.png"))
    names = (
        "logo.png",
        "step1-airports.png",
        "step1-saved.png",
        "step2-audio.png",
        "step2-test.png",
        "step3-start.png",
        "step3-listening.png",
        "step3-attention.png",
        "step3-stopped.png",
    )

    for name in names:
        src = os.path.join(docs, name)

        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(artifacts, name))

    win.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
