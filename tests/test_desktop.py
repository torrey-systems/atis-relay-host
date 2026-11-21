#!/usr/bin/env python3
import io
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import atis_relay_ctl as ctl
import atis_relay_devices as devices
import atis_relay_service as service
import atis_relay_status as status
import atis_relay_worker as worker
import pi_atc_config as config


TOKEN = "desktop-secret-station-token"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class DesktopConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_home = os.path.join(self.tmpdir.name, "config")
        self.data_home = os.path.join(self.tmpdir.name, "data")
        self.env_file = os.path.join(self.tmpdir.name, "etc", "atis-relay", "env")
        self.state_dir = os.path.join(self.tmpdir.name, "var", "atis-relay")
        os.makedirs(os.path.dirname(self.env_file), exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)
        self.env_patch = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": self.config_home,
            "XDG_DATA_HOME": self.data_home,
            "PI_ATC_ENV_FILE": self.env_file,
            "PI_ATC_STATE_DIR": self.state_dir,
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def test_save_settings_mode_640_and_public_config_omits_token(self):
        config.save_desktop_settings(
            self.env_file,
            [("KMYF", "126.9")],
            {"KMYF": TOKEN},
            audio_device="plughw:1,0",
            capture_kind="alsa",
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.env_file).st_mode), 0o640)
        with open(self.env_file) as env_file:
            data = env_file.read()
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, data)
        self.assertIn("CAPTURE_KIND=alsa", data)
        public = config.read_public_config()
        self.assertEqual(public["stations"], [["KMYF", "126.9"]])
        self.assertEqual(public["tokens"]["KMYF"], True)
        with open(config.public_config_path()) as public_file:
            self.assertNotIn(TOKEN, public_file.read())
        self.assertEqual(stat.S_IMODE(os.stat(config.public_config_path()).st_mode), 0o644)
        self.assertEqual(config.env_path(), self.env_file)

    def test_save_keeps_existing_key_when_blank(self):
        config.save_desktop_settings(self.env_file, [("KMYF", "126.9")], {"KMYF": TOKEN})
        config.save_desktop_settings(self.env_file, [("KMYF", "126.9")], {"KMYF": ""})
        data = config.read_env_file(self.env_file)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)

    def test_load_config_sees_settings_saved_after_first_load(self):
        config.migrate_to_system_env(dest_path=self.env_file, extra_sources=[])
        config.load_config()
        self.assertEqual(config.station_token_for("KMYF"), "")
        config.save_desktop_settings(self.env_file, [("KMYF", "126.9")], {"KMYF": TOKEN})
        config.load_config()
        self.assertEqual(config.station_token_for("KMYF"), TOKEN)
        self.assertEqual(config.upload_mode(), "http")
        config.save_desktop_settings(self.env_file, [("KMYF", "125.45")], {})
        config.load_config()
        self.assertEqual(config.stations_from_env(), [("KMYF", "125.45")])
        self.assertEqual(config.station_token_for("KMYF"), TOKEN)

    def test_default_env_path_is_system(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PI_ATC_ENV_FILE", None)
            self.assertEqual(config.env_path(), config.SYSTEM_ENV_PATH)

    def test_migrate_to_system_env_from_extra_sources_without_printing_paths(self):
        src = os.path.join(self.tmpdir.name, "old.env")
        dest = os.path.join(self.tmpdir.name, "new.env")
        with open(src, "w") as f:
            f.write("STATIONS=KMYF:126.9\nSTATION_TOKEN=%s\nCAPTURE_KIND=sdr\n" % TOKEN)

        copied = config.migrate_to_system_env(dest_path=dest, extra_sources=[src])
        self.assertGreater(copied, 0)
        data = config.read_env_file(dest)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o640)
        with open(config.public_config_path()) as public_file:
            self.assertNotIn(TOKEN, public_file.read())


class StatusFormatTest(unittest.TestCase):
    def setUp(self):
        self.prev_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_patch = patch.dict(os.environ, {"PI_ATC_STATE_DIR": self.tmpdir.name})
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.tmpdir.cleanup()

        if self.prev_tz is None:
            os.environ.pop("TZ", None)

        else:
            os.environ["TZ"] = self.prev_tz

        time.tzset()

    def test_local_time_has_no_leading_zero(self):
        dt = datetime(2026, 11, 18, 15, 5, tzinfo=timezone.utc)
        self.assertEqual(status.format_local_time(dt), "3:05 PM")
        dt2 = datetime(2026, 11, 18, 9, 7, tzinfo=timezone.utc)
        self.assertEqual(status.format_local_time(dt2), "9:07 AM")

    def test_write_status_keeps_last_upload_and_is_readable(self):
        status.write_status(state="uploading", last_upload="2026-11-18T15:05:00Z")
        status.write_status(state="listening")
        data = status.read_status()
        self.assertEqual(data["state"], "listening")
        self.assertEqual(data["last_upload"], "2026-11-18T15:05:00Z")
        self.assertEqual(stat.S_IMODE(os.stat(status.status_path()).st_mode), 0o644)

    def test_missing_key_points_at_issued_station_key(self):
        message, fix = status.suggestion_for_error("missing_key")
        self.assertIn("station key", fix)
        self.assertIn("ATIS Relay sent you", fix)
        self.assertNotIn("atisrelay.com", fix)
        self.assertNotIn("atisrelay.com", message)


class ServiceCtlTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def fake_ctl(self, args, timeout=30):
        self.calls.append(list(args))

        class Result:
            returncode = 0
            stdout = "active\n" if args and args[0] == "is-active" else "enabled\n"
            stderr = ""

        return Result()

    def test_start_stop_enable_use_ctl(self):
        with patch.object(service, "run_ctl", side_effect=self.fake_ctl):
            self.assertTrue(service.start_worker())
            self.assertTrue(service.stop_worker())
            self.assertTrue(service.enable_service())
            self.assertTrue(service.disable_service())
            self.assertTrue(service.is_worker_active())
            self.assertTrue(service.is_enabled())

        self.assertEqual([call[0] for call in self.calls], [
            "start",
            "stop",
            "enable",
            "disable",
            "is-active",
            "is-enabled",
        ])

    def test_install_settings_writes_override_without_pkexec(self):
        tmpdir = tempfile.TemporaryDirectory()
        env_file = os.path.join(tmpdir.name, "env")
        state_dir = os.path.join(tmpdir.name, "state")
        os.makedirs(state_dir, exist_ok=True)

        with patch.dict(os.environ, {
            "PI_ATC_ENV_FILE": env_file,
            "PI_ATC_STATE_DIR": state_dir,
        }):
            ok = service.install_settings(
                [("KMYF", "126.9")],
                {"KMYF": TOKEN},
                capture_kind="sdr",
            )

        self.assertTrue(ok)
        with open(env_file) as saved:
            self.assertIn("STATION_TOKEN=%s\n" % TOKEN, saved.read())
        tmpdir.cleanup()


class HelperCtlTest(unittest.TestCase):
    def test_rejects_unknown_action(self):
        self.assertEqual(ctl.main(["reboot"]), 2)
        self.assertEqual(ctl.main(["status"]), 2)

    def test_install_config_merges_and_omits_secrets_from_public(self):
        tmpdir = tempfile.TemporaryDirectory()
        src = os.path.join(tmpdir.name, "incoming.env")
        dest = os.path.join(tmpdir.name, "env")
        state_dir = os.path.join(tmpdir.name, "state")
        os.makedirs(state_dir, exist_ok=True)
        with open(src, "w") as f:
            f.write("STATIONS=KMYF:126.9\nSTATION_TOKENS=KMYF:%s\nCAPTURE_KIND=sdr\n" % TOKEN)
        os.chmod(src, 0o600)

        with patch.dict(os.environ, {
            "PI_ATC_ENV_FILE": dest,
            "PI_ATC_STATE_DIR": state_dir,
        }):
            with patch("os.geteuid", return_value=0):
                code = ctl.main(["install-config", src])

        self.assertEqual(code, 0)
        data = config.read_env_file(dest)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o640)
        with open(os.path.join(state_dir, "public.json")) as public_file:
            self.assertNotIn(TOKEN, public_file.read())
        tmpdir.cleanup()

    def test_install_config_rejects_world_writable_source(self):
        tmpdir = tempfile.TemporaryDirectory()
        src = os.path.join(tmpdir.name, "incoming.env")
        with open(src, "w") as f:
            f.write("STATIONS=KMYF:126.9\nSTATION_TOKEN=%s\n" % TOKEN)
        os.chmod(src, 0o666)

        with patch("os.geteuid", return_value=0):
            code = ctl.main(["install-config", src])

        self.assertEqual(code, 1)
        tmpdir.cleanup()


class WordingTest(unittest.TestCase):
    def test_gui_and_readme_use_issued_station_key(self):
        with open(os.path.join(ROOT, "atis_relay_gui.py")) as gui_file:
            gui = gui_file.read()
        with open(os.path.join(ROOT, "README.md")) as readme_file:
            readme = readme_file.read()
        self.assertIn("Paste the station key ATIS Relay sent you", gui)
        self.assertIn("Paste the station key ATIS Relay sent you", readme)
        self.assertNotIn("Paste the station key from atisrelay.com", gui)
        self.assertNotIn("Paste the station key from atisrelay.com", readme)
        self.assertNotIn("~/.config/atis-relay", readme)
        self.assertNotIn("\u2014", readme)
        self.assertIn("docs/images/logo.png", readme)
        self.assertIn("docs/images/install-releases.png", readme)
        self.assertIn("docs/images/install-deb.png", readme)
        self.assertIn("docs/images/install-menu.png", readme)
        for name in ("install-releases.png", "install-deb.png", "install-menu.png"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "images", name)), name)
        self.assertIn("126.9", gui)
        self.assertIn("125.45", gui)
        self.assertIn("126.9", readme)
        self.assertIn("125.45", readme)
        self.assertNotIn("126.8", gui)
        self.assertNotIn("126.8", readme)
        self.assertNotIn("135.1", gui)
        self.assertNotIn("135.1", readme)
        self.assertIn("KMYF or 1G0", gui)
        self.assertIn("`KMYF` or `1G0`", readme)
        self.assertNotIn("3 or 4 letter", gui)
        self.assertNotIn("{ICAO}", readme)

        with open(os.path.join(ROOT, "pi_setup", "setup_portal.py")) as portal_file:
            portal = portal_file.read()

        self.assertIn('pattern="[A-Za-z0-9]{3,4}"', portal)
        self.assertIn("3- or 4-character airport code", portal)
        self.assertNotIn("4-character ICAO", portal)
        self.assertIn('placeholder="1G0"', portal)


