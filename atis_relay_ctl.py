#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import sys

from atis_relay_status import write_status
from pi_atc_config import (
    SYSTEM_ENV_PATH,
    SYSTEM_STATE_DIR,
    apply_runtime_paths,
    env_path,
    parse_stations,
    parse_station_tokens,
    read_env_file,
    save_desktop_settings,
    write_public_config,
)

UNIT_NAME = "atis-relay.service"
SYSTEMCTL_VERBS = ("start", "stop", "restart", "enable", "disable", "is-active", "is-enabled")
PRIV_VERBS = ("start", "stop", "restart", "enable", "disable")
ALLOWED_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


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


def usage(stdout):
    print("usage: atis-relay-ctl start|stop|restart|enable|disable|is-active|is-enabled", file=stdout)
    print("       atis-relay-ctl install-config FILE", file=stdout)
    print("       atis-relay-ctl public-config", file=stdout)


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

    usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
