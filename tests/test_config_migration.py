#!/usr/bin/env python3
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pi_atc_config as config


SECRET = "legacy-aws-secret-value"
TOKEN = "legacy-station-token"


class ConfigMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.legacy = os.path.join(self.tmpdir.name, "legacy.env")
        self.dest = os.path.join(self.tmpdir.name, "env")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_copies_unset_keys_without_printing_values(self):
        with open(self.legacy, "w") as f:
            f.write("STATIONS=KMYF:126.8,KNKX:124.9\n")
            f.write("PI_ID=nkx-relay\n")
            f.write("AWS_ACCESS_KEY_ID=AKIATESTNOTREAL1\n")
            f.write("AWS_SECRET_ACCESS_KEY=%s\n" % SECRET)
            f.write("STATION_TOKEN=%s\n" % TOKEN)

        with open(self.dest, "w") as f:
            f.write("ATIS_RELAY_URL=https://atisrelay.com\nSTATION_TOKEN=\nSTATIONS=\n")

        buf = io.StringIO()

        with redirect_stdout(buf):
            copied = config.migrate_legacy_env(self.legacy, self.dest)

        output = buf.getvalue()
        self.assertGreater(copied, 0)
        self.assertNotIn(SECRET, output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("AKIATESTNOTREAL1", output)
        dest = config.read_env_file(self.dest)
        self.assertEqual(dest["STATIONS"], "KMYF:126.8,KNKX:124.9")
        self.assertEqual(dest["PI_ID"], "nkx-relay")
        self.assertEqual(dest["STATION_TOKEN"], TOKEN)
        self.assertEqual(dest["AWS_SECRET_ACCESS_KEY"], SECRET)
        self.assertEqual(stat.S_IMODE(os.stat(self.dest).st_mode), 0o600)

    def test_does_not_overwrite_existing_values(self):
        with open(self.legacy, "w") as f:
            f.write("STATIONS=KOLD:118.0\nPI_ID=old-id\n")

        with open(self.dest, "w") as f:
            f.write("STATIONS=KMYF:126.8\nPI_ID=kept\n")

        copied = config.migrate_legacy_env(self.legacy, self.dest)
        dest = config.read_env_file(self.dest)
        self.assertEqual(copied, 0)
        self.assertEqual(dest["STATIONS"], "KMYF:126.8")
        self.assertEqual(dest["PI_ID"], "kept")

    def test_cli_migrate_does_not_print_secrets(self):
        with open(self.legacy, "w") as f:
            f.write("STATION_TOKEN=%s\n" % TOKEN)

        buf = io.StringIO()

        with patch("pi_atc_config.env_path", return_value=self.dest):
            with patch("pi_atc_config.LEGACY_ENV_PATH", self.legacy):
                with redirect_stdout(buf):
                    code = config.main(["migrate"])

        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, buf.getvalue())
        self.assertIn("copied", buf.getvalue())

    def test_upload_mode(self):
        self.assertEqual(config.upload_mode({"STATION_TOKEN": "abc"}), "http")
        self.assertEqual(config.upload_mode({
            "AWS_ACCESS_KEY_ID": "AKIATESTNOTREAL1",
            "AWS_SECRET_ACCESS_KEY": SECRET,
        }), "s3")
        self.assertEqual(config.upload_mode({
            "STATION_TOKEN": "abc",
            "AWS_ACCESS_KEY_ID": "AKIATESTNOTREAL1",
            "AWS_SECRET_ACCESS_KEY": SECRET,
        }), "http")
        self.assertEqual(config.upload_mode({}), "none")
        self.assertEqual(config.upload_mode({
            "STATION_TOKEN": "abc",
            "STATIONS": "KMYF:126.8,KSEE:135.1",
        }), "none")
        self.assertEqual(config.upload_mode({
            "STATION_TOKENS": "KMYF:abc,KSEE:def",
            "STATIONS": "KMYF:126.8,KSEE:135.1",
        }), "http")

    def test_station_token_for_icao(self):
        environ = {
            "STATIONS": "KMYF:126.8,KSEE:135.1",
            "STATION_TOKENS": "KMYF:tok-myf,KSEE:tok-see",
        }
        self.assertEqual(config.station_token_for("kmyf", environ), "tok-myf")
        self.assertEqual(config.station_token_for("KSEE", environ), "tok-see")
        self.assertEqual(config.station_token_for("KNKX", environ), "")

    def test_station_token_shorthand_single_station_only(self):
        self.assertEqual(config.station_token_for("KMYF", {
            "STATIONS": "KMYF:126.8",
            "STATION_TOKEN": "only-one",
        }), "only-one")
        self.assertEqual(config.station_token_for("KMYF", {
            "STATIONS": "KMYF:126.8,KSEE:135.1",
            "STATION_TOKEN": "only-one",
        }), "")
        self.assertEqual(config.station_token_for("KSEE", {
            "STATIONS": "KMYF:126.8,KSEE:135.1",
            "STATION_TOKEN": "only-one",
        }), "")

    def test_migrate_station_tokens(self):
        with open(self.legacy, "w") as f:
            f.write("STATIONS=KMYF:126.8,KSEE:135.1\n")
            f.write("STATION_TOKENS=KMYF:%s,KSEE:%s\n" % (TOKEN, SECRET))

        copied = config.migrate_legacy_env(self.legacy, self.dest)
        dest = config.read_env_file(self.dest)
        self.assertGreater(copied, 0)
        self.assertEqual(dest["STATION_TOKENS"], "KMYF:%s,KSEE:%s" % (TOKEN, SECRET))

    def test_mask_secret(self):
        masked = config.mask_secret(TOKEN)
        self.assertNotIn(TOKEN, masked)
        self.assertIn("set", masked)


if __name__ == "__main__":
    unittest.main()