class IconPackagingTest(unittest.TestCase):
    def test_hicolor_sizes_exist_and_old_icon_is_gone(self):
        self.assertFalse(os.path.isfile(os.path.join(ROOT, "packaging", "atis-relay.svg")))
        scalable = os.path.join(ROOT, "packaging", "icons", "hicolor", "scalable", "apps", "atis-relay.svg")
        self.assertTrue(os.path.isfile(scalable))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "images", "logo.png")))

        for size in (16, 32, 48, 64, 128, 256, 512):
            path = os.path.join(
                ROOT,
                "packaging",
                "icons",
                "hicolor",
                "%sx%s" % (size, size),
                "apps",
                "atis-relay.png",
            )
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 100)

        source = os.path.join(ROOT, "packaging", "icons", "source", "atis-relay.png")
        self.assertTrue(os.path.isfile(source))
        with open(scalable) as svg_file:
            svg = svg_file.read()
        self.assertNotIn("rotate", svg)
        self.assertNotIn("translate", svg)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', svg)

        gui_path = os.path.join(ROOT, "atis_relay_gui.py")
        with open(gui_path) as gui_file:
            gui = gui_file.read()
        self.assertIn("header_logo_image", gui)
        self.assertIn('icon_file("256x256")', gui)


class StartStopButtonsTest(unittest.TestCase):
    def test_start_disabled_while_listening_stop_disabled_while_stopped(self):
        self.assertEqual(status.start_stop_enabled("listening"), (False, True))
        self.assertEqual(status.start_stop_enabled("uploading"), (False, True))
        self.assertEqual(status.start_stop_enabled("waiting"), (False, True))
        self.assertEqual(status.start_stop_enabled("stopped"), (True, False))
        self.assertEqual(status.start_stop_enabled("stopped", True), (False, True))
        self.assertEqual(status.start_stop_enabled("error"), (True, False))
        self.assertEqual(status.start_stop_enabled("error", True), (False, True))


