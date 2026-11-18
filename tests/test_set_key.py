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
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=\nSTATIONS=KMYF:126.8\n")
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
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
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

    def test_set_key_requires_root(self):
        stdout = io.StringIO()

        with patch("os.geteuid", return_value=1):
            code = pi_atc_cli.cmd_set_key(
                ["set-key", TOKEN],
                stdout=stdout,
                restart_fn=self.restart,
                path=self.env_path,
            )

        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, stdout.getvalue())
        self.assertEqual(self.restarts, [])
        self.assertNotIn(TOKEN, open(self.env_path).read())

    def test_status_masks_token(self):
        with open(self.env_path, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=%s\nSTATIONS=KMYF:126.8\n" % TOKEN)

        stdout = io.StringIO()
        environ = {
            "STATION_TOKEN": TOKEN,
            "STATIONS": "KMYF:126.8",
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
        self.assertIn("KMYF:126.8", output)


if __name__ == "__main__":
    unittest.main()
