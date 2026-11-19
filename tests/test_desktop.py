#!/usr/bin/env python3
import os
import stat
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import atis_relay_ctl as ctl
import atis_relay_service as service
import atis_relay_status as status
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

    def test_save_settings_mode_600_and_public_config_omits_token(self):
        config.save_desktop_settings(
            self.env_file,
            [("KMYF", "126.8")],
            {"KMYF": TOKEN},
            audio_device="plughw:1,0",
            capture_kind="alsa",
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.env_file).st_mode), 0o600)
        with open(self.env_file) as env_file:
            data = env_file.read()
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, data)
        self.assertIn("CAPTURE_KIND=alsa", data)
        public = config.read_public_config()
        self.assertEqual(public["stations"], [["KMYF", "126.8"]])
        self.assertEqual(public["tokens"]["KMYF"], True)
        with open(config.public_config_path()) as public_file:
            self.assertNotIn(TOKEN, public_file.read())
        self.assertEqual(stat.S_IMODE(os.stat(config.public_config_path()).st_mode), 0o644)
        self.assertEqual(config.env_path(), self.env_file)

    def test_save_keeps_existing_key_when_blank(self):
        config.save_desktop_settings(self.env_file, [("KMYF", "126.8")], {"KMYF": TOKEN})
        config.save_desktop_settings(self.env_file, [("KMYF", "126.8")], {"KMYF": ""})
        data = config.read_env_file(self.env_file)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)

    def test_default_env_path_is_system(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PI_ATC_ENV_FILE", None)
            self.assertEqual(config.env_path(), config.SYSTEM_ENV_PATH)

    def test_migrate_to_system_env_from_extra_sources_without_printing_paths(self):
        src = os.path.join(self.tmpdir.name, "old.env")
        dest = os.path.join(self.tmpdir.name, "new.env")
        with open(src, "w") as f:
            f.write("STATIONS=KMYF:126.8\nSTATION_TOKEN=%s\nCAPTURE_KIND=sdr\n" % TOKEN)

        copied = config.migrate_to_system_env(dest_path=dest, extra_sources=[src])
        self.assertGreater(copied, 0)
        data = config.read_env_file(dest)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)
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
                [("KMYF", "126.8")],
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
            f.write("STATIONS=KMYF:126.8\nSTATION_TOKENS=KMYF:%s\nCAPTURE_KIND=sdr\n" % TOKEN)
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
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)
        with open(os.path.join(state_dir, "public.json")) as public_file:
            self.assertNotIn(TOKEN, public_file.read())
        tmpdir.cleanup()

    def test_install_config_rejects_world_writable_source(self):
        tmpdir = tempfile.TemporaryDirectory()
        src = os.path.join(tmpdir.name, "incoming.env")
        with open(src, "w") as f:
            f.write("STATIONS=KMYF:126.8\nSTATION_TOKEN=%s\n" % TOKEN)
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


if __name__ == "__main__":
    unittest.main()