class UsbRootHubsTest(unittest.TestCase):
    def test_lists_every_root_hub_in_bus_order(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("usb10", "usb3", "usb1", "usb4", "usb2", "1-1", "3-0:1.0"):
                os.mkdir(os.path.join(root, name))

            self.assertEqual(devices.usb_root_hubs(root), ["usb1", "usb2", "usb3", "usb4", "usb10"])

    def test_missing_sysfs_gives_no_hubs(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(devices.usb_root_hubs(os.path.join(root, "missing")), [])


class WorkerTest(unittest.TestCase):
    def test_worker_points_heartbeat_at_the_desktop_unit(self):
        with patch.dict(os.environ):
            os.environ.pop("PI_ATC_CAPTURE_UNIT", None)

            with patch.dict(sys.modules, {"audio_uploader": MagicMock()}):
                with patch.object(worker, "threading"), patch.object(worker, "write_status"):
                    with patch.object(worker, "load_config"), patch.object(worker, "apply_runtime_paths"):
                        with patch.object(worker, "clear_stale_notice") as clear_stale_notice:
                            code = worker.main()

            self.assertEqual(code, 0)
            self.assertEqual(os.environ.get("PI_ATC_CAPTURE_UNIT"), "atis-relay.service")
            clear_stale_notice.assert_called_once_with()


class RemoteCommandsConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_file = os.path.join(self.tmpdir.name, "env")
        self.env_patch = patch.dict(os.environ, {
            "PI_ATC_ENV_FILE": self.env_file,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        })
        self.env_patch.start()
        os.environ.pop("REMOTE_COMMANDS", None)

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def test_remote_commands_are_off_unless_turned_on(self):
        self.assertFalse(config.remote_commands_enabled({}))
        self.assertFalse(config.remote_commands_enabled({"REMOTE_COMMANDS": ""}))
        self.assertFalse(config.remote_commands_enabled({"REMOTE_COMMANDS": "0"}))
        self.assertFalse(config.remote_commands_enabled({"REMOTE_COMMANDS": "off"}))
        self.assertTrue(config.remote_commands_enabled({"REMOTE_COMMANDS": "1"}))
        self.assertTrue(config.remote_commands_enabled({"REMOTE_COMMANDS": " On "}))

    def test_helper_turns_remote_commands_on_and_off(self):
        config.save_desktop_settings(self.env_file, [("KMYF", "126.9")], {"KMYF": TOKEN})
        self.assertFalse(config.read_public_config()["remote_commands"])

        with patch("os.geteuid", return_value=0):
            self.assertEqual(ctl.main(["remote-commands", "on"]), 0)

        self.assertEqual(config.read_env_file(self.env_file)["REMOTE_COMMANDS"], "1")
        self.assertTrue(config.read_public_config()["remote_commands"])
        config.load_config()
        self.assertTrue(config.remote_commands_enabled())

        with patch("os.geteuid", return_value=0):
            self.assertEqual(ctl.main(["remote-commands", "off"]), 0)

        self.assertFalse(config.read_public_config()["remote_commands"])
        config.load_config()
        self.assertFalse(config.remote_commands_enabled())
        self.assertEqual(config.read_env_file(self.env_file)["STATION_TOKEN"], TOKEN)

    def test_saving_airports_keeps_remote_commands_on(self):
        config.set_env_value(self.env_file, "REMOTE_COMMANDS", "1")
        config.save_desktop_settings(self.env_file, [("KMYF", "126.9")], {"KMYF": TOKEN})
        self.assertTrue(config.read_public_config()["remote_commands"])

    def test_turning_remote_commands_on_needs_root(self):
        with patch("os.geteuid", return_value=1000), redirect_stderr(io.StringIO()):
            self.assertEqual(ctl.main(["remote-commands", "on"]), 1)
            self.assertEqual(ctl.main(["remote-commands", "maybe"]), 2)
            self.assertEqual(ctl.main(["remote-commands"]), 2)

        self.assertFalse(os.path.exists(self.env_file))


class RemoteHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_file = os.path.join(self.tmpdir.name, "env")
        self.env_patch = patch.dict(os.environ, {
            "PI_ATC_ENV_FILE": self.env_file,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        })
        self.env_patch.start()
        self.runs = []

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def _fake_run(self, args, **_kwargs):
        self.runs.append(list(args))
        return MagicMock(returncode=0, stdout="", stderr="")

    def _remote(self, action, enabled=True, trusted=True, reset=True):
        config.set_env_value(self.env_file, "REMOTE_COMMANDS", "1" if enabled else "0")

        with patch("os.geteuid", return_value=0), redirect_stderr(io.StringIO()):
            with patch.object(ctl, "_trusted_env_file", return_value=trusted):
                with patch.object(ctl.subprocess, "run", side_effect=self._fake_run):
                    with patch.object(ctl, "reset_rtl_sdr", return_value=reset) as reset_rtl_sdr:
                        code = ctl.main(["remote", action])

        return code, reset_rtl_sdr

    def test_reboot_runs_when_turned_on(self):
        code, _reset = self._remote("reboot")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, [["systemctl", "--no-block", "reboot"]])

    def test_recorder_restart_is_scheduled_so_the_reply_can_be_sent_first(self):
        code, _reset = self._remote("restart_capture")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.runs[0][0], "systemd-run")
        self.assertIn("--on-active=15", self.runs[0])
        self.assertEqual(self.runs[0][-3:], ["systemctl", "restart", "atis-relay.service"])

    def test_usb_reset_only_touches_the_radio(self):
        code, reset_rtl_sdr = self._remote("usb_reset")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, [])
        reset_rtl_sdr.assert_called_once_with()
        code, _reset = self._remote("usb_reset", reset=False)
        self.assertEqual(code, 1)

    def test_nothing_runs_while_turned_off(self):
        for action in ("reboot", "restart_capture", "usb_reset"):
            code, reset_rtl_sdr = self._remote(action, enabled=False)
            self.assertEqual(code, 1)
            reset_rtl_sdr.assert_not_called()

        self.assertEqual(self.runs, [])

    def test_nothing_runs_when_the_settings_file_is_not_roots(self):
        code, _reset = self._remote("reboot", trusted=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.runs, [])

    def test_nothing_runs_without_root_or_for_unknown_commands(self):
        config.set_env_value(self.env_file, "REMOTE_COMMANDS", "1")

        with patch.object(ctl.subprocess, "run", side_effect=self._fake_run), redirect_stderr(io.StringIO()):
            with patch("os.geteuid", return_value=1000):
                self.assertEqual(ctl.main(["remote", "reboot"]), 1)

            with patch("os.geteuid", return_value=0):
                self.assertEqual(ctl.main(["remote", "poweroff"]), 2)
                self.assertEqual(ctl.main(["remote"]), 2)

        self.assertEqual(self.runs, [])

    def test_settings_file_must_be_a_root_owned_regular_file(self):
        config.set_env_value(self.env_file, "REMOTE_COMMANDS", "1")
        self.assertEqual(ctl._trusted_env_file(self.env_file), os.stat(self.env_file).st_uid == 0)
        self.assertFalse(ctl._trusted_env_file(os.path.join(self.tmpdir.name, "missing")))
        link = os.path.join(self.tmpdir.name, "link")
        os.symlink(self.env_file, link)

        with patch.object(ctl.os, "lstat", wraps=os.lstat) as lstat:
            self.assertFalse(ctl._trusted_env_file(link))

        lstat.assert_called_once_with(link)
        owned = MagicMock(st_mode=stat.S_IFREG | 0o640, st_uid=0)

        with patch.object(ctl.os, "lstat", return_value=owned):
            self.assertTrue(ctl._trusted_env_file(self.env_file))

        owned.st_mode = stat.S_IFREG | 0o660

        with patch.object(ctl.os, "lstat", return_value=owned):
            self.assertFalse(ctl._trusted_env_file(self.env_file))

        owned.st_mode = stat.S_IFREG | 0o640
        owned.st_uid = 1000

        with patch.object(ctl.os, "lstat", return_value=owned):
            self.assertFalse(ctl._trusted_env_file(self.env_file))


