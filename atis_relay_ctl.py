#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import sys

from atis_relay_devices import reset_rtl_sdr
from atis_relay_status import write_status
from pi_atc_config import (
    REMOTE_ACTIONS,
    SYSTEM_ENV_PATH,
    SYSTEM_STATE_DIR,
    apply_runtime_paths,
    env_path,
    parse_stations,
    parse_station_tokens,
    read_env_file,
    remote_commands_enabled,
    save_desktop_settings,
    set_env_value,
    write_public_config,
)

UNIT_NAME = "atis-relay.service"
SYSTEMCTL_VERBS = ("start", "stop", "restart", "enable", "disable", "is-active", "is-enabled")
PRIV_VERBS = ("start", "stop", "restart", "enable", "disable")
ALLOWED_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
REMOTE_RESTART_DELAY_SEC = 15
V4_DRIVER_INSTALLER = "/usr/lib/atis-relay/install-v4-driver"


def _as_root():
    return os.geteuid() == 0


def _require_root():
    if not _as_root():
        print("root is required", file=sys.stderr)
        return False

    return True


def _bind_system_paths():
    if os.environ.get("PKEXEC_UID"):
        os.environ["PI_ATC_ENV_FILE"] = SYSTEM_ENV_PATH
        os.environ["PI_ATC_STATE_DIR"] = SYSTEM_STATE_DIR

    apply_runtime_paths()


def run_systemctl(verb):
    if verb not in SYSTEMCTL_VERBS:
        return subprocess.CompletedProcess(["systemctl", verb], 2, "", "unknown action\n")

    try:
        return subprocess.run(
            ["systemctl", "--no-ask-password", verb, UNIT_NAME],
            capture_output=True,
            text=True,
            check=False,
        )

    except OSError as exc:
        return subprocess.CompletedProcess(["systemctl", verb], 1, "", str(exc) + "\n")


def cmd_systemctl(verb):
    if verb in PRIV_VERBS and not _require_root():
        return 1

    res = run_systemctl(verb)

    if verb == "stop" and _as_root():
        write_status(state="stopped")

    if res.stdout:
        sys.stdout.write(res.stdout)

    if res.returncode != 0 and res.stderr:
        sys.stderr.write(res.stderr)

    return res.returncode


def _caller_uid():
    raw = os.environ.get("PKEXEC_UID")

    if raw is None or raw == "":
        return None

    try:
        return int(raw)

    except ValueError:
        return None


def _validate_install_source(path):
    if not path or path.startswith("-"):
        return "invalid source"

    try:
        st = os.lstat(path)

    except OSError:
        return "source not found"

    if stat.S_ISLNK(st.st_mode):
        return "source must be a regular file"

    if not stat.S_ISREG(st.st_mode):
        return "source must be a regular file"

    if st.st_mode & stat.S_IWOTH:
        return "source is world-writable"

    caller = _caller_uid()

    if caller is not None and st.st_uid not in (caller, 0):
        return "source not owned by caller"

    return None


def cmd_install_config(src):
    if not _require_root():
        return 1

    error = _validate_install_source(src)

    if error:
        print(error, file=sys.stderr)
        return 1

    incoming = read_env_file(src)
    stations = parse_stations(incoming.get("STATIONS", ""))

    if not stations:
        print("stations are required", file=sys.stderr)
        return 1

    tokens = parse_station_tokens(incoming.get("STATION_TOKENS", ""))
    shorthand = (incoming.get("STATION_TOKEN") or "").strip()

    if shorthand and len(stations) == 1 and stations[0][0] not in tokens:
        tokens[stations[0][0]] = shorthand

    for key in incoming:
        if not ALLOWED_ENV_KEY.match(key):
            print("invalid config key", file=sys.stderr)
            return 1

    dest = env_path()
    save_desktop_settings(
        dest,
        stations,
        tokens,
        audio_device=(incoming.get("AUDIO_DEVICE") or "").strip(),
        capture_kind=(incoming.get("CAPTURE_KIND") or "sdr").strip() or "sdr",
    )
    write_public_config(read_env_file(dest))
    return 0


def cmd_public_config():
    if _as_root():
        write_public_config()

    from pi_atc_config import public_config_path
    path = public_config_path()

    try:
        with open(path) as f:
            sys.stdout.write(f.read())

    except OSError:
        print("{}", file=sys.stdout)

    return 0


def cmd_remote_commands(value):
    if value not in ("on", "off"):
        print("remote-commands requires on or off", file=sys.stderr)
        return 2

    if not _require_root():
        return 1

    dest = env_path()
    set_env_value(dest, "REMOTE_COMMANDS", "1" if value == "on" else "0")
    write_public_config(read_env_file(dest))
    return 0


def _trusted_env_file(path):
    try:
        st = os.lstat(path)

    except OSError:
        return False

    if not stat.S_ISREG(st.st_mode):
        return False

    if st.st_uid != 0:
        return False

    return not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _remote_commands_allowed():
    path = env_path()

    if not _trusted_env_file(path):
        return False

    return remote_commands_enabled(read_env_file(path))


def _run_quiet(args):
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=False)

    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if res.returncode != 0 and res.stderr:
        sys.stderr.write(res.stderr)

    return res.returncode


def cmd_remote(action):
    if action not in REMOTE_ACTIONS:
        print("unknown remote command", file=sys.stderr)
        return 2

    if not _require_root():
        return 1

    if not _remote_commands_allowed():
        print("remote commands are turned off", file=sys.stderr)
        return 1

    if action == "reboot":
        return _run_quiet(["systemctl", "--no-block", "reboot"])

    if action == "restart_capture":
        return _run_quiet([
            "systemd-run",
            "--quiet",
            "--collect",
            "--on-active=%d" % REMOTE_RESTART_DELAY_SEC,
            "--timer-property=AccuracySec=1s",
            "systemctl",
            "restart",
            UNIT_NAME,
        ])

    if reset_rtl_sdr():
        return 0

    print("no USB radio could be reset", file=sys.stderr)
    return 1


def cmd_install_v4_driver():
    if not _require_root():
        return 1

    try:
        res = subprocess.run([V4_DRIVER_INSTALLER], check=False)

    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return res.returncode


def usage(stdout):
    print("usage: atis-relay-ctl start|stop|restart|enable|disable|is-active|is-enabled", file=stdout)
    print("       atis-relay-ctl install-config FILE", file=stdout)
    print("       atis-relay-ctl public-config", file=stdout)
    print("       atis-relay-ctl remote-commands on|off", file=stdout)
    print("       atis-relay-ctl remote reboot|restart_capture|usb_reset", file=stdout)
    print("       atis-relay-ctl install-v4-driver", file=stdout)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    _bind_system_paths()

    if not argv:
        usage(sys.stderr)
        return 2

    action = argv[0]

    if action in SYSTEMCTL_VERBS:
        return cmd_systemctl(action)

    if action == "install-config":
        if len(argv) < 2:
            print("install-config requires a file", file=sys.stderr)
            return 2

        return cmd_install_config(argv[1])

    if action == "public-config":
        return cmd_public_config()

    if action == "remote-commands":
        if len(argv) < 2:
            print("remote-commands requires on or off", file=sys.stderr)
            return 2

        return cmd_remote_commands(argv[1])

    if action == "remote":
        if len(argv) < 2:
            print("remote requires a command", file=sys.stderr)
            return 2

        return cmd_remote(argv[1])

    if action == "install-v4-driver":
        return cmd_install_v4_driver()

    usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
