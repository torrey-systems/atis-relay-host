#!/usr/bin/env python3
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pi_atc_state as state


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.prev_state_dir = os.environ.get("PI_ATC_STATE_DIR")
        self.prev_pi_id = os.environ.get("PI_ID")
        os.environ["PI_ATC_STATE_DIR"] = self.tmpdir.name

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


class TestDerivePiId(StateTest):
    def test_override_from_env(self):
        os.environ["PI_ID"] = "nkx-relay"
        self.assertEqual(state.derive_pi_id(), "nkx-relay")

    def test_blank_override_falls_back_to_hostname(self):
        os.environ["PI_ID"] = "  "
        self.assertEqual(state.derive_pi_id(), socket.gethostname().strip())

    def test_default_is_hostname_not_random(self):
        os.environ.pop("PI_ID", None)
        first = state.derive_pi_id()
        second = state.derive_pi_id()
        self.assertEqual(first, second)
        self.assertEqual(first, socket.gethostname().strip())
        self.assertNotEqual(first, "")


class TestLastUpload(StateTest):
    def test_records_station_key_and_ts(self):
        now = datetime(2026, 10, 23, 20, 55, tzinfo=timezone.utc)
        state.record_last_upload("KNKX", "raw_atc_stream/KNKX/KNKX_abc.mp3", now=now)
        last = state.read_last_upload()
        self.assertEqual(last, {
            "station": "KNKX",
            "key": "raw_atc_stream/KNKX/KNKX_abc.mp3",
            "ts": "2026-10-23T20:55:00Z",
        })

    def test_per_station_map(self):
        t1 = datetime(2026, 10, 23, 20, 50, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 23, 20, 55, tzinfo=timezone.utc)
        state.record_last_upload("KNKX", "raw_atc_stream/KNKX/a.mp3", now=t1)
        state.record_last_upload("KMYF", "raw_atc_stream/KMYF/b.mp3", now=t2)
        uploads = state.read_last_uploads()
        self.assertEqual(set(uploads), {"KNKX", "KMYF"})
        self.assertEqual(uploads["KNKX"]["key"], "raw_atc_stream/KNKX/a.mp3")
        self.assertEqual(uploads["KMYF"]["station"], "KMYF")
        last = state.read_last_upload()
        self.assertEqual(last["station"], "KMYF")
        self.assertEqual(last["key"], "raw_atc_stream/KMYF/b.mp3")

    def test_updates_one_station_without_dropping_the_other(self):
        t1 = datetime(2026, 10, 23, 20, 50, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 23, 20, 55, tzinfo=timezone.utc)
        t3 = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        state.record_last_upload("KNKX", "raw_atc_stream/KNKX/a.mp3", now=t1)
        state.record_last_upload("KMYF", "raw_atc_stream/KMYF/b.mp3", now=t2)
        state.record_last_upload("KNKX", "raw_atc_stream/KNKX/c.mp3", now=t3)
        uploads = state.read_last_uploads()
        self.assertEqual(uploads["KNKX"]["key"], "raw_atc_stream/KNKX/c.mp3")
        self.assertEqual(uploads["KMYF"]["key"], "raw_atc_stream/KMYF/b.mp3")
        self.assertEqual(state.read_last_upload()["station"], "KNKX")


class TestCommands(StateTest):
    def test_execute_reboot(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        result = state.evaluate_command(
            {"id": "cmd-1", "action": "reboot", "requested_by": "rails"},
            now=now,
        )
        self.assertEqual(result["decision"], "execute")
        self.assertEqual(result["id"], "cmd-1")
        self.assertTrue(state.should_persist_command(result))

    def test_skip_same_id(self):
        result = state.evaluate_command(
            {"id": "cmd-1", "action": "reboot"},
            last_id="cmd-1",
        )
        self.assertEqual(result["decision"], "skip")
        self.assertFalse(state.should_persist_command(result))

    def test_defer_not_before(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        later = now + timedelta(hours=2)
        result = state.evaluate_command(
            {
                "id": "cmd-2",
                "action": "reboot",
                "not_before": later.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            now=now,
        )
        self.assertEqual(result["decision"], "defer")
        self.assertFalse(state.should_persist_command(result))

    def test_not_before_reached(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        earlier = now - timedelta(minutes=1)
        result = state.evaluate_command(
            {
                "id": "cmd-3",
                "action": "reboot",
                "not_before": earlier.isoformat(),
            },
            now=now,
        )
        self.assertEqual(result["decision"], "execute")

    def test_unsupported_action_is_ignored(self):
        result = state.evaluate_command({"id": "cmd-4", "action": "wipe"})
        self.assertEqual(result["decision"], "ignore")
        self.assertEqual(result["reason"], "unsupported_action")
        self.assertTrue(state.should_persist_command(result))

    def test_missing_id(self):
        result = state.evaluate_command({"action": "reboot"})
        self.assertEqual(result["decision"], "ignore")
        self.assertEqual(result["reason"], "missing_id")
        self.assertFalse(state.should_persist_command(result))


class TestSetupEpisodes(StateTest):
    def test_reads_completed_and_open_episodes(self):
        path = os.path.join(self.tmpdir.name, state.SETUP_EPISODES_FILE)
        with open(path, "w") as f:
            f.write('{"start":"2026-10-23T18:00:00Z","end":"2026-10-23T18:12:00Z"}\n')
        with open(os.path.join(self.tmpdir.name, state.SETUP_CURRENT_FILE), "w") as f:
            f.write("2026-10-23T19:00:00Z\n")
        episodes = state.read_setup_episodes()
        self.assertEqual(episodes, [
            {"start": "2026-10-23T18:00:00Z", "end": "2026-10-23T18:12:00Z"},
            {"start": "2026-10-23T19:00:00Z", "end": None},
        ])
        state.clear_completed_setup_episodes()
        remaining = state.read_setup_episodes()
        self.assertEqual(remaining, [
            {"start": "2026-10-23T19:00:00Z", "end": None},
        ])


if __name__ == "__main__":
    unittest.main()