class RemoteServiceTest(unittest.TestCase):
    def test_worker_asks_systemd_to_start_the_remote_unit(self):
        done = MagicMock(returncode=0)

        with patch.object(service.subprocess, "run", return_value=done) as run:
            self.assertTrue(service.run_remote_command("restart_capture"))

        self.assertEqual(run.call_args.args[0], [
            "systemctl",
            "--no-ask-password",
            "start",
            "atis-relay-remote@restart_capture.service",
        ])

    def test_refused_or_unknown_commands_report_failure(self):
        with patch.object(service.subprocess, "run", return_value=MagicMock(returncode=1)) as run:
            self.assertFalse(service.run_remote_command("reboot"))
            self.assertEqual(run.call_count, 1)
            self.assertFalse(service.run_remote_command("poweroff; reboot"))
            self.assertEqual(run.call_count, 1)

        with patch.object(service.subprocess, "run", side_effect=OSError("no systemctl")):
            self.assertFalse(service.run_remote_command("reboot"))

    def test_setting_and_driver_install_go_through_the_root_helper(self):
        calls = []

        def fake_ctl(args, timeout=30):
            calls.append((list(args), timeout))
            return MagicMock(returncode=0)

        with patch.object(service, "run_ctl", side_effect=fake_ctl):
            self.assertTrue(service.set_remote_commands(True))
            self.assertTrue(service.set_remote_commands(False))
            self.assertTrue(service.install_v4_driver())

        self.assertEqual(calls, [
            (["remote-commands", "on"], 30),
            (["remote-commands", "off"], 30),
            (["install-v4-driver"], service.V4_DRIVER_TIMEOUT_SEC),
        ])

        for action in ("remote-commands", "install-v4-driver"):
            self.assertIn(action, service.PRIV_ACTIONS)


class NoticeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_patch = patch.dict(os.environ, {"PI_ATC_STATE_DIR": self.tmpdir.name})
        self.state_patch.start()
        os.environ.pop("REMOTE_COMMANDS", None)

    def tearDown(self):
        self.state_patch.stop()
        self.tmpdir.cleanup()

    def test_no_notice_by_default(self):
        self.assertEqual(status.read_notice(), {"message": "", "fix": "", "boot_id": ""})

    def test_notice_survives_status_updates_and_is_readable_by_the_desktop_user(self):
        status.write_notice("ATIS Relay asked this Pi to restart.", "Restart it.")
        status.write_status(state="listening")
        status.write_status(state="uploading")
        self.assertEqual(status.read_notice()["message"], "ATIS Relay asked this Pi to restart.")
        self.assertEqual(status.read_notice()["fix"], "Restart it.")
        self.assertEqual(stat.S_IMODE(os.stat(status.notice_path()).st_mode), 0o644)

    def test_restart_notice_stays_until_the_pi_restarts(self):
        with patch.object(status, "read_boot_id", return_value="boot-a"):
            status.write_notice("restart", "fix", until_reboot=True)
            self.assertFalse(status.clear_stale_notice())
            self.assertEqual(status.read_notice()["message"], "restart")

        with patch.object(status, "read_boot_id", return_value="boot-b"):
            self.assertTrue(status.clear_stale_notice())

        self.assertEqual(status.read_notice()["message"], "")

    def test_other_notices_clear_when_the_recorder_starts_again(self):
        with patch.object(status, "read_boot_id", return_value="boot-a"):
            status.write_notice("recorder", "fix")
            self.assertTrue(status.clear_stale_notice())

        self.assertEqual(status.read_notice()["message"], "")

    def test_request_while_turned_off_is_refused_with_a_notice(self):
        with patch.object(status, "read_boot_id", return_value="boot-a"):
            for action in ("reboot", "restart_capture", "usb_reset"):
                self.assertFalse(worker._remote_permitted(action))
                notice = status.read_notice()
                expected = status.suggestion_for_error("remote_%s" % action)
                self.assertEqual((notice["message"], notice["fix"]), expected)
                self.assertNotEqual(expected, status.suggestion_for_error("unknown"))
                self.assertEqual(notice["boot_id"], "boot-a" if action == "reboot" else "")

    def test_request_while_turned_on_is_allowed_without_a_notice(self):
        with patch.dict(os.environ, {"REMOTE_COMMANDS": "1"}):
            self.assertTrue(worker._remote_permitted("reboot"))

        self.assertEqual(status.read_notice()["message"], "")

    def test_restart_request_while_turned_off_does_not_restart(self):
        with patch.object(worker, "run_remote_command") as run_remote_command:
            worker._desktop_reboot()

        run_remote_command.assert_not_called()
        self.assertEqual(status.read_notice()["message"], "ATIS Relay asked this Pi to restart.")

    def test_restart_request_while_turned_on_restarts(self):
        with patch.dict(os.environ, {"REMOTE_COMMANDS": "1"}):
            with patch.object(worker, "run_remote_command", return_value=True) as run_remote_command:
                worker._desktop_reboot()

        run_remote_command.assert_called_once_with("reboot")
        self.assertEqual(status.read_notice()["message"], "")

    def test_failed_restart_is_reported(self):
        with patch.dict(os.environ, {"REMOTE_COMMANDS": "1"}):
            with patch.object(worker, "run_remote_command", return_value=False):
                with patch.object(status, "read_boot_id", return_value="boot-a"):
                    worker._desktop_reboot()

        notice = status.read_notice()
        self.assertEqual((notice["message"], notice["fix"]), status.suggestion_for_error("remote_reboot_failed"))
        self.assertEqual(notice["boot_id"], "boot-a")

    def test_worker_hands_its_remote_handlers_to_the_heartbeat(self):
        class Halt(BaseException):
            pass

        fake = MagicMock()

        with patch.dict(sys.modules, {"heartbeat": fake}):
            with patch.object(worker.time, "sleep", side_effect=Halt):
                with self.assertRaises(Halt):
                    worker._heartbeat_loop()

        fake.run_checkin.assert_called_once_with(
            reboot_fn=worker._desktop_reboot,
            permit_fn=worker._remote_permitted,
            restart_fn=worker._desktop_restart_capture,
            usb_reset_fn=worker._desktop_usb_reset,
        )

        with patch.object(worker, "run_remote_command", return_value=True) as run_remote_command:
            self.assertTrue(worker._desktop_restart_capture())
            self.assertTrue(worker._desktop_usb_reset())

        self.assertEqual([call.args for call in run_remote_command.call_args_list], [
            ("restart_capture",),
            ("usb_reset",),
        ])


