#!/usr/bin/env python3
import os
import re
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from atis_relay_devices import list_capture_devices, test_capture
from atis_relay_service import (
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
    is_worker_active,
    start_worker,
    stop_worker,
)
from atis_relay_status import format_local_time, read_status, status_label, write_status
from pi_atc_config import (
    apply_user_paths,
    env_path,
    load_config,
    parse_stations,
    read_env_file,
    save_desktop_settings,
    station_token_for,
    user_config_dir,
)

ICAO_RE = re.compile(r"^[A-Z0-9]{3,4}$")


def _valid_freq(text):
    try:
        value = float(text)

    except (TypeError, ValueError):
        return False

    return 118.0 <= value <= 136.999


class AtisRelayWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="ATIS Relay")
        self.set_default_size(560, 640)
        self.set_border_width(18)
        self.connect("destroy", lambda *_a: Gtk.main_quit() if Gtk.main_level() else None)
        self.devices = []
        self.poll_id = None
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(outer)
        title = Gtk.Label(label="ATIS Relay")
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        subtitle = Gtk.Label(label="Record ATIS audio and send it to ATIS Relay.")
        subtitle.set_xalign(0)
        subtitle.set_line_wrap(True)
        outer.pack_start(title, False, False, 0)
        outer.pack_start(subtitle, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        outer.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._build_step1(), "step1")
        self.stack.add_named(self._build_step2(), "step2")
        self.stack.add_named(self._build_step3(), "step3")
        self._load_saved()
        self.show_step("step1")
        self.poll_id = GLib.timeout_add_seconds(1, self._poll_status)

    def _build_step1(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        heading = Gtk.Label(label="Step 1 of 3: Airports")
        heading.set_xalign(0)
        heading.get_style_context().add_class("heading")
        box.pack_start(heading, False, False, 0)
        hint = Gtk.Label(label="Enter the airport code and ATIS frequency. Paste the station key from atisrelay.com. Leave airport 2 blank if you only have one.")
        hint.set_line_wrap(True)
        hint.set_xalign(0)
        box.pack_start(hint, False, False, 0)
        self.icao1 = Gtk.Entry()
        self.icao1.set_placeholder_text("KMYF")
        self.icao1.set_max_length(4)
        self.freq1 = Gtk.Entry()
        self.freq1.set_placeholder_text("126.8")
        self.key1 = Gtk.Entry()
        self.key1.set_visibility(False)
        self.key1.set_placeholder_text("Station key")
        box.pack_start(self._labeled("Airport 1 code", self.icao1), False, False, 0)
        box.pack_start(self._labeled("Airport 1 frequency (MHz)", self.freq1), False, False, 0)
        box.pack_start(self._labeled("Airport 1 station key", self.key1), False, False, 0)
        self.icao2 = Gtk.Entry()
        self.icao2.set_placeholder_text("KSEE")
        self.icao2.set_max_length(4)
        self.freq2 = Gtk.Entry()
        self.freq2.set_placeholder_text("135.1")
        self.key2 = Gtk.Entry()
        self.key2.set_visibility(False)
        self.key2.set_placeholder_text("Optional")
        box.pack_start(self._labeled("Airport 2 code (optional)", self.icao2), False, False, 0)
        box.pack_start(self._labeled("Airport 2 frequency (MHz)", self.freq2), False, False, 0)
        box.pack_start(self._labeled("Airport 2 station key", self.key2), False, False, 0)
        self.step1_error = Gtk.Label()
        self.step1_error.set_xalign(0)
        self.step1_error.set_line_wrap(True)
        box.pack_start(self.step1_error, False, False, 0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        next_btn = Gtk.Button.new_with_label("Next")
        next_btn.connect("clicked", self._on_step1_next)
        nav.pack_end(next_btn, False, False, 0)
        box.pack_end(nav, False, False, 0)
        return box

    def _build_step2(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        heading = Gtk.Label(label="Step 2 of 3: Audio")
        heading.set_xalign(0)
        heading.get_style_context().add_class("heading")
        box.pack_start(heading, False, False, 0)
        hint = Gtk.Label(label="Choose the USB radio or USB audio adapter. Click Test to record five seconds and play it back.")
        hint.set_line_wrap(True)
        hint.set_xalign(0)
        box.pack_start(hint, False, False, 0)
        self.device_combo = Gtk.ComboBoxText()
        box.pack_start(self._labeled("Audio input", self.device_combo), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button.new_with_label("Refresh list")
        refresh.connect("clicked", lambda *_a: self._reload_devices())
        test_btn = Gtk.Button.new_with_label("Test")
        test_btn.connect("clicked", self._on_test)
        row.pack_start(refresh, False, False, 0)
        row.pack_start(test_btn, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.level = Gtk.ProgressBar()
        self.level.set_show_text(True)
        self.level.set_text("Level")
        box.pack_start(self.level, False, False, 0)
        self.step2_note = Gtk.Label()
        self.step2_note.set_xalign(0)
        self.step2_note.set_line_wrap(True)
        box.pack_start(self.step2_note, False, False, 0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back = Gtk.Button.new_with_label("Back")
        back.connect("clicked", lambda *_a: self.show_step("step1"))
        nxt = Gtk.Button.new_with_label("Next")
        nxt.connect("clicked", self._on_step2_next)
        nav.pack_start(back, False, False, 0)
        nav.pack_end(nxt, False, False, 0)
        box.pack_end(nav, False, False, 0)
        return box

    def _build_step3(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        heading = Gtk.Label(label="Step 3 of 3: Start")
        heading.set_xalign(0)
        heading.get_style_context().add_class("heading")
        box.pack_start(heading, False, False, 0)
        self.autostart = Gtk.CheckButton.new_with_label("Start automatically when the Pi turns on")
        self.autostart.set_active(True)
        box.pack_start(self.autostart, False, False, 0)
        self.status_label = Gtk.Label(label="Stopped")
        self.status_label.set_xalign(0)
        self.status_label.get_style_context().add_class("heading")
        box.pack_start(self.status_label, False, False, 0)
        self.upload_label = Gtk.Label(label="Last upload: none yet")
        self.upload_label.set_xalign(0)
        box.pack_start(self.upload_label, False, False, 0)
        self.error_label = Gtk.Label()
        self.error_label.set_xalign(0)
        self.error_label.set_line_wrap(True)
        box.pack_start(self.error_label, False, False, 0)
        self.fix_label = Gtk.Label()
        self.fix_label.set_xalign(0)
        self.fix_label.set_line_wrap(True)
        box.pack_start(self.fix_label, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start_btn = Gtk.Button.new_with_label("Start")
        start_btn.connect("clicked", self._on_start)
        stop_btn = Gtk.Button.new_with_label("Stop")
        stop_btn.connect("clicked", self._on_stop)
        row.pack_start(start_btn, False, False, 0)
        row.pack_start(stop_btn, False, False, 0)
        box.pack_start(row, False, False, 0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back = Gtk.Button.new_with_label("Back")
        back.connect("clicked", lambda *_a: self.show_step("step2"))
        nav.pack_start(back, False, False, 0)
        box.pack_end(nav, False, False, 0)
        return box

    def _labeled(self, text, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        box.pack_start(widget, False, False, 0)
        return box

    def show_step(self, name):
        if name == "step2":
            self._reload_devices()

        if name == "step3":
            self._poll_status()

        self.stack.set_visible_child_name(name)

    def _load_saved(self):
        apply_user_paths()
        load_config()
        data = read_env_file(env_path())
        stations = parse_stations(data.get("STATIONS", ""))

        if stations:
            self.icao1.set_text(stations[0][0])
            self.freq1.set_text(stations[0][1])

        if len(stations) > 1:
            self.icao2.set_text(stations[1][0])
            self.freq2.set_text(stations[1][1])

        if station_token_for(self.icao1.get_text().strip().upper(), data):
            self.key1.set_placeholder_text("Key saved. Leave blank to keep it.")

        if station_token_for(self.icao2.get_text().strip().upper(), data):
            self.key2.set_placeholder_text("Key saved. Leave blank to keep it.")

        self.autostart.set_active(is_autostart_enabled() or True)
        saved_device = (data.get("AUDIO_DEVICE") or "").strip()
        saved_kind = (data.get("CAPTURE_KIND") or "sdr").strip()
        self._saved_device = saved_device
        self._saved_kind = saved_kind

    def _stations_from_form(self):
        icao1 = self.icao1.get_text().strip().upper()
        freq1 = self.freq1.get_text().strip()
        icao2 = self.icao2.get_text().strip().upper()
        freq2 = self.freq2.get_text().strip()
        stations = []

        if not ICAO_RE.match(icao1):
            return None, "Enter a 3 or 4 letter airport code for airport 1."

        if not _valid_freq(freq1):
            return None, "Enter a frequency between 118.0 and 136.99 for airport 1."

        stations.append((icao1, freq1))

        if icao2 or freq2:
            if not ICAO_RE.match(icao2):
                return None, "Enter a 3 or 4 letter airport code for airport 2, or leave it blank."

            if not _valid_freq(freq2):
                return None, "Enter a frequency between 118.0 and 136.99 for airport 2."

            stations.append((icao2, freq2))

        return stations, ""

    def _tokens_from_form(self, stations):
        tokens = {}
        key1 = self.key1.get_text().strip()
        key2 = self.key2.get_text().strip()

        if key1:
            tokens[stations[0][0]] = key1

        if len(stations) > 1 and key2:
            tokens[stations[1][0]] = key2

        return tokens

    def _on_step1_next(self, _button):
        stations, error = self._stations_from_form()
        self.step1_error.set_text(error)

        if error:
            return

        tokens = self._tokens_from_form(stations)
        existing = read_env_file(env_path())

        if not tokens.get(stations[0][0]) and not station_token_for(stations[0][0], existing):
            self.step1_error.set_text("Paste the station key for airport 1 from atisrelay.com.")
            return

        if len(stations) > 1 and not tokens.get(stations[1][0]) and not station_token_for(stations[1][0], existing):
            self.step1_error.set_text("Paste the station key for airport 2 from atisrelay.com.")
            return

        os.makedirs(user_config_dir(), exist_ok=True)
        save_desktop_settings(env_path(), stations, tokens)
        self.show_step("step2")

    def _reload_devices(self):
        current = self.device_combo.get_active_id()
        self.device_combo.remove_all()
        self.devices = list_capture_devices()

        for item in self.devices:
            self.device_combo.append(item["id"], item["label"])

        pick = current or getattr(self, "_saved_device", "") or "sdr"
        ids = [item["id"] for item in self.devices]

        if pick in ids:
            self.device_combo.set_active_id(pick)

        elif ids:
            self.device_combo.set_active(0)

    def _selected_device(self):
        chosen = self.device_combo.get_active_id()

        for item in self.devices:
            if item["id"] == chosen:
                return item

        return {"id": "sdr", "kind": "sdr", "label": "USB radio (RTL-SDR)"}

    def _on_step2_next(self, _button):
        item = self._selected_device()
        stations, error = self._stations_from_form()

        if error:
            self.show_step("step1")
            self.step1_error.set_text(error)
            return

        tokens = self._tokens_from_form(stations)
        audio_device = "" if item["kind"] == "sdr" else item["id"]
        save_desktop_settings(
            env_path(),
            stations,
            tokens,
            audio_device=audio_device,
            capture_kind=item["kind"],
        )
        self.show_step("step3")

    def _on_test(self, _button):
        item = self._selected_device()
        self.step2_note.set_text("Testing. You should hear a five second clip.")
        self.level.set_fraction(0.1)
        self.level.set_text("Testing")
        freq = self.freq1.get_text().strip() or "126.8"

        def work():
            ok, level, error = test_capture(item["id"], kind=item["kind"], freq=freq)
            GLib.idle_add(self._test_done, ok, level, error)

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, ok, level, error):
        self.level.set_fraction(level)

        if ok:
            self.level.set_text("Heard audio")
            self.step2_note.set_text("That clip is what the app will send. If it was silent, check the antenna or try Refresh list.")

        else:
            self.level.set_text("No audio")
            self.step2_note.set_text(error or "The test did not hear anything.")

        return False

    def _on_start(self, _button):
        self._on_step2_next(_button)

        if self.autostart.get_active():
            enable_autostart()

        else:
            disable_autostart()

        write_status(state="waiting")
        start_worker()
        self._poll_status()

    def _on_stop(self, _button):
        stop_worker()
        write_status(state="stopped")
        self._poll_status()

    def _poll_status(self):
        data = read_status()
        state = data.get("state") or "stopped"
        self.status_label.set_text(status_label(state))
        last = data.get("last_upload") or ""
        pretty = format_local_time(last)

        if pretty:
            self.upload_label.set_text("Last upload: %s" % pretty)

        else:
            self.upload_label.set_text("Last upload: none yet")

        self.error_label.set_text(data.get("error") or "")
        self.fix_label.set_text(data.get("fix") or "")

        if is_worker_active() and state == "stopped":
            self.status_label.set_text("Listening")

        return True


def main():
    apply_user_paths()
    os.makedirs(user_config_dir(), exist_ok=True)
    AtisRelayWindow()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
