#!/usr/bin/env python3
import io
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import pi_atc_cli


TOKEN = "super-secret-station-token-xyz"


class SetKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_path = os.path.join(self.tmpdir.name, "env")
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=\nSTATIONS=KMYF:126.9\n")
        self.restarts = []
        self.prev_euid = os.geteuid

    def tearDown(self):
        self.tmpdir.cleanup()

    def restart(self, names=None):
        self.restarts.append(list(names) if names else ["default"])

    def test_set_key_from_argv_does_not_print_token(self):
        stdout = io.StringIO()

        with patch("os.geteuid", return_value=0):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", TOKEN],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("super-secret", output)
        self.assertIn("station key saved", output)
        data = open(self.env_path).read()
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, data)
        self.assertIn("STATION_TOKENS=KMYF:%s\n" % TOKEN, data)
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o640)
        self.assertTrue(self.restarts)

    def test_set_key_from_stdin(self):
        stdout = io.StringIO()
        stdin = io.StringIO(TOKEN + "\n")
        stdin.isatty = lambda: False

        with patch("os.geteuid", return_value=0):
            code = pi_atc_cli.cmd_set_key(
                ["set-key"],
                stdin=stdin,
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, stdout.getvalue())
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, open(self.env_path).read())

    def test_set_key_works_without_root(self):
        stdout = io.StringIO()

        with patch("os.geteuid", return_value=1):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", TOKEN],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, stdout.getvalue())
        self.assertTrue(self.restarts)
        self.assertIn("STATION_TOKEN=%s\n" % TOKEN, open(self.env_path).read())

    def test_status_masks_token(self):
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=%s\nSTATIONS=KMYF:126.9\n" % TOKEN)

        stdout = io.StringIO()
        environ = {
            "STATION_TOKEN": TOKEN,
            "STATIONS": "KMYF:126.9",
            "ATIS_RELAY_URL": "https://atisrelay.com",
            "PI_ATC_ENV_FILE": self.env_path,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        }

        with patch.dict(os.environ, environ, clear=False):
            code = pi_atc_cli.cmd_status(stdout=stdout, environ=environ)

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertIn("token: set", output)
        self.assertIn("KMYF:126.9", output)

    def test_set_key_with_icao_for_second_station(self):
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=\nSTATION_TOKENS=\nSTATIONS=KMYF:126.9,KSEE:125.45\n")

        other = "other-secret-station-token-abc"
        stdout = io.StringIO()

        with patch("os.geteuid", return_value=0):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", "--icao", "KSEE", other],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn(other, output)
        data = open(self.env_path).read()
        self.assertIn("STATION_TOKENS=KSEE:%s\n" % other, data)
        self.assertNotIn("STATION_TOKEN=%s\n" % other, data)

    def test_set_key_requires_icao_when_two_stations(self):
        with open(self.env_path, "w") as f:
            f.write("STATIONS=KMYF:126.9,KSEE:125.45\nSTATION_TOKEN=\n")

        stdout = io.StringIO()

        with patch("os.geteuid", return_value=0):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", TOKEN],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 1)
        output = stdout.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertIn("--icao", output)
        self.assertNotIn(TOKEN, open(self.env_path).read())
        self.assertEqual(self.restarts, [])

    def test_status_masks_per_icao_tokens(self):
        other = "second-secret-station-token"
        environ = {
            "STATION_TOKEN": "",
            "STATION_TOKENS": "KMYF:%s,KSEE:%s" % (TOKEN, other),
            "STATIONS": "KMYF:126.9,KSEE:125.45",
            "ATIS_RELAY_URL": "https://atisrelay.com",
            "PI_ATC_ENV_FILE": self.env_path,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        }
        stdout = io.StringIO()

        with patch.dict(os.environ, environ, clear=False):
            code = pi_atc_cli.cmd_status(stdout=stdout, environ=environ)

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(other, output)
        self.assertIn("KMYF:126.9 token: set", output)
        self.assertIn("KSEE:125.45 token: set", output)

    def test_set_key_with_faa_lid_alongside_icao(self):
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=\nSTATION_TOKENS=\nSTATIONS=KMYF:126.9,1g0:119.475\n")

        other = "other-secret-station-token-abc"
        stdout = io.StringIO()

        with patch("os.geteuid", return_value=0):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", "--icao", "1g0", other],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn(other, output)
        data = open(self.env_path).read()
        self.assertIn("STATION_TOKENS=1G0:%s\n" % other, data)
        self.assertNotIn("K1G0", data)
        self.assertNotIn("STATION_TOKEN=%s\n" % other, data)

        environ = {
            "STATION_TOKEN": "",
            "STATION_TOKENS": "KMYF:%s,1G0:%s" % (TOKEN, other),
            "STATIONS": "KMYF:126.9,1G0:119.475",
            "ATIS_RELAY_URL": "https://atisrelay.com",
            "PI_ATC_ENV_FILE": self.env_path,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        }
        stdout = io.StringIO()

        with patch.dict(os.environ, environ, clear=False):
            code = pi_atc_cli.cmd_status(stdout=stdout, environ=environ)

        self.assertEqual(code, 0)
        status = stdout.getvalue()
        self.assertNotIn(TOKEN, status)
        self.assertNotIn(other, status)
        self.assertIn("KMYF:126.9 token: set", status)
        self.assertIn("1G0:119.475 token: set", status)

    def test_status_unreadable_env_reports_permission_not_not_set(self):
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=%s\nSTATIONS=KMYF:126.9\n" % TOKEN)

        os.chmod(self.env_path, 0o000)
        stdout = io.StringIO()
        environ = {
            "PI_ATC_ENV_FILE": self.env_path,
            "PI_ATC_STATE_DIR": self.tmpdir.name,
        }

        try:
            with patch.dict(os.environ, environ, clear=False):
                code = pi_atc_cli.cmd_status(stdout=stdout, environ=environ)

        finally:
            os.chmod(self.env_path, 0o640)

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("can't read config (permission)", output)
        self.assertNotIn("token: not set", output)
        self.assertNotIn("stations: not set", output)
        self.assertNotIn(TOKEN, output)


if __name__ == "__main__":
    unittest.main()