def _fake_usb_device(root, name, vendor, product, manufacturer="", label="", busnum="1", devnum="2"):
    base = os.path.join(root, name)
    os.makedirs(base)
    values = {
        "idVendor": vendor,
        "idProduct": product,
        "manufacturer": manufacturer,
        "product": label,
        "busnum": busnum,
        "devnum": devnum,
    }

    for key, value in values.items():
        with open(os.path.join(base, key), "w") as f:
            f.write(value + "\n")


class RadioDriverTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmpdir.name, "sys")
        os.makedirs(self.root)
        os.makedirs(os.path.join(self.root, "usb1"))
        _fake_usb_device(self.root, "1-1", "1d6b", "0002", "Linux", "xHCI Host Controller")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_finds_only_rtl_sdr_dongles(self):
        _fake_usb_device(self.root, "1-2", "0BDA", "2838", "Realtek", "RTL2838UHIDIR", "1", "5")
        found = devices.rtl_sdr_devices(self.root)
        self.assertEqual([item["name"] for item in found], ["1-2"])
        self.assertEqual((found[0]["busnum"], found[0]["devnum"]), ("1", "5"))
        self.assertFalse(devices.rtl_sdr_v4_present(self.root))
        self.assertEqual(devices.rtl_sdr_devices(os.path.join(self.root, "missing")), [])

    def test_recognises_the_blog_v4(self):
        _fake_usb_device(self.root, "3-1", "0bda", "2838", "RTLSDRBlog", "Blog V4", "3", "4")
        self.assertTrue(devices.rtl_sdr_v4_present(self.root))

    def test_blog_v3_is_not_a_v4(self):
        _fake_usb_device(self.root, "3-1", "0bda", "2838", "RTLSDRBlog", "Blog V3")
        self.assertFalse(devices.rtl_sdr_v4_present(self.root))

    def _library(self, name, data):
        path = os.path.join(self.tmpdir.name, name)

        with open(path, "wb") as f:
            f.write(data)

        return path

    def _supports(self, library, ldd_code=0):
        ldd = MagicMock(returncode=ldd_code, stdout=(
            "\tlinux-vdso.so.1 (0x0000ffff)\n"
            "\tlibrtlsdr.so.0 => %s (0x0000ffff)\n"
            "\tlibusb-1.0.so.0 => /lib/aarch64-linux-gnu/libusb-1.0.so.0 (0x0000ffff)\n"
        ) % library)

        with patch.object(devices, "PRIVATE_RTL_DIR", os.path.join(self.tmpdir.name, "private")):
            with patch.object(devices.shutil, "which", return_value="/usr/bin/rtl_fm"):
                with patch.object(devices, "_run", return_value=ldd) as run:
                    result = devices.rtl_fm_supports_v4()

        run.assert_called_once_with(["ldd", "/usr/bin/rtl_fm"])
        return result

    def test_old_driver_is_recognised_as_lacking_v4_support(self):
        old = self._library("librtlsdr.so.0.6.0", b"\x7fELF Generic RTL2832U OEM \x00 R828D \x00")
        self.assertIs(self._supports(old), False)

    def test_new_driver_is_recognised_as_supporting_v4(self):
        new = self._library("librtlsdr.so.2.0.2", b"\x7fELF RTLSDRBlog\x00Blog V4\x00RTL-SDR Blog V4 Detected\x00")
        self.assertIs(self._supports(new), True)

    def test_driver_that_cannot_be_inspected_is_not_reported_as_missing(self):
        self.assertIsNone(self._supports(os.path.join(self.tmpdir.name, "gone.so")))
        self.assertIsNone(self._supports("/dev/null", ldd_code=1))

        with patch.object(devices, "PRIVATE_RTL_DIR", os.path.join(self.tmpdir.name, "private")):
            with patch.object(devices.shutil, "which", return_value=None):
                self.assertIsNone(devices.rtl_fm_supports_v4())

        with patch.object(devices, "rtl_sdr_v4_present", return_value=True):
            with patch.object(devices, "rtl_fm_supports_v4", return_value=None):
                self.assertFalse(devices.v4_driver_missing())

    def test_driver_is_missing_only_for_a_v4_on_an_old_driver(self):
        cases = (
            (True, False, True),
            (True, True, False),
            (False, False, False),
        )

        for present, supported, missing in cases:
            with patch.object(devices, "rtl_sdr_v4_present", return_value=present):
                with patch.object(devices, "rtl_fm_supports_v4", return_value=supported):
                    self.assertEqual(devices.v4_driver_missing(), missing)

    def test_installed_driver_is_preferred_and_supports_v4(self):
        private = os.path.join(self.tmpdir.name, "private")

        with patch.object(devices, "PRIVATE_RTL_DIR", private):
            self.assertEqual(devices.rtl_fm_path(), "rtl_fm")
            os.makedirs(os.path.join(private, "bin"))
            binary = os.path.join(private, "bin", "rtl_fm")

            with open(binary, "w") as f:
                f.write("#!/bin/sh\n")

            os.chmod(binary, 0o755)
            self.assertEqual(devices.rtl_fm_path(), binary)

            with patch.object(devices, "_run") as run:
                self.assertIs(devices.rtl_fm_supports_v4(), True)

            run.assert_not_called()

    def test_radio_test_uses_the_installed_driver(self):
        seen = []

        def fake_run(cmd, **_kwargs):
            seen.append(cmd)
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch.object(devices, "rtl_fm_path", return_value="/opt/pi-atc/rtl-sdr/bin/rtl_fm"):
            with patch.object(devices.subprocess, "run", side_effect=fake_run):
                devices.test_capture("sdr", kind="sdr", freq="126.9", seconds=1)

        self.assertEqual(len(seen), 1)
        self.assertIn(" /opt/pi-atc/rtl-sdr/bin/rtl_fm -f 126.9M -M am ", seen[0])

    def test_usb_reset_targets_the_radio_device_node(self):
        _fake_usb_device(self.root, "3-1", "0bda", "2838", "RTLSDRBlog", "Blog V4", "3", "12")
        nodes = os.path.join(self.tmpdir.name, "dev")

        with patch.object(devices.os, "open", return_value=41) as opened:
            with patch.object(devices.os, "close") as closed:
                with patch.object(devices.fcntl, "ioctl") as ioctl:
                    self.assertTrue(devices.reset_rtl_sdr(self.root, nodes))

        opened.assert_called_once_with(os.path.join(nodes, "003", "012"), os.O_WRONLY)
        ioctl.assert_called_once_with(41, 0x5514, 0)
        closed.assert_called_once_with(41)

    def test_usb_reset_reports_failure_without_a_radio(self):
        with patch.object(devices.os, "open") as opened:
            self.assertFalse(devices.reset_rtl_sdr(self.root, self.tmpdir.name))

        opened.assert_not_called()
        _fake_usb_device(self.root, "3-1", "0bda", "2838", "RTLSDRBlog", "Blog V4", "3", "12")
        self.assertFalse(devices.reset_rtl_sdr(self.root, os.path.join(self.tmpdir.name, "dev")))

        with patch.object(devices.os, "open", return_value=41), patch.object(devices.os, "close") as closed:
            with patch.object(devices.fcntl, "ioctl", side_effect=OSError("busy")):
                self.assertFalse(devices.reset_rtl_sdr(self.root, self.tmpdir.name))

        closed.assert_called_once_with(41)


