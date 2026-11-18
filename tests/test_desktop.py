#!/usr/bin/env python3
import os
import stat
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import atis_relay_service as service
import atis_relay_status as status
import pi_atc_config as config


TOKEN = "desktop-secret-station-token"


class DesktopConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_home = os.path.join(self.tmpdir.name, "config")
        self.data_home = os.path.join(self.tmpdir.name, "data")
        self.env_patch = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": self.config_home,
            "XDG_DATA_HOME": self.data_home,
            "PI_ATC_ENV_FILE": os.path.join(self.config_home, "atis-relay", "env"),
            "PI_ATC_STATE_DIR": os.path.join(self.data_home, "atis-relay"),
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def test_save_user_settings_mode_600_and_no_token_in_status_helpers(self):
        path = config.user_env_path()
        config.save_desktop_settings(
            path,
            [("KMYF", "126.8")],
            {"KMYF": TOKEN},
            audio_device="plughw:1,0",
            capture_kind="alsa",
        )
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        data = open(path).read()
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, data)
        self.assertIn("CAPTURE_KIND=alsa", data)
        masked = config.mask_secret(TOKEN)
        self.assertNotIn(TOKEN, masked)
        self.assertEqual(config.env_path(), path)

    def test_save_keeps_existing_key_when_blank(self):
        path = config.user_env_path()
        config.save_desktop_settings(path, [("KMYF", "126.8")], {"KMYF": TOKEN})
        config.save_desktop_settings(path, [("KMYF", "126.8")], {"KMYF": ""})
        data = config.read_env_file(path)
        self.assertEqual(data["STATION_TOKEN"], TOKEN)

    def test_user_paths_use_xdg(self):
        self.assertEqual(config.user_config_dir(), os.path.join(self.config_home, "atis-relay"))
        self.assertEqual(config.user_state_dir(), os.path.join(self.data_home, "atis-relay"))


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

    def test_write_status_keeps_last_upload(self):
        status.write_status(state="uploading", last_upload="2026-11-18T15:05:00Z")
        status.write_status(state="listening")
        data = status.read_status()
        self.assertEqual(data["state"], "listening")
        self.assertEqual(data["last_upload"], "2026-11-18T15:05:00Z")


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_home = os.path.join(self.tmpdir.name, "config")
        self.env_patch = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config_home})
        self.env_patch.start()
        self.calls = []

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def fake_systemctl(self, args):
        self.calls.append(list(args))

        class Result:
            returncode = 0
            stdout = "active\n"

        return Result()

    def test_enable_writes_desktop_file(self):
        with patch.object(service, "_systemctl_user", side_effect=self.fake_systemctl):
            ok = service.enable_autostart()

        self.assertTrue(ok)
        path = service.autostart_path()
        self.assertTrue(os.path.isfile(path))
        text = open(path).read()
        self.assertIn("Exec=atis-relay --worker", text)
        self.assertTrue(any(call[:2] == ["enable", "--now"] for call in self.calls))

    def test_disable_removes_desktop_file(self):
        with patch.object(service, "_systemctl_user", side_effect=self.fake_systemctl):
            service.enable_autostart()
            service.disable_autostart()

        self.assertFalse(os.path.isfile(service.autostart_path()))
        self.assertTrue(any(call[:2] == ["disable", "--now"] for call in self.calls))


if __name__ == "__main__":
    unittest.main()
