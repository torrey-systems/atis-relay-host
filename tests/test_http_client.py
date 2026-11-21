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
        session = FakeSession([FakeResponse(201, {
            "key": "raw_atc_stream/KMYF/20261118T200000Z-abc.mp3",
            "icao": "KMYF",
            "received_at": "2026-11-18T20:00:05Z",
        })])
        key, body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "kmyf",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(key, "raw_atc_stream/KMYF/20261118T200000Z-abc.mp3")
        self.assertEqual(body["icao"], "KMYF")
        self.assertEqual(body["received_at"], "2026-11-18T20:00:05Z")
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
        session = FakeSession([FakeResponse(403, {"error": "forbidden"})])

        with self.assertRaises(http.Forbidden) as ctx:
            http.upload_audio(
                "https://atisrelay.com",
                "other-icao-token",
                "KMYF",
                self.mp3,
                "2026-11-18T20:00:00Z",
                session=session,
                sleep_fn=self.sleep,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertNotIn("other-icao-token", str(ctx.exception))

    def test_upload_404_413_422_do_not_retry(self):
        for status, text in ((404, "not_found"), (413, "too_large"), (422, "invalid_file")):
            session = FakeSession([FakeResponse(status, {"error": text})])
            self.sleeps = []

            with self.assertRaises(http.ClientError) as ctx:
                http.upload_audio(
                    "https://atisrelay.com",
                    "station-token-value",
                    "KMYF",
                    self.mp3,
                    "2026-11-18T20:00:00Z",
                    session=session,
                    sleep_fn=self.sleep,
                )

            self.assertEqual(ctx.exception.status_code, status)
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(self.sleeps, [])

    def test_upload_200_is_not_success(self):
        session = FakeSession([FakeResponse(200, {"key": "nope"})])

        with self.assertRaises(http.ClientError) as ctx:
            http.upload_audio(
                "https://atisrelay.com",
                "station-token-value",
                "KMYF",
                self.mp3,
                "2026-11-18T20:00:00Z",
                session=session,
                sleep_fn=self.sleep,
            )

        self.assertEqual(ctx.exception.status_code, 200)
        self.assertEqual(len(session.calls), 1)

    def test_upload_retries_429_then_succeeds(self):
        session = FakeSession([
            FakeResponse(429, text="rate limited"),
            FakeResponse(201, {"key": "raw_atc_stream/KMYF/clip.mp3", "icao": "KMYF", "received_at": "2026-11-18T20:00:05Z"}),
        ])
        key, body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "KMYF",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
            retry_delays=(0.01,),
        )
        self.assertEqual(key, "raw_atc_stream/KMYF/clip.mp3")
        self.assertEqual(body["icao"], "KMYF")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.sleeps, [0.01])

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

    def test_faa_lid_urls_are_not_k_prefixed(self):
        session = FakeSession([
            FakeResponse(201, {
                "key": "raw_atc_stream/1G0/clip.mp3",
                "icao": "1G0",
                "received_at": "2026-11-18T20:00:05Z",
            }),
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": [{"id": 15, "command": "reboot"}]}),
            FakeResponse(200, {"ok": True, "status": "acked"}),
        ])
        key, body = http.upload_audio(
            "https://atisrelay.com",
            "station-token-value",
            "1g0",
            self.mp3,
            "2026-11-18T20:00:00Z",
            session=session,
            sleep_fn=self.sleep,
        )
        self.assertEqual(key, "raw_atc_stream/1G0/clip.mp3")
        self.assertEqual(body["icao"], "1G0")
        http.post_heartbeat(
            "https://atisrelay.com",
            "tok",
            "1g0",
            {"temp_c": 48.5},
            session=session,
            sleep_fn=self.sleep,
        )
        http.fetch_commands(
            "https://atisrelay.com",
            "tok",
            "1g0",
            session=session,
            sleep_fn=self.sleep,
        )
        http.ack_command(
            "https://atisrelay.com",
            "tok",
            "1g0",
            15,
            ok=True,
            result="rebooting",
            session=session,
            sleep_fn=self.sleep,
        )
        urls = [call["url"] for call in session.calls]
        self.assertEqual(urls, [
            "https://atisrelay.com/api/pi/1G0/audio",
            "https://atisrelay.com/api/pi/1G0/heartbeat",
            "https://atisrelay.com/api/pi/1G0/commands",
            "https://atisrelay.com/api/pi/1G0/commands/15/ack",
        ])

        for url in urls:
            self.assertNotIn("K1G0", url)
            self.assertNotIn("/api/pi/KMYF/", url)


class HeartbeatHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.prev_state = os.environ.get("PI_ATC_STATE_DIR")
        self.prev_token = os.environ.get("STATION_TOKEN")
        self.prev_tokens = os.environ.get("STATION_TOKENS")
        self.prev_stations = os.environ.get("STATIONS")
        os.environ["PI_ATC_STATE_DIR"] = self.tmpdir.name
        os.environ["STATION_TOKEN"] = "tok"
        os.environ.pop("STATION_TOKENS", None)
        os.environ["STATIONS"] = "KMYF:126.9"
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

        if self.prev_tokens is None:
            os.environ.pop("STATION_TOKENS", None)

        else:
            os.environ["STATION_TOKENS"] = self.prev_tokens

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

    def _command_session(self, action, cmd_id=21):
        return FakeSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": [{"id": cmd_id, "command": action}]}),
            FakeResponse(200, {"ok": True, "status": "acked"}),
        ])

    def test_refused_reboot_is_acked_as_failed_and_does_not_reboot(self):
        import heartbeat

        asked = []
        session = self._command_session("reboot")
        status = heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
            permit_fn=lambda action: asked.append(action) or False,
        )
        self.assertEqual(asked, ["reboot"])
        self.assertEqual(self.reboots, [])
        self.assertEqual(session.calls[2]["url"], "https://atisrelay.com/api/pi/KMYF/commands/21/ack")
        self.assertEqual(session.calls[2]["json"], {
            "ok": False,
            "result": "remote commands are turned off on this Pi",
        })
        self.assertEqual(status["last_command"]["id"], "KMYF:21")
        self.assertEqual(status["last_command"]["result"], "ignore")

    def _desktop_checkin(self, action, remote_commands, cmd_id=21):
        import atis_relay_status
        import atis_relay_worker
        import heartbeat
        from unittest.mock import patch

        session = self._command_session(action, cmd_id)

        with patch.dict(os.environ, {"REMOTE_COMMANDS": remote_commands}):
            with patch.object(atis_relay_worker, "run_remote_command", return_value=True) as run_remote_command:
                heartbeat.run_http_checkin(
                    reboot_fn=atis_relay_worker._desktop_reboot,
                    session=session,
                    sleep_fn=lambda _s: None,
                    pi_id="test-pi",
                    permit_fn=atis_relay_worker._remote_permitted,
                    restart_fn=atis_relay_worker._desktop_restart_capture,
                    usb_reset_fn=atis_relay_worker._desktop_usb_reset,
                )

        ran = [call.args[0] for call in run_remote_command.call_args_list]
        return session.calls[2]["json"], ran, atis_relay_status.read_notice()

    def test_desktop_restart_request_while_turned_off_leaves_a_warning(self):
        ack, ran, notice = self._desktop_checkin("reboot", "0")
        self.assertEqual(ack["ok"], False)
        self.assertEqual(ran, [])
        self.assertEqual(notice["message"], "ATIS Relay asked this Pi to restart.")
        self.assertIn("Raspberry Pi menu", notice["fix"])

    def test_desktop_requests_while_turned_on_run_without_a_warning(self):
        for cmd_id, action in enumerate(("reboot", "restart_capture", "usb_reset"), start=21):
            with self.subTest(action=action):
                ack, ran, notice = self._desktop_checkin(action, "1", cmd_id)
                self.assertEqual(ack["ok"], True)
                self.assertEqual(ran, [action])
                self.assertEqual(notice["message"], "")

    def test_refused_command_is_not_asked_about_twice(self):
        import heartbeat

        asked = []

        for _attempt in range(2):
            heartbeat.run_http_checkin(
                reboot_fn=lambda: self.reboots.append("nope"),
                session=self._command_session("reboot"),
                sleep_fn=lambda _s: None,
                pi_id="test-pi",
                permit_fn=lambda action: asked.append(action) or False,
            )

        self.assertEqual(asked, ["reboot"])

    def test_refused_restart_and_usb_reset_do_not_run(self):
        import heartbeat

        ran = []

        for cmd_id, action in enumerate(("restart_capture", "usb_reset"), start=21):
            session = self._command_session(action, cmd_id)
            heartbeat.run_http_checkin(
                reboot_fn=lambda: self.reboots.append("nope"),
                session=session,
                sleep_fn=lambda _s: None,
                pi_id="test-pi",
                permit_fn=lambda _action: False,
                restart_fn=lambda: ran.append("restart") or True,
                usb_reset_fn=lambda: ran.append("usb") or True,
            )
            self.assertEqual(session.calls[2]["json"]["ok"], False)

        self.assertEqual(ran, [])

    def test_permitted_commands_use_the_given_handlers(self):
        import heartbeat

        ran = []
        expected = {"restart_capture": "restarted", "usb_reset": "usb reset"}

        for cmd_id, (action, result) in enumerate(expected.items(), start=21):
            session = self._command_session(action, cmd_id)
            heartbeat.run_http_checkin(
                reboot_fn=lambda: self.reboots.append("nope"),
                session=session,
                sleep_fn=lambda _s: None,
                pi_id="test-pi",
                permit_fn=lambda _action: True,
                restart_fn=lambda: ran.append("restart") or True,
                usb_reset_fn=lambda: ran.append("usb") or True,
            )
            self.assertEqual(session.calls[2]["json"], {"ok": True, "result": result})

        self.assertEqual(ran, ["restart", "usb"])
        self.assertEqual(self.reboots, [])

    def test_permitted_reboot_still_acks_then_reboots(self):
        import heartbeat

        session = self._command_session("reboot")
        heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append(len(session.calls)),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
            permit_fn=lambda _action: True,
        )
        self.assertEqual(self.reboots, [3])
        self.assertEqual(session.calls[2]["json"], {"ok": True, "result": "rebooting"})

    def test_command_is_remembered_before_the_reply_is_sent(self):
        import heartbeat
        import pi_atc_state

        remembered = []

        class RecordingSession(FakeSession):
            def request(self, method, url, timeout=None, **kwargs):
                if url.endswith("/ack"):
                    remembered.append((pi_atc_state.read_last_command() or {}).get("id"))

                return super().request(method, url, timeout=timeout, **kwargs)

        session = RecordingSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": [{"id": 21, "command": "restart_capture"}]}),
            FakeResponse(200, {"ok": True, "status": "acked"}),
        ])
        heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
            restart_fn=lambda: True,
        )
        self.assertEqual(remembered, ["KMYF:21"])

    def test_factory_handlers_are_used_when_none_are_given(self):
        import heartbeat
        from unittest.mock import patch

        with patch.object(heartbeat, "restart_capture", return_value=True) as restart_capture:
            with patch.object(heartbeat, "reset_usb", return_value=True) as reset_usb:
                for cmd_id, action in enumerate(("restart_capture", "usb_reset"), start=21):
                    heartbeat.run_http_checkin(
                        reboot_fn=lambda: self.reboots.append("nope"),
                        session=self._command_session(action, cmd_id),
                        sleep_fn=lambda _s: None,
                        pi_id="test-pi",
                    )

        restart_capture.assert_called_once_with()
        reset_usb.assert_called_once_with()

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

    def test_http_uses_token_per_icao(self):
        import heartbeat

        os.environ.pop("STATION_TOKEN", None)
        os.environ["STATION_TOKENS"] = "KMYF:tok-myf,KSEE:tok-see"
        os.environ["STATIONS"] = "KMYF:126.9,KSEE:125.45"
        session = FakeSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": []}),
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": []}),
        ])
        heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
        )
        self.assertEqual(self.reboots, [])
        self.assertEqual(session.calls[0]["url"], "https://atisrelay.com/api/pi/KMYF/heartbeat")
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer tok-myf")
        self.assertEqual(session.calls[0]["json"]["last_error"], None)
        self.assertIn("capture_status", session.calls[0]["json"])
        self.assertEqual(session.calls[1]["url"], "https://atisrelay.com/api/pi/KMYF/commands")
        self.assertEqual(session.calls[2]["url"], "https://atisrelay.com/api/pi/KSEE/heartbeat")
        self.assertEqual(session.calls[2]["headers"]["Authorization"], "Bearer tok-see")
        self.assertEqual(session.calls[3]["url"], "https://atisrelay.com/api/pi/KSEE/commands")

    def test_http_uses_token_for_faa_lid_and_icao(self):
        import heartbeat

        os.environ.pop("STATION_TOKEN", None)
        os.environ["STATION_TOKENS"] = "KMYF:tok-myf,1G0:tok-1g0"
        os.environ["STATIONS"] = "KMYF:126.9,1g0:119.475"
        session = FakeSession([
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": []}),
            FakeResponse(200, {"ok": True}),
            FakeResponse(200, {"commands": []}),
        ])
        heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
        )
        self.assertEqual(self.reboots, [])
        self.assertEqual(session.calls[0]["url"], "https://atisrelay.com/api/pi/KMYF/heartbeat")
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer tok-myf")
        self.assertEqual(session.calls[1]["url"], "https://atisrelay.com/api/pi/KMYF/commands")
        self.assertEqual(session.calls[2]["url"], "https://atisrelay.com/api/pi/1G0/heartbeat")
        self.assertEqual(session.calls[2]["headers"]["Authorization"], "Bearer tok-1g0")
        self.assertEqual(session.calls[3]["url"], "https://atisrelay.com/api/pi/1G0/commands")

        for call in session.calls:
            self.assertNotIn("K1G0", call["url"])

    def test_shared_token_is_not_used_for_two_icaos(self):
        import heartbeat

        os.environ["STATION_TOKEN"] = "shared-token"
        os.environ.pop("STATION_TOKENS", None)
        os.environ["STATIONS"] = "KMYF:126.9,KSEE:125.45"
        session = FakeSession([])
        status = heartbeat.run_http_checkin(
            reboot_fn=lambda: self.reboots.append("nope"),
            session=session,
            sleep_fn=lambda _s: None,
            pi_id="test-pi",
        )
        self.assertEqual(session.calls, [])
        self.assertEqual(status["commands"]["fetch"], "skipped")


if __name__ == "__main__":
    unittest.main()