class RadioDriverHoldTest(unittest.TestCase):
    def _run(self, kind, missing):
        class Halt(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "PI_ATC_ENV_FILE": os.path.join(tmp, "env"),
                "PI_ATC_STATE_DIR": tmp,
                "STATIONS": "KMYF:126.9",
                "CAPTURE_KIND": kind,
            }):
                import audio_uploader

                with patch.object(audio_uploader, "TMP_DIR", tmp), patch.object(audio_uploader, "wait_for_config"):
                    with patch.object(audio_uploader, "read_cpu_temp", return_value=40.0):
                        with patch.object(audio_uploader, "v4_driver_missing", return_value=missing):
                            with patch.object(audio_uploader, "record_wav", side_effect=Halt) as record_wav:
                                with patch.object(audio_uploader, "write_status") as write_status:
                                    with patch.object(audio_uploader.time, "sleep", side_effect=Halt):
                                        with redirect_stdout(io.StringIO()):
                                            with self.assertRaises(Halt):
                                                audio_uploader.main()

        return record_wav, write_status

    def test_v4_on_an_old_driver_is_reported_instead_of_recorded(self):
        record_wav, write_status = self._run("sdr", True)
        record_wav.assert_not_called()
        write_status.assert_called_once()
        reported = write_status.call_args.kwargs
        self.assertEqual(reported["state"], "error")
        self.assertEqual((reported["error"], reported["fix"]), status.suggestion_for_error("v4_driver"))
        self.assertIn("Install driver", reported["fix"])

    def test_recording_goes_ahead_when_the_driver_is_fine(self):
        record_wav, _write_status = self._run("sdr", False)
        record_wav.assert_called_once()

    def test_usb_audio_is_not_held_back_by_the_radio_driver(self):
        record_wav, _write_status = self._run("alsa", True)
        record_wav.assert_called_once()


