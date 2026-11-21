#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile

from pi_atc_config import REMOTE_ACTIONS, env_path, save_desktop_settings, write_public_config

UNIT_NAME = "atis-relay.service"
REMOTE_UNIT = "atis-relay-remote@%s.service"
CTL_PATH = "/usr/lib/atis-relay/atis-relay-ctl"
PRIV_ACTIONS = (
    "start",
    "stop",
    "restart",
    "enable",
    "disable",
    "install-config",
    "remote-commands",
    "install-v4-driver",
)
V4_DRIVER_TIMEOUT_SEC = 1800


def ctl_command():
    if os.path.isfile(CTL_PATH) and os.access(CTL_PATH, os.X_OK):
        return [CTL_PATH]

    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "atis_relay_ctl.py")]


def run_ctl(args, timeout=30):
    cmd = ctl_command() + list(args)
    action = args[0] if args else ""

    if action in PRIV_ACTIONS and os.geteuid() != 0:
        cmd = ["pkexec"] + cmd

    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    except (OSError, subprocess.TimeoutExpired):
        return None


def service_enabled_state():
    res = run_ctl(["is-enabled"])

    if res is None:
        return None

    text = (res.stdout or "").strip()

    if res.returncode == 0 or text == "enabled":
        return True

    if text in ("disabled", "masked"):
        return False

    return None


def is_enabled():
    return service_enabled_state() is True


def is_autostart_enabled():
    return is_enabled()


def is_worker_active():
    res = run_ctl(["is-active"])

    if res is None:
        return False

    return res.returncode == 0 and (res.stdout or "").strip() == "active"


def enable_service():
    res = run_ctl(["enable"])
    return res is not None and res.returncode == 0


def disable_service():
    res = run_ctl(["disable"])
    return res is not None and res.returncode == 0


def enable_autostart():
    return enable_service()


def disable_autostart():
    return disable_service()


def start_worker():
    res = run_ctl(["start"])
    return res is not None and res.returncode == 0


def stop_worker():
    res = run_ctl(["stop"])
    return res is not None and res.returncode == 0


def restart_worker():
    res = run_ctl(["restart"])
    return res is not None and res.returncode == 0


def set_remote_commands(enabled):
    res = run_ctl(["remote-commands", "on" if enabled else "off"])
    return res is not None and res.returncode == 0


def install_v4_driver():
    res = run_ctl(["install-v4-driver"], timeout=V4_DRIVER_TIMEOUT_SEC)
    return res is not None and res.returncode == 0


def run_remote_command(action, timeout=60):
    if action not in REMOTE_ACTIONS:
        return False

    try:
        res = subprocess.run(
            ["systemctl", "--no-ask-password", "start", REMOTE_UNIT % action],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    except (OSError, subprocess.TimeoutExpired):
        return False

    return res.returncode == 0


def install_settings(stations, tokens, audio_device="", capture_kind="sdr"):
    dest = env_path()
    directory = os.path.dirname(dest) or "."

    if os.path.isfile(dest):
        can_write = os.access(dest, os.W_OK)

    else:
        can_write = os.access(directory, os.W_OK | os.X_OK)

    if can_write:
        save_desktop_settings(
            dest,
            stations,
            tokens,
            audio_device=audio_device,
            capture_kind=capture_kind,
        )
        write_public_config()
        return True

    handle, tmp = tempfile.mkstemp(prefix="atis-relay-", suffix=".env", text=True)

    try:
        os.close(handle)
        save_desktop_settings(
            tmp,
            stations,
            tokens,
            audio_device=audio_device,
            capture_kind=capture_kind,
        )
        os.chmod(tmp, 0o600)
        res = run_ctl(["install-config", tmp])
        return res is not None and res.returncode == 0

    finally:
        try:
            os.remove(tmp)

        except OSError:
            pass
