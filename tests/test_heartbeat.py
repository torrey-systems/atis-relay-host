#!/usr/bin/env python3
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import mock_open, patch

import heartbeat
import pi_atc_state as state


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3:
    def __init__(self, objects=None, get_error=None):
        self.objects = dict(objects or {})
        self.get_error = get_error
        self.puts = []

    def get_object(self, Bucket, Key):
        if self.get_error is not None:
            raise self.get_error

        if Key not in self.objects:
            raise ClientError("NoSuchKey")

        body = self.objects[Key]

        if isinstance(body, str):
            body = body.encode("utf-8")

        return {"Body": FakeBody(body)}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.puts.append({"key": Key, "body": Body, "content_type": ContentType})
        self.objects[Key] = Body

    def close(self):
        pass


class HeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.prev_state_dir = os.environ.get("PI_ATC_STATE_DIR")
        self.prev_pi_id = os.environ.get("PI_ID")
        os.environ["PI_ATC_STATE_DIR"] = self.tmpdir.name
        os.environ["PI_ID"] = "test-pi"

    def tearDown(self):
        if self.prev_state_dir is None:
            os.environ.pop("PI_ATC_STATE_DIR", None)

        else:
            os.environ["PI_ATC_STATE_DIR"] = self.prev_state_dir

        if self.prev_pi_id is None:
            os.environ.pop("PI_ID", None)

        else:
            os.environ["PI_ID"] = self.prev_pi_id

        self.tmpdir.cleanup()

    def test_collect_status_schema(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        state.record_last_upload(
            "KNKX",
            "raw_atc_stream/KNKX/KNKX_abc.mp3",
            now=now,
        )
        status = heartbeat.collect_status(now=now, pi_id=state.derive_pi_id())
        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(status["pi_id"], "test-pi")
        self.assertEqual(status["hostname"], socket.gethostname())
        self.assertEqual(status["timestamp"], "2026-10-23T21:00:00Z")
        self.assertEqual(status["last_upload"], {
            "station": "KNKX",
            "key": "raw_atc_stream/KNKX/KNKX_abc.mp3",
            "ts": "2026-10-23T21:00:00Z",
        })
        self.assertIn("KNKX", status["last_uploads"])
        self.assertIn("setup_episodes", status)
        self.assertIn("wifi", status)
        self.assertIn("pi_atc_service", status)

    def test_reboot_once_then_skip(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        command = {"id": "11111111-1111-1111-1111-111111111111", "action": "reboot"}
        s3 = FakeS3({"pi-commands/test-pi.json": json.dumps(command)})
        reboots = []
        first = heartbeat.run_checkin(
            s3_client=s3,
            reboot_fn=lambda: reboots.append("first"),
            now=now,
            pi_id="test-pi",
        )
        self.assertEqual(len(reboots), 1)
        self.assertEqual(first["last_command"]["id"], command["id"])
        ack_keys = [item["key"] for item in s3.puts]
        self.assertIn("pi-status/test-pi.json", ack_keys)
        self.assertIn("pi-commands/test-pi.ack.json", ack_keys)
        second = heartbeat.run_checkin(
            s3_client=s3,
            reboot_fn=lambda: reboots.append("second"),
            now=now + timedelta(minutes=5),
            pi_id="test-pi",
        )
        self.assertEqual(len(reboots), 1)
        self.assertEqual(second["last_command"]["id"], command["id"])

    def test_access_denied_is_quiet(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        s3 = FakeS3(get_error=ClientError("AccessDenied"))
        reboots = []
        status = heartbeat.run_checkin(
            s3_client=s3,
            reboot_fn=lambda: reboots.append("nope"),
            now=now,
            pi_id="test-pi",
        )
        self.assertEqual(reboots, [])
        self.assertEqual(status["commands"]["fetch"], "access_denied")
        self.assertTrue(any(item["key"] == "pi-status/test-pi.json" for item in s3.puts))

    def test_no_such_key_is_ok(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        s3 = FakeS3()
        status = heartbeat.run_checkin(
            s3_client=s3,
            reboot_fn=lambda: None,
            now=now,
            pi_id="test-pi",
        )
        self.assertEqual(status["commands"]["fetch"], "no_such_key")

    def test_future_command_does_not_reboot(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        command = {
            "id": "22222222-2222-2222-2222-222222222222",
            "action": "reboot",
            "not_before": "2026-10-24T03:00:00Z",
        }
        s3 = FakeS3({"pi-commands/test-pi.json": json.dumps(command)})
        reboots = []
        status = heartbeat.run_checkin(
            s3_client=s3,
            reboot_fn=lambda: reboots.append("nope"),
            now=now,
            pi_id="test-pi",
        )
        self.assertEqual(reboots, [])
        self.assertEqual(status["pending_command"]["id"], command["id"])
        self.assertIsNone(status["last_command"])

    def test_http_payload_fields(self):
        payload = heartbeat.heartbeat_payload_from_status({
            "cpu_temp_c": 48.5,
            "throttled": "0x50000",
            "uptime_seconds": 12.9,
            "pi_atc_service": {"active": "active"},
        })
        self.assertEqual(payload["temp_c"], 48.5)
        self.assertEqual(payload["throttled_flags"], 0x50000)
        self.assertEqual(payload["uptime_seconds"], 12)
        self.assertEqual(payload["capture_status"], "running")
        self.assertIsNone(payload["last_error"])

    def test_capture_unit_defaults_to_factory_unit(self):
        with patch.dict(os.environ):
            os.environ.pop("PI_ATC_CAPTURE_UNIT", None)
            self.assertEqual(heartbeat.capture_unit(), "pi-atc.service")

    def test_service_state_follows_capture_unit(self):
        calls = []

        class Result:
            returncode = 0
            stdout = "active\nrunning\n"

        def fake_run(args, timeout=5):
            calls.append(list(args))
            return Result()

        with patch.dict(os.environ, {"PI_ATC_CAPTURE_UNIT": "atis-relay.service"}):
            with patch.object(heartbeat, "_run", side_effect=fake_run):
                service = heartbeat.read_service_state()

        self.assertEqual(calls[0][-1], "atis-relay.service")
        self.assertEqual(service, {"active": "active", "sub": "running"})
        payload = heartbeat.heartbeat_payload_from_status({"pi_atc_service": service})
        self.assertEqual(payload["capture_status"], "running")

    def test_usb_reset_covers_every_root_hub(self):
        opener = mock_open()

        with patch.object(heartbeat, "usb_root_hubs", return_value=["usb1", "usb2", "usb3", "usb4"]):
            with patch.object(heartbeat, "open", opener, create=True):
                with patch.object(heartbeat.time, "sleep"):
                    self.assertTrue(heartbeat.reset_usb())

        written = [call.args[0] for call in opener.return_value.write.call_args_list]
        self.assertEqual(written, ["usb1", "usb1", "usb2", "usb2", "usb3", "usb3", "usb4", "usb4"])


if __name__ == "__main__":
    unittest.main()
