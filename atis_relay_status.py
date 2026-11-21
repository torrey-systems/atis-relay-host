#!/usr/bin/env python3
import os
from datetime import datetime, timezone

from pi_atc_config import _chown_service_user
from pi_atc_state import read_json, state_dir, write_json_atomic

STATUS_FILE = "status.json"
NOTICE_FILE = "notice.json"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

STATES = ("stopped", "waiting", "listening", "uploading", "error")


def status_path():
    return os.path.join(state_dir(), STATUS_FILE)


def notice_path():
    return os.path.join(state_dir(), NOTICE_FILE)


def read_boot_id():
    try:
        with open(BOOT_ID_PATH) as f:
            return f.read().strip()

    except OSError:
        return ""


def write_notice(message, fix="", until_reboot=False):
    payload = {
        "message": message or "",
        "fix": fix or "",
        "boot_id": read_boot_id() if until_reboot else "",
    }
    write_json_atomic(notice_path(), payload)

    try:
        os.chmod(notice_path(), 0o644)
        _chown_service_user(notice_path())

    except OSError:
        pass

    return payload


def read_notice():
    data = read_json(notice_path(), default=None)

    if not isinstance(data, dict):
        data = {}

    return {
        "message": str(data.get("message") or ""),
        "fix": str(data.get("fix") or ""),
        "boot_id": str(data.get("boot_id") or ""),
    }


def clear_notice():
    try:
        os.remove(notice_path())

    except OSError:
        pass


def clear_stale_notice():
    boot_id = read_notice()["boot_id"]

    if boot_id and boot_id == read_boot_id():
        return False

    clear_notice()
    return True


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
        "too_hot": (
            "This Pi is too hot to record.",
            "Move it somewhere cooler or add a fan. Recording starts again when it cools down.",
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
        "v4_driver": (
            "This USB radio (RTL-SDR Blog V4) needs a newer driver.",
            "Open ATIS Relay, go to step 2, and click Install driver.",
        ),
        "remote_reboot": (
            "ATIS Relay asked this Pi to restart.",
            "Use the Raspberry Pi menu to restart when you are ready. To let ATIS Relay restart it for you next time, turn on remote restarts in step 3.",
        ),
        "remote_restart_capture": (
            "ATIS Relay asked the recorder to restart.",
            "Click Stop, then Start. To let ATIS Relay do this for you next time, turn on remote restarts in step 3.",
        ),
        "remote_usb_reset": (
            "ATIS Relay asked to reset the USB radio.",
            "Unplug the USB radio, plug it back in, then click Stop and Start. To let ATIS Relay do this for you next time, turn on remote restarts in step 3.",
        ),
        "remote_reboot_failed": (
            "ATIS Relay asked this Pi to restart, but it could not restart itself.",
            "Use the Raspberry Pi menu to restart when you are ready.",
        ),
    }
    return mapping.get(kind, (
        "Something went wrong.",
        "Open ATIS Relay and try Start again. If it keeps failing, unplug the USB radio and plug it back in.",
    ))
