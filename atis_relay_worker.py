#!/usr/bin/env python3
import os
import threading
import time

from atis_relay_service import UNIT_NAME, run_remote_command
from atis_relay_status import clear_stale_notice, suggestion_for_error, write_notice, write_status
from pi_atc_config import apply_runtime_paths, load_config, remote_commands_enabled


def _remote_permitted(action):
    if remote_commands_enabled():
        return True

    message, fix = suggestion_for_error("remote_%s" % action)
    write_notice(message, fix, until_reboot=action == "reboot")
    return False


def _desktop_reboot():
    if not _remote_permitted("reboot"):
        return

    if not run_remote_command("reboot"):
        message, fix = suggestion_for_error("remote_reboot_failed")
        write_notice(message, fix, until_reboot=True)


def _desktop_restart_capture():
    return run_remote_command("restart_capture")


def _desktop_usb_reset():
    return run_remote_command("usb_reset")


def _heartbeat_loop():
    import heartbeat

    while True:
        try:
            heartbeat.run_checkin(
                reboot_fn=_desktop_reboot,
                permit_fn=_remote_permitted,
                restart_fn=_desktop_restart_capture,
                usb_reset_fn=_desktop_usb_reset,
            )

        except Exception:
            pass

        time.sleep(300)


def main():
    apply_runtime_paths()
    os.environ.setdefault("PI_ATC_CAPTURE_UNIT", UNIT_NAME)
    load_config()
    clear_stale_notice()
    write_status(state="waiting")
    thread = threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True)
    thread.start()
    import audio_uploader
    audio_uploader.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
