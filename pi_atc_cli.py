#!/usr/bin/env python3
import getpass
import os
import sys

from pi_atc_config import (
    apply_runtime_paths,
    atis_relay_url,
    env_file_access,
    env_path,
    load_config,
    mask_secret,
    normalize_airport_code,
    set_station_token,
    station_token,
    station_token_for,
    stations_from_env,
    token_map_from_env,
    upload_mode,
)
from pi_atc_state import read_last_heartbeat, read_last_upload, read_last_uploads, read_mode

def default_restart(names=None):
    from atis_relay_service import restart_worker

    restart_worker()


def parse_set_key_args(argv):
    icao = None
    token = None
    args = list(argv[1:])
    i = 0

    while i < len(args):
        arg = args[i]

        if arg in ("--icao", "-i"):
            if i + 1 >= len(args):
                return None, None, "missing icao after --icao"

            icao = normalize_airport_code(args[i + 1])
            i += 2
            continue

        if arg.startswith("--icao="):
            icao = normalize_airport_code(arg.split("=", 1)[1])
            i += 1
            continue

        if token is None:
            token = arg

        i += 1

    return icao, token, None


def scrub_token(token):
    if not token:
        return

    for i, arg in enumerate(sys.argv):
        if arg == token:
            sys.argv[i] = "***"


def read_token_value(parsed_token, stdin):
    if parsed_token:
        return parsed_token.strip()

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

    icao, parsed_token, parse_error = parse_set_key_args(argv)

    if parse_error:
        print(parse_error, file=stdout)
        return 1

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    token = read_token_value(parsed_token, stdin)
    scrub_token(token)

    if not token:
        print("station key is required", file=stdout)
        return 1

    error = set_station_token(path, token, icao=icao)

    if error:
        print(error, file=stdout)
        return 1

    restart_fn()
    print("station key saved", file=stdout)
    return 0


def cmd_status(stdout=None, environ=None):
    if stdout is None:
        stdout = sys.stdout

    if environ is None:
        environ = os.environ

    load_config()
    last = read_last_upload()
    uploads = read_last_uploads()
    heartbeat = read_last_heartbeat()
    device_mode = read_mode() or "unknown"
    access = env_file_access(env_path())

    if access == "unreadable":
        print("can't read config (permission)", file=stdout)
        print("relay url: %s" % atis_relay_url(environ), file=stdout)
        print("device mode: %s" % device_mode, file=stdout)
        _print_runtime_status(stdout, heartbeat, last, uploads)
        return 0

    tokens = token_map_from_env(environ)
    shorthand = station_token(environ)
    stations = stations_from_env(environ)
    mode = upload_mode(environ)
    print("token: %s" % ("set" if (tokens or shorthand) else "not set"), file=stdout)
    print("relay url: %s" % atis_relay_url(environ), file=stdout)
    print("upload mode: %s" % ("http" if mode == "http" else ("legacy s3 (deprecated)" if mode == "s3" else "none")), file=stdout)

    if stations:
        print("stations: %s" % ", ".join("%s:%s" % item for item in stations), file=stdout)

        for icao, freq in stations:
            print("  %s:%s token: %s" % (icao, freq, mask_secret(station_token_for(icao, environ))), file=stdout)

    else:
        print("stations: not set", file=stdout)

    print("device mode: %s" % device_mode, file=stdout)
    _print_runtime_status(stdout, heartbeat, last, uploads)
    return 0


def _print_runtime_status(stdout, heartbeat, last, uploads):
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


def usage(stdout):
    print("usage: atis-relay", file=stdout)
    print("       atis-relay set-key [--icao CODE] [token]", file=stdout)
    print("       atis-relay status", file=stdout)


def main(argv=None, stdin=None, stdout=None, restart_fn=None, env_file=None):
    apply_runtime_paths()

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
