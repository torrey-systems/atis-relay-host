#!/usr/bin/env python3
import os
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from atis_relay_devices import list_capture_devices, test_capture, v4_driver_missing
from atis_relay_service import (
    disable_service,
    enable_service,
    install_settings,
    install_v4_driver,
    is_worker_active,
    service_enabled_state,
    set_remote_commands,
    start_worker,
    stop_worker,
)
from atis_relay_status import (
    format_local_time,
    read_notice,
    read_status,
    start_stop_enabled,
    status_label,
    suggestion_for_error,
)
from pi_atc_config import (
    apply_runtime_paths,
    env_path,
    normalize_airport_code,
    parse_stations,
    read_env_file,
    read_public_config,
    remote_commands_enabled,
    station_token_for,
    valid_airport_code,
)

KEY_HINT = "Enter the airport code (for example KMYF or 1G0) and ATIS frequency. Paste the station key ATIS Relay sent you. Leave airport 2 blank if you only have one."
KEY_MISSING_1 = "Paste the station key ATIS Relay sent you."
KEY_MISSING_2 = "Paste the station key ATIS Relay sent you for airport 2."
REMOTE_LABEL = "Let ATIS Relay restart this Pi and the recorder remotely"
REMOTE_ON_NOTE = "ATIS Relay can restart this Pi, restart the recorder, and reset the USB radio when needed."
REMOTE_SAVE_FAILED = "Could not save that setting. Try again from this window."
NOTICE_MARKUP = '<span foreground="#c01c28"><b>%s</b>\n%s</span>'
V4_DRIVER_HINT = "Click Install driver. It needs internet and can take several minutes."
V4_DRIVER_INSTALLING = "Installing the driver. This needs internet and can take several minutes."
V4_DRIVER_INSTALLED = "Driver installed. Click Test."
V4_DRIVER_FAILED = "Could not install the driver. Check that the Pi is online, then try again."
ICON_NAME = "atis-relay"
HEADER_LOGO_PX = 128


def icon_file(size="256x256"):
    filename = "atis-relay.svg" if size == "scalable" else "atis-relay.png"
    rel = os.path.join(size, "apps", filename)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join("/usr/share/icons/hicolor", rel),
        os.path.join(here, "packaging", "icons", "hicolor", rel),
        os.path.join(os.path.dirname(here), "packaging", "icons", "hicolor", rel),
    )

    for path in candidates:
        if os.path.isfile(path):
            return path

    return ""


def apply_app_icon(window=None):
    Gtk.Window.set_default_icon_name(ICON_NAME)
    path = icon_file("256x256") or icon_file("512x512") or icon_file("128x128")

    if not path:
        return

    try:
        Gtk.Window.set_default_icon_from_file(path)

        if window is not None:
            window.set_icon_from_file(path)

    except Exception:
        return


def header_logo_image():
    path = icon_file("256x256") or icon_file("512x512") or icon_file("128x128")

    if not path:
        return None

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)

        if pixbuf.get_width() != HEADER_LOGO_PX or pixbuf.get_height() != HEADER_LOGO_PX:
            pixbuf = pixbuf.scale_simple(
                HEADER_LOGO_PX,
                HEADER_LOGO_PX,
                GdkPixbuf.InterpType.HYPER,
            )

        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_pixel_size(HEADER_LOGO_PX)
        return image

    except Exception:
        try:
            return Gtk.Image.new_from_file(path)

        except Exception:
            return None


