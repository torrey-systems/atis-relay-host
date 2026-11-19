#!/usr/bin/env python3
import os
from datetime import datetime, timezone

from pi_atc_config import _chown_service_user
from pi_atc_state import read_json, state_dir, write_json_atomic

STATUS_FILE = "status.json"

STATES = ("stopped", "waiting", "listening", "uploading", "error")


def status_path():
    return os.path.join(state_dir(), STATUS_FILE)


def format_local_time(value):
    if not value:
        return ""

    if isinstance(value, datetime):
        dt = value

    else:
        text = str(value).strip()

        if not text:
            return ""

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(text)

        except ValueError:
            return ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    local = dt.astimezone()
    hour = local.strftime("%I").lstrip("0") or "0"
    return "%s:%s %s" % (hour, local.strftime("%M"), local.strftime("%p"))


def write_status(state="stopped", error="", fix="", last_upload=None, extra=None):
    if state not in STATES:
        state = "error"

    current = read_status()

    if last_upload is None:
        last_upload = current.get("last_upload") or ""

    payload = {
        "state": state,
        "error": error or "",
        "fix": fix or "",
        "last_upload": last_upload or "",
    }

    if extra:
        payload.update(extra)

    write_json_atomic(status_path(), payload)

    try:
        os.chmod(status_path(), 0o644)
        _chown_service_user(status_path())

    except OSError:
        pass

    return payload


def read_status():
    data = read_json(status_path(), default=None)

    if not isinstance(data, dict):
        return {
            "state": "stopped",
            "error": "",
            "fix": "",
            "last_upload": "",
        }

    return data


def status_label(state):
    labels = {
        "stopped": "Stopped",
        "waiting": "Waiting for setup",
        "listening": "Listening",
        "uploading": "Uploading",
        "error": "Needs attention",
    }
    return labels.get(state, "Needs attention")


def start_stop_enabled(state, worker_active=False):
    running = bool(worker_active) or state in ("listening", "uploading", "waiting")
    return (not running, running)


def suggestion_for_error(kind):
    mapping = {
        "missing_key": (
            "The station key is missing.",
            "Open ATIS Relay and paste the station key ATIS Relay sent you.",
        ),
        "bad_key": (
            "The station key was not accepted.",
            "Paste the station key ATIS Relay sent you for this airport.",
        ),
        "wrong_airport": (
            "This key belongs to a different airport.",
            "Paste the station key ATIS Relay sent you for this airport.",
        ),
        "no_radio": (
            "No radio dongle was found.",
            "Plug in the USB radio stick, wait a few seconds, then click Test.",
        ),
        "no_microphone": (
            "No USB audio device was found.",
            "Plug in the USB audio adapter, then click Test.",
        ),
        "capture_failed": (
            "Could not hear the radio.",
            "Check the antenna, the USB plug, and the frequency.",
        ),
        "upload_failed": (
            "Could not send audio right now.",
            "Check that the Pi is online, then wait. It will try again.",
        ),
        "too_large": (
            "The recording was too large to send.",
            "Leave the app running. The next clip should be smaller.",
        ),
        "rate_limited": (
            "Too many clips were sent in a short time.",
            "Wait a minute. The app will keep listening.",
        ),
    }
    return mapping.get(kind, (
        "Something went wrong.",
        "Open ATIS Relay and try Start again. If it keeps failing, unplug the USB radio and plug it back in.",
    ))
