#!/usr/bin/env python3
import threading
import time

from atis_relay_status import write_status
from pi_atc_config import apply_user_paths, load_config


def _desktop_reboot():
    write_status(
        state="error",
        error="ATIS Relay asked this Pi to restart.",
        fix="Use the Raspberry Pi menu to restart when you are ready.",
    )


def _heartbeat_loop():
    import heartbeat

    while True:
        try:
            heartbeat.run_checkin(reboot_fn=_desktop_reboot)

        except Exception:
            pass

        time.sleep(300)


def main():
    apply_user_paths()
    load_config()
    write_status(state="waiting")
    thread = threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True)
    thread.start()
    import audio_uploader
    audio_uploader.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