def run_capture_test(item, freq):
    paused = is_worker_active() and stop_worker()

    try:
        return test_capture(item["id"], kind=item["kind"], freq=freq)

    finally:
        if paused:
            start_worker()


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
        apply_app_icon(self)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(outer)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        logo = header_logo_image()

        if logo is not None:
            header.pack_start(logo, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label="ATIS Relay")
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        subtitle = Gtk.Label(label="Record ATIS audio and send it to ATIS Relay.")
        subtitle.set_xalign(0)
        subtitle.set_line_wrap(True)
        text.pack_start(title, False, False, 0)
        text.pack_start(subtitle, False, False, 0)
        header.pack_start(text, True, True, 0)
        outer.pack_start(header, False, False, 0)
        self.notice_label = Gtk.Label()
        self.notice_label.set_xalign(0)
        self.notice_label.set_line_wrap(True)
        self.notice_label.set_no_show_all(True)
        outer.pack_start(self.notice_label, False, False, 0)
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
        hint = Gtk.Label(label=KEY_HINT)
        hint.set_line_wrap(True)
        hint.set_xalign(0)
        box.pack_start(hint, False, False, 0)
        self.icao1 = Gtk.Entry()
        self.icao1.set_placeholder_text("KMYF")
        self.icao1.set_max_length(4)
        self.freq1 = Gtk.Entry()
        self.freq1.set_placeholder_text("126.9")
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
        self.freq2.set_placeholder_text("125.45")
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
        self.driver_btn = Gtk.Button.new_with_label("Install driver")
        self.driver_btn.connect("clicked", self._on_install_driver)
        self.driver_btn.set_no_show_all(True)
        row.pack_start(refresh, False, False, 0)
        row.pack_start(test_btn, False, False, 0)
        row.pack_start(self.driver_btn, False, False, 0)
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
        self.remote = Gtk.CheckButton.new_with_label(REMOTE_LABEL)
        self.remote_handler = self.remote.connect("toggled", self._on_remote_toggled)
        box.pack_start(self.remote, False, False, 0)
        self.remote_note = Gtk.Label()
        self.remote_note.set_xalign(0)
        self.remote_note.set_line_wrap(True)
        box.pack_start(self.remote_note, False, False, 0)
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
        self.start_btn = Gtk.Button.new_with_label("Start")
        self.start_btn.connect("clicked", self._on_start)
        self.stop_btn = Gtk.Button.new_with_label("Stop")
        self.stop_btn.connect("clicked", self._on_stop)
        row.pack_start(self.start_btn, False, False, 0)
        row.pack_start(self.stop_btn, False, False, 0)
        box.pack_start(row, False, False, 0)
        self._apply_start_stop_buttons("stopped", False)
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

    def _saved_config(self):
        public = read_public_config()
        stations = public.get("stations") or []
        parsed = []

        for item in stations:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                parsed.append((normalize_airport_code(item[0]), str(item[1]).strip()))

        if parsed:
            return {
                "stations": parsed,
                "tokens": public.get("tokens") or {},
                "audio_device": public.get("audio_device") or "",
                "capture_kind": public.get("capture_kind") or "sdr",
                "remote_commands": public.get("remote_commands") is True,
            }

        data = read_env_file(env_path())
        return {
            "stations": parse_stations(data.get("STATIONS", "")),
            "tokens": {
                icao: bool(station_token_for(icao, data))
                for icao, _freq in parse_stations(data.get("STATIONS", ""))
            },
            "audio_device": (data.get("AUDIO_DEVICE") or "").strip(),
            "capture_kind": (data.get("CAPTURE_KIND") or "sdr").strip() or "sdr",
            "remote_commands": remote_commands_enabled(data),
        }

    def _token_saved(self, icao):
        if not icao:
            return False

        saved = self._saved_config()
        flags = saved.get("tokens") or {}
        code = normalize_airport_code(icao)
        return bool(flags.get(code) or flags.get(icao))

    def _load_saved(self):
        apply_runtime_paths()
        saved = self._saved_config()
        stations = saved.get("stations") or []

        if stations:
            self.icao1.set_text(stations[0][0])
            self.freq1.set_text(stations[0][1])

        if len(stations) > 1:
            self.icao2.set_text(stations[1][0])
            self.freq2.set_text(stations[1][1])

        if self._token_saved(self.icao1.get_text()):
            self.key1.set_placeholder_text("Key saved. Leave blank to keep it.")

        if self._token_saved(self.icao2.get_text()):
            self.key2.set_placeholder_text("Key saved. Leave blank to keep it.")

        enabled = service_enabled_state()
        self.autostart.set_active(True if enabled is None else enabled)
        self._show_remote_commands(bool(saved.get("remote_commands")))
        self._saved_device = saved.get("audio_device") or ""
        self._saved_kind = saved.get("capture_kind") or "sdr"

    def _show_remote_commands(self, enabled):
        self.remote.handler_block(self.remote_handler)
        self.remote.set_active(enabled)
        self.remote.handler_unblock(self.remote_handler)
        self.remote_note.set_text(REMOTE_ON_NOTE if enabled else "")

    def _on_remote_toggled(self, button):
        wanted = button.get_active()

        if set_remote_commands(wanted):
            self.remote_note.set_text(REMOTE_ON_NOTE if wanted else "")
            return

        self._show_remote_commands(not wanted)
        self.remote_note.set_text(REMOTE_SAVE_FAILED)

    def _stations_from_form(self):
        icao1 = normalize_airport_code(self.icao1.get_text())
        freq1 = self.freq1.get_text().strip()
        icao2 = normalize_airport_code(self.icao2.get_text())
        freq2 = self.freq2.get_text().strip()
        stations = []

        if not valid_airport_code(icao1):
            return None, "Enter a 3 or 4 character airport code for airport 1."

        if not _valid_freq(freq1):
            return None, "Enter a frequency between 118.0 and 136.99 for airport 1."

        stations.append((icao1, freq1))

        if icao2 or freq2:
            if not valid_airport_code(icao2):
                return None, "Enter a 3 or 4 character airport code for airport 2, or leave it blank."

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

    def _save_form(self, stations, tokens, audio_device="", capture_kind="sdr"):
        return install_settings(
            stations,
            tokens,
            audio_device=audio_device,
            capture_kind=capture_kind,
        )

    def _on_step1_next(self, _button):
        stations, error = self._stations_from_form()
        self.step1_error.set_text(error)

        if error:
            return

        tokens = self._tokens_from_form(stations)

        if not tokens.get(stations[0][0]) and not self._token_saved(stations[0][0]):
            self.step1_error.set_text(KEY_MISSING_1)
            return

        if len(stations) > 1 and not tokens.get(stations[1][0]) and not self._token_saved(stations[1][0]):
            self.step1_error.set_text(KEY_MISSING_2)
            return

        if not self._save_form(stations, tokens):
            self.step1_error.set_text("Could not save settings. Try again from this window.")
            return

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

        self._show_driver_prompt()

    def _show_driver_prompt(self):
        if not v4_driver_missing():
            self.driver_btn.hide()
            return False

        message, _fix = suggestion_for_error("v4_driver")
        self.step2_note.set_text("%s %s" % (message, V4_DRIVER_HINT))
        self.driver_btn.show()
        return True

    def _on_install_driver(self, button):
        button.set_sensitive(False)
        self.step2_note.set_text(V4_DRIVER_INSTALLING)

        def work():
            ok = install_v4_driver()
            GLib.idle_add(self._driver_done, ok)

        threading.Thread(target=work, daemon=True).start()

    def _driver_done(self, ok):
        self.driver_btn.set_sensitive(True)
        missing = self._show_driver_prompt()
        self.step2_note.set_text(V4_DRIVER_FAILED if missing or not ok else V4_DRIVER_INSTALLED)
        return False

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
            return False

        tokens = self._tokens_from_form(stations)
        audio_device = "" if item["kind"] == "sdr" else item["id"]

        if not self._save_form(
            stations,
            tokens,
            audio_device=audio_device,
            capture_kind=item["kind"],
        ):
            self.step2_note.set_text("Could not save settings. Try again from this window.")
            return False

        self.show_step("step3")
        return True

    def _on_test(self, _button):
        item = self._selected_device()

        if item["kind"] == "sdr" and self._show_driver_prompt():
            return

        self.step2_note.set_text("Testing. You should hear a five second clip.")
        self.level.set_fraction(0.1)
        self.level.set_text("Testing")
        freq = self.freq1.get_text().strip() or "126.9"

        def work():
            ok, level, error = run_capture_test(item, freq)
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
        if not self._on_step2_next(_button):
            return

        if self.autostart.get_active():
            enable_service()

        else:
            disable_service()

        if not start_worker():
            self.error_label.set_text("Could not start the recorder.")
            self.fix_label.set_text("Try Start again from this window.")
            return

        self._poll_status()

    def _on_stop(self, _button):
        if not stop_worker():
            self.error_label.set_text("Could not stop the recorder.")
            self.fix_label.set_text("Try Stop again from this window.")
            return

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
        self._show_notice(read_notice())
        worker_active = is_worker_active()

        if worker_active and state == "stopped":
            state = "listening"
            self.status_label.set_text("Listening")

        self._apply_start_stop_buttons(state, worker_active)
        return True

    def _show_notice(self, notice):
        message = notice.get("message") or ""

        if not message:
            self.notice_label.hide()
            return

        self.notice_label.set_markup(NOTICE_MARKUP % (
            GLib.markup_escape_text(message),
            GLib.markup_escape_text(notice.get("fix") or ""),
        ))
        self.notice_label.show()

    def _apply_start_stop_buttons(self, state, worker_active=False):
        start_on, stop_on = start_stop_enabled(state, worker_active)
        self.start_btn.set_sensitive(start_on)
        self.stop_btn.set_sensitive(stop_on)


def main():
    apply_runtime_paths()
    window = AtisRelayWindow()
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
