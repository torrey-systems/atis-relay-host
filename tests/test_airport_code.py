#!/usr/bin/env python3
import os
import tempfile
import unittest
from unittest.mock import patch

import pi_atc_config as config
import pi_atc_http as http


class AirportCodeTest(unittest.TestCase):
    def test_normalize_uppercases_without_k_prefix(self):
        self.assertEqual(config.normalize_airport_code("kmyf"), "KMYF")
        self.assertEqual(config.normalize_airport_code("1g0"), "1G0")
        self.assertEqual(config.normalize_airport_code(" 1G0 "), "1G0")
        self.assertEqual(config.normalize_airport_code("MYF"), "MYF")
        self.assertNotEqual(config.normalize_airport_code("1G0"), "K1G0")
        self.assertNotEqual(config.normalize_airport_code("MYF"), "KMYF")

    def test_valid_accepts_icao_and_faa_lid(self):
        self.assertTrue(config.valid_airport_code("KMYF"))
        self.assertTrue(config.valid_airport_code("kmyf"))
        self.assertTrue(config.valid_airport_code("1G0"))
        self.assertTrue(config.valid_airport_code("1g0"))
        self.assertFalse(config.valid_airport_code("1G"))
        self.assertFalse(config.valid_airport_code("KMYFX"))
        self.assertFalse(config.valid_airport_code(""))
        self.assertFalse(config.valid_airport_code("KM-Y"))

    def test_parse_stations_keeps_faa_lid(self):
        self.assertEqual(
            config.parse_stations("1g0:119.475,kmyf:126.9"),
            [("1G0", "119.475"), ("KMYF", "126.9")],
        )
        self.assertEqual(
            config.parse_station_tokens("1g0:tok-1g0,kmyf:tok-myf"),
            {"1G0": "tok-1g0", "KMYF": "tok-myf"},
        )

    def test_station_token_for_faa_lid(self):
        environ = {
            "STATIONS": "KMYF:126.9,1G0:119.475",
            "STATION_TOKENS": "KMYF:tok-myf,1G0:tok-1g0",
        }
        self.assertEqual(config.station_token_for("kmyf", environ), "tok-myf")
        self.assertEqual(config.station_token_for("1g0", environ), "tok-1g0")
        self.assertEqual(config.station_token_for("K1G0", environ), "")

    def test_save_settings_keeps_faa_lid(self):
        tmpdir = tempfile.TemporaryDirectory()
        env_file = os.path.join(tmpdir.name, "env")
        state_dir = os.path.join(tmpdir.name, "state")
        os.makedirs(state_dir, exist_ok=True)

        with patch.dict(os.environ, {
            "PI_ATC_ENV_FILE": env_file,
            "PI_ATC_STATE_DIR": state_dir,
        }):
            config.save_desktop_settings(
                env_file,
                [("kmyf", "126.9"), ("1g0", "119.475")],
                {"kmyf": "tok-myf", "1g0": "tok-1g0"},
            )

        data = config.read_env_file(env_file)
        self.assertEqual(data["STATIONS"], "KMYF:126.9,1G0:119.475")
        self.assertIn("1G0:tok-1g0", data["STATION_TOKENS"])
        self.assertIn("KMYF:tok-myf", data["STATION_TOKENS"])
        self.assertNotIn("K1G0", data["STATIONS"])
        self.assertNotIn("K1G0", data["STATION_TOKENS"])
        tmpdir.cleanup()

    def test_api_urls_use_uppercase_code_unchanged(self):
        self.assertEqual(
            http.api_url("https://atisrelay.com", "kmyf", "heartbeat"),
            "https://atisrelay.com/api/pi/KMYF/heartbeat",
        )
        self.assertEqual(
            http.api_url("https://atisrelay.com", "1g0", "heartbeat"),
            "https://atisrelay.com/api/pi/1G0/heartbeat",
        )
        self.assertEqual(
            http.api_url("https://atisrelay.com", "1g0", "audio"),
            "https://atisrelay.com/api/pi/1G0/audio",
        )
        self.assertEqual(
            http.api_url("https://atisrelay.com", "1g0", "commands"),
            "https://atisrelay.com/api/pi/1G0/commands",
        )
        self.assertEqual(
            http.api_url("https://atisrelay.com", "1g0", "commands", 15, "ack"),
            "https://atisrelay.com/api/pi/1G0/commands/15/ack",
        )
        self.assertNotIn("K1G0", http.api_url("https://atisrelay.com", "1G0", "heartbeat"))

    def test_s3_object_key_uses_faa_lid(self):
        key = "raw_atc_stream/" + config.normalize_airport_code("1g0") + "/clip.mp3"
        self.assertEqual(key, "raw_atc_stream/1G0/clip.mp3")
        self.assertNotIn("K1G0", key)
        key_icao = "raw_atc_stream/" + config.normalize_airport_code("kmyf") + "/clip.mp3"
        self.assertEqual(key_icao, "raw_atc_stream/KMYF/clip.mp3")


if __name__ == "__main__":
    unittest.main()
