#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

import requests

import pi_atc_http as http


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")

        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append({
            "method": method,
            "url": url,
            "timeout": timeout,
            "headers": kwargs.get("headers"),
            "files": kwargs.get("files"),
            "data": kwargs.get("data"),
            "json": kwargs.get("json"),
        })

        if not self.responses:
            raise AssertionError("unexpected request %s %s" % (method, url))

        item = self.responses.pop(0)

        if isinstance(item, Exception):
            raise item

        return item


class HttpClientTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mp3 = os.path.join(self.tmpdir.name, "KMYF_abc123.mp3")
        with open(self.mp3, "wb") as f:
            f.write(b"ID3fake")

        self.sleeps = []

    def tearDown(self):
        self.tmpdir.cleanup()

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def test_upload_multipart_shape_and_201(self):
        session = FakeSession([FakeResponse(201, {"ok": True, "key": "audio/KMYF/KMYF_abc123.mp3"})])
        key, body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "kmyf",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(key, "audio/KMYF/KMYF_abc123.mp3")
        self.assertEqual(body["ok"], True)
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://atisrelay.com/api/pi/KMYF/audio")
        self.assertEqual(call["headers"]["Authorization"], "Bearer station-token-value")
        self.assertEqual(call["data"]["recorded_at"], "2026-11-18T20:00:00Z")
        filename, handle, content_type = call["files"]["file"]
        self.assertEqual(filename, "KMYF_abc123.mp3")
        self.assertEqual(content_type, "audio/mpeg")
        self.assertTrue(hasattr(handle, "read"))
        self.assertEqual(self.sleeps, [])

    def test_upload_401_does_not_retry(self):
        session = FakeSession([FakeResponse(401, {"error": "unauthorized"})])

        with self.assertRaises(http.AuthRejected) as ctx:
            http.upload_audio(
                "https://atisrelay.com",
                "bad-token",
                "KMYF",
                self.mp3,
                "2026-11-18T20:00:00Z",
                session=session,
                sleep_fn=self.sleep,
            )

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertNotIn("bad-token", str(ctx.exception))

    def test_upload_403_does_not_retry(self):
        session = FakeSession([FakeResponse(403, {"error": "unauthorized"})])

        with self.assertRaises(http.AuthRejected):
            http.upload_audio(
                "https://atisrelay.com",
                "bad-token",
                "KMYF",
                self.mp3,
                "2026-11-18T20:00:00Z",
                session=session,
                sleep_fn=self.sleep,
            )

        self.assertEqual(len(session.calls), 1)

    def test_upload_retries_5xx_then_succeeds(self):
        session = FakeSession([
            FakeResponse(503, {"error": "busy"}),
            FakeResponse(500, {"error": "oops"}),
            FakeResponse(201, {"ok": True}),
        ])
        key, body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "KMYF",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
            retry_delays=(0.01, 0.02, 0.03),
        )
        self.assertEqual(key, "KMYF_abc123.mp3")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(self.sleeps, [0.01, 0.02])
        self.assertEqual(body["ok"], True)

    def test_upload_retries_network_error(self):
        session = FakeSession([
            requests.ConnectionError("down"),
            FakeResponse(201, {"ok": True}),
        ])
        key, _body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "KMYF",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
            retry_delays=(0.01,),
        )
        self.assertEqual(key, "KMYF_abc123.mp3")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.sleeps, [0.01])

    def test_heartbeat_and_commands_urls(self):
        session = FakeSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": [{"id": 15, "command": "reboot"}]}),
            FakeResponse(200, {"ok": True, "status": "acked"}),
        ])
        body = http.post_heartbeat(
            "https://atisrelay.com",
            "tok",
            "KMYF",
            {"temp_c": 48.5},
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(body["ok"], True)
        commands = http.fetch_commands(
            "https://atisrelay.com",
            "tok",
            "KMYF",
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(commands[0]["id"], 15)
        status, ack = http.ack_command(
            "https://atisrelay.com",
            "tok",
            "KMYF",
            15,
            ok=True,
            result="rebooting",
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["status"], "acked")
        self.assertEqual(session.calls[0]["url"], "https://atisrelay.com/api/pi/KMYF/heartbeat")
        self.assertEqual(session.calls[1]["url"], "https://atisrelay.com/api/pi/KMYF/commands")
        self.assertEqual(session.calls[2]["url"], "https://atisrelay.com/api/pi/KMYF/commands/15/ack")
        self.assertEqual(session.calls[2]["json"], {"ok": True, "result": "rebooting"})


class HeartbeatHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.prev_state = os.environ.get("PI_ATC_STATE_DIR")
        self.prev_token = os.environ.get("STATION_TOKEN")
        self.prev_stations = os.environ.get("STATIONS")
        os.environ["PI_ATC_STATE_DIR"] = self.tmpdir.name
        os.environ["STATION_TOKEN"] = "tok"
        os.environ["STATIONS"] = "KMYF:126.8"
        self.reboots = []

    def tearDown(self):
        if self.prev_state is None:
            os.environ.pop("PI_ATC_STATE_DIR", None)

        else:
            os.environ["PI_ATC_STATE_DIR"] = self.prev_state

        if self.prev_token is None:
            os.environ.pop("STATION_TOKEN", None)

        else:
            os.environ["STATION_TOKEN"] = self.prev_token

        if self.prev_stations is None:
            os.environ.pop("STATIONS", None)

        else:
            os.environ["STATIONS"] = self.prev_stations

        self.tmpdir.cleanup()

    def test_http_reboot_acks_then_reboots(self):
        import heartbeat

        session = FakeSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": [{"id": 15, "command": "reboot"}]}),
            FakeResponse(200, {"ok": True, "status": "acked"}),
        ])
        status = heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("yes"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
        )
        self.assertEqual(self.reboots, ["yes"])
        self.assertEqual(status["last_command"]["id"], "KMYF:15")
        self.assertEqual(session.calls[2]["json"]["result"], "rebooting")

    def test_http_401_stops_without_reboot(self):
        import heartbeat

        session = FakeSession([FakeResponse(401, {"error": "unauthorized"})])
        status = heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
        )
        self.assertEqual(self.reboots, [])
        self.assertEqual(status["commands"]["fetch"], "access_denied")


if __name__ == "__main__":
    unittest.main()
