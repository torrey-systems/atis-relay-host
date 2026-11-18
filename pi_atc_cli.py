#!/usr/bin/env python3
import getpass
import os
import subprocess
import sys

from pi_atc_config import (
    atis_relay_url,
    env_path,
    load_config,
    mask_secret,
    read_env_file,
    set_env_value,
    station_token,
    stations_from_env,
    upload_mode,
)
from pi_atc_state import read_last_heartbeat, read_last_upload, read_last_uploads, read_mode

SERVICES_TO_RESTART = (
    "pi-atc.service",
    "pi-atc-heartbeat.service",
)


def default_restart(names=SERVICES_TO_RESTART):
    for name in names:
        subprocess.run(
            ["systemctl", "restart", name],
            capture_output=True,
            check=False,
        )


def read_token_arg(argv, stdin):
    if len(argv) >= 2 and argv[1]:
        token = argv[1]

        if len(sys.argv) > 2:
            sys.argv[2] = "***"

        return token.strip()

    if stdin is None:
        stdin = sys.stdin

    if stdin.isatty():
        token = getpass.getpass("Station key: ")
        return token.strip()

    token = stdin.readline()
    return token.strip()


def cmd_set_key(argv, stdin=None, stdout=None, restart_fn=None, path=None):
    if stdout is None:
        stdout = sys.stdout

    if restart_fn is None:
        restart_fn = default_restart

    if path is None:
        path = env_path()

    if os.geteuid() != 0:
        print("pi-atc set-key must be run as root", file=stdout)
        return 1

    token = read_token_arg(argv, stdin)

    if not token:
        print("station key is required", file=stdout)
        return 1

    set_env_value(path, "STATION_TOKEN", token)
    restart_fn()
    print("station key saved; services restarted", file=stdout)
    return 0


def cmd_status(stdout=None, environ=None):
    if stdout is None:
        stdout = sys.stdout

    if environ is None:
        environ = os.environ

    load_config()
    token = station_token(environ)
    stations = stations_from_env(environ)
    mode = upload_mode(environ)
    last = read_last_upload()
    uploads = read_last_uploads()
    heartbeat = read_last_heartbeat()
    device_mode = read_mode() or "unknown"
    print("token: %s" % mask_secret(token), file=stdout)
    print("relay url: %s" % atis_relay_url(environ), file=stdout)
    print("upload mode: %s" % ("http" if mode == "http" else ("legacy s3 (deprecated)" if mode == "s3" else "none")), file=stdout)

    if stations:
        print("stations: %s" % ", ".join("%s:%s" % item for item in stations), file=stdout)

    else:
        print("stations: not set", file=stdout)

    print("device mode: %s" % device_mode, file=stdout)

    if heartbeat and heartbeat.get("ts"):
        station = heartbeat.get("station") or ""
        extra = " %s" % station if station else ""
        print("last heartbeat:%s %s" % (extra, heartbeat["ts"]), file=stdout)

    else:
        print("last heartbeat: none", file=stdout)

    if last:
        print("last upload: %s %s %s" % (last.get("station") or "-", last.get("key") or "-", last.get("ts") or "-"), file=stdout)

    else:
        print("last upload: none", file=stdout)

    if uploads:
        for icao in sorted(uploads):
            item = uploads[icao]
            print("  %s: %s %s" % (icao, item.get("key") or "-", item.get("ts") or "-"), file=stdout)

    return 0


def usage(stdout):
    print("usage: pi-atc set-key [token]", file=stdout)
    print("       pi-atc status", file=stdout)


def main(argv=None, stdin=None, stdout=None, restart_fn=None, env_file=None):
    if argv is None:
        argv = sys.argv[1:]

    if stdout is None:
        stdout = sys.stdout

    if not argv:
        usage(stdout)
        return 1

    cmd = argv[0]

    if cmd in ("set-key", "set_key"):
        return cmd_set_key(argv, stdin=stdin, stdout=stdout, restart_fn=restart_fn, path=env_file)

    if cmd == "status":
        return cmd_status(stdout=stdout)

    usage(stdout)
    return 1


if __name__ == "__main__":
    sys.exit(main())