class RemotePackagingTest(unittest.TestCase):
    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts)) as handle:
            return handle.read()

    def test_remote_unit_runs_the_root_helper(self):
        unit = self._read("packaging", "atis-relay-remote@.service")
        self.assertIn("Type=oneshot", unit)
        self.assertIn("ExecStart=/usr/lib/atis-relay/atis-relay-ctl remote %i", unit)
        self.assertNotIn("User=", unit)

    def test_only_the_recorder_user_may_start_the_remote_units(self):
        rules = self._read("packaging", "10-com.atisrelay.host.rules")
        self.assertIn('action.id == "org.freedesktop.systemd1.manage-units"', rules)
        self.assertIn('subject.user == "atis-relay"', rules)
        self.assertIn('action.lookup("verb") == "start"', rules)

        for action in config.REMOTE_ACTIONS:
            self.assertIn('"%s"' % (service.REMOTE_UNIT % action), rules)

        self.assertNotIn(service.UNIT_NAME, rules)

    def test_package_ships_the_remote_unit_and_driver_installer(self):
        nfpm = self._read("packaging", "nfpm.yaml")
        self.assertIn("dst: /lib/systemd/system/atis-relay-remote@.service", nfpm)
        self.assertIn("dst: %s" % ctl.V4_DRIVER_INSTALLER, nfpm)
        installer = os.path.join(ROOT, "packaging", "install-v4-driver")
        self.assertTrue(os.access(installer, os.X_OK))
        script = self._read("packaging", "install-v4-driver")
        self.assertIn('PREFIX="%s"' % devices.PRIVATE_RTL_DIR, script)
        self.assertIn('TAG="v2.0.2"', script)
        self.assertIn('COMMIT="619ac3186ea0ffc092615e1f59f7397e5e6f668c"', script)
        self.assertIn(devices.PRIVATE_RTL_DIR, self._read("packaging", "postrm"))

    def test_driver_install_needs_root_and_runs_the_installer(self):
        with patch("os.geteuid", return_value=1000), redirect_stderr(io.StringIO()):
            self.assertEqual(ctl.main(["install-v4-driver"]), 1)

        with patch("os.geteuid", return_value=0):
            with patch.object(ctl.subprocess, "run", return_value=MagicMock(returncode=0)) as run:
                self.assertEqual(ctl.main(["install-v4-driver"]), 0)

        self.assertEqual(run.call_args.args[0], [ctl.V4_DRIVER_INSTALLER])

        with patch("os.geteuid", return_value=0):
            with patch.object(ctl.subprocess, "run", return_value=MagicMock(returncode=100)):
                self.assertEqual(ctl.main(["install-v4-driver"]), 100)


class ThermalPauseTest(unittest.TestCase):
    def test_overheating_is_reported_before_recording_pauses(self):
        class Halt(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "PI_ATC_ENV_FILE": os.path.join(tmp, "env"),
                "PI_ATC_STATE_DIR": tmp,
            }):
                import audio_uploader

                hot = audio_uploader.TEMP_SHUTDOWN_C + 1

                with patch.object(audio_uploader, "TMP_DIR", tmp), patch.object(audio_uploader, "wait_for_config"):
                    with patch.object(audio_uploader, "read_cpu_temp", return_value=hot):
                        with patch.object(audio_uploader, "set_usb_power") as usb_power:
                            with patch.object(audio_uploader, "write_status") as write_status:
                                with patch.object(audio_uploader.time, "sleep", side_effect=Halt):
                                    with redirect_stdout(io.StringIO()):
                                        with self.assertRaises(Halt):
                                            audio_uploader.main()

        usb_power.assert_called_once_with(False)
        write_status.assert_called_once()
        reported = write_status.call_args.kwargs
        self.assertEqual(reported["state"], "error")
        self.assertIn("too hot", reported["error"])
        self.assertEqual((reported["error"], reported["fix"]), status.suggestion_for_error("too_hot"))


if __name__ == "__main__":
    unittest.main()
