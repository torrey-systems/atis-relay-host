#!/usr/bin/env python3
import errno
import json
import os
import re
import subprocess
import sys

from dotenv import load_dotenv

AIRPORT_CODE_RE = re.compile(r"^[A-Z0-9]{3,4}$")
DESKTOP_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")

DEFAULT_ENV_PATH = "/etc/pi-atc/env"
SYSTEM_ENV_PATH = "/etc/atis-relay/env"
SYSTEM_STATE_DIR = "/var/lib/atis-relay"
SYSTEM_PUBLIC_PATH = "/var/lib/atis-relay/public.json"
LEGACY_ENV_PATH = "/opt/pi-atc/.env"
DEFAULT_ATIS_RELAY_URL = "https://atisrelay.com"
APP_ID = "atis-relay"
SERVICE_USER = "atis-relay"
ENV_FILE_MODE = 0o640


def xdg_dir(env_name, fallback):
    value = os.environ.get(env_name)

    if isinstance(value, str) and value.strip():
        return value.strip()

    return os.path.join(os.path.expanduser("~"), fallback)


def user_config_dir():
    return os.path.join(xdg_dir("XDG_CONFIG_HOME", ".config"), APP_ID)


def user_state_dir():
    return os.path.join(xdg_dir("XDG_DATA_HOME", ".local/share"), APP_ID)


def user_env_path():
    return os.path.join(user_config_dir(), "env")


def apply_runtime_paths():
    os.environ.setdefault("PI_ATC_ENV_FILE", SYSTEM_ENV_PATH)
    os.environ.setdefault("PI_ATC_STATE_DIR", SYSTEM_STATE_DIR)


def apply_user_paths():
    apply_runtime_paths()


SECRET_KEYS = (
    "STATION_TOKEN",
    "STATION_TOKENS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AP_PASSWORD",
)
MIGRATE_KEYS = (
    "ATIS_RELAY_URL",
    "STATION_TOKEN",
    "STATION_TOKENS",
    "STATIONS",
    "PI_ID",
    "AUDIO_DEVICE",
    "SAMPLE_RATE",
    "CHUNK_SEC",
    "SIMILARITY_THRESHOLD",
    "COSINE_THRESHOLD",
    "AP_PASSWORD",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "TEMP_SHUTDOWN_C",
    "TEMP_RESUME_C",
    "CAPTURE_KIND",
)


def env_path():
    override = os.environ.get("PI_ATC_ENV_FILE")

    if isinstance(override, str) and override.strip():
        return override.strip()

    return SYSTEM_ENV_PATH


def env_search_paths():
    return [env_path()]


def public_config_path():
    override = os.environ.get("PI_ATC_PUBLIC_FILE")

    if isinstance(override, str) and override.strip():
        return override.strip()

    return os.path.join(os.environ.get("PI_ATC_STATE_DIR", SYSTEM_STATE_DIR), "public.json")


def load_config():
    for path in env_search_paths():

        if not os.path.isfile(path):
            continue

        try:
            load_dotenv(path, override=False)

        except OSError:
            continue


def normalize_airport_code(value):
    if value is None:
        return ""

    return str(value).strip().upper()


def valid_airport_code(value):
    code = normalize_airport_code(value)

    if not code:
        return False

    return AIRPORT_CODE_RE.match(code) is not None


def parse_stations(raw):
    stations = []

    if not raw:
        return stations

    for entry in raw.split(","):
        entry = entry.strip()

        if ":" not in entry:
            continue

        icao, freq = entry.split(":", 1)
        stations.append((normalize_airport_code(icao), freq.strip()))

    return stations


def stations_from_env(environ=None):
    if environ is None:
        environ = os.environ

    return parse_stations(environ.get("STATIONS", ""))


def station_token(environ=None):
    if environ is None:
        environ = os.environ

    value = environ.get("STATION_TOKEN", "")

    if isinstance(value, str) and value.strip():
        return value.strip()

    return ""


def parse_station_tokens(raw):
    tokens = {}

    if not raw:
        return tokens

    for entry in raw.split(","):
        entry = entry.strip()

        if ":" not in entry:
            continue

        icao, token = entry.split(":", 1)
        icao = normalize_airport_code(icao)
        token = token.strip()

        if icao and token:
            tokens[icao] = token

    return tokens


def format_station_tokens(tokens):
    parts = []

    for icao in sorted(tokens):
        token = tokens[icao]

        if icao and token:
            parts.append("%s:%s" % (icao, token))

    return ",".join(parts)


def token_map_from_env(environ=None):
    if environ is None:
        environ = os.environ

    tokens = parse_station_tokens(environ.get("STATION_TOKENS", ""))
    stations = stations_from_env(environ)
    shorthand = station_token(environ)

    if shorthand and len(stations) == 1:
        icao = stations[0][0]

        if icao not in tokens:
            tokens[icao] = shorthand

    return tokens


def station_token_for(icao, environ=None):
    if not icao:
        return ""

    return token_map_from_env(environ).get(normalize_airport_code(icao), "")


def set_station_token(path, token, icao=None):
    data = read_env_file(path)
    stations = parse_stations(data.get("STATIONS", ""))
    icaos = [item[0] for item in stations]
    tokens = parse_station_tokens(data.get("STATION_TOKENS", ""))

    if icao:
        icao = normalize_airport_code(icao)

        if not icao:
            return "icao is required"

        tokens[icao] = token

    else:
        if len(icaos) > 1:
            return "more than one station configured; use: atis-relay set-key --icao %s" % icaos[0]

        if len(icaos) == 1:
            tokens[icaos[0]] = token

        else:
            data["STATION_TOKEN"] = token
            data["STATION_TOKENS"] = format_station_tokens(tokens)
            write_env_file(path, data)
            write_public_config(data)
            return None

    data["STATION_TOKENS"] = format_station_tokens(tokens)

    if len(icaos) <= 1 and len(tokens) == 1:
        data["STATION_TOKEN"] = next(iter(tokens.values()))

    else:
        data["STATION_TOKEN"] = ""

    if "ATIS_RELAY_URL" not in data:
        data["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    write_env_file(path, data)
    write_public_config(data)
    return None


def save_desktop_settings(path, stations, tokens, audio_device="", capture_kind="sdr"):
    data = read_env_file(path) if os.path.isfile(path) else {}

    if "ATIS_RELAY_URL" not in data or data.get("ATIS_RELAY_URL") == "":
        data["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    stations = [
        (normalize_airport_code(icao), freq)
        for icao, freq in stations
    ]
    data["STATIONS"] = ",".join("%s:%s" % (icao, freq) for icao, freq in stations)

    incoming = dict(tokens or {})
    existing = parse_station_tokens(data.get("STATION_TOKENS", ""))

    for icao, token in incoming.items():
        code = normalize_airport_code(icao)

        if token and code:
            existing[code] = token

    keep = {icao for icao, _freq in stations}

    for icao in list(existing):
        if keep and icao not in keep:
            del existing[icao]

    data["STATION_TOKENS"] = format_station_tokens(existing)

    if len(stations) <= 1 and len(existing) == 1:
        data["STATION_TOKEN"] = next(iter(existing.values()))

    else:
        data["STATION_TOKEN"] = ""

    if audio_device:
        data["AUDIO_DEVICE"] = audio_device

    if capture_kind:
        data["CAPTURE_KIND"] = capture_kind

    write_env_file(path, data)
    write_public_config(data)
    return None


def public_payload(data):
    stations = parse_stations(data.get("STATIONS", "") if data else "")
    tokens = {}

    for icao, _freq in stations:
        tokens[icao] = bool(station_token_for(icao, data))

    return {
        "stations": [[icao, freq] for icao, freq in stations],
        "audio_device": ((data or {}).get("AUDIO_DEVICE") or "").strip(),
        "capture_kind": ((data or {}).get("CAPTURE_KIND") or "sdr").strip() or "sdr",
        "tokens": tokens,
    }


def write_public_config(data=None):
    if data is None:
        data = read_env_file(env_path())

    payload = public_payload(data)
    path = public_config_path()
    directory = os.path.dirname(path)

    try:
        if directory:
            os.makedirs(directory, exist_ok=True)

        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
            f.write("\n")

        os.replace(tmp, path)
        os.chmod(path, 0o644)
        _chown_service_user(path)

    except OSError:
        return payload

    return payload


def read_public_config():
    path = public_config_path()

    try:
        with open(path) as f:
            data = json.load(f)

    except (OSError, json.JSONDecodeError, ValueError):
        data = None

    if not isinstance(data, dict):
        return {
            "stations": [],
            "audio_device": "",
            "capture_kind": "sdr",
            "tokens": {},
        }

    stations = data.get("stations") or []
    tokens = data.get("tokens") or {}

    if not isinstance(stations, list):
        stations = []

    if not isinstance(tokens, dict):
        tokens = {}

    return {
        "stations": stations,
        "audio_device": data.get("audio_device") or "",
        "capture_kind": data.get("capture_kind") or "sdr",
        "tokens": tokens,
    }


def atis_relay_url(environ=None):
    if environ is None:
        environ = os.environ

    value = environ.get("ATIS_RELAY_URL", "")

    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")

    return DEFAULT_ATIS_RELAY_URL


def has_legacy_aws(environ=None):
    if environ is None:
        environ = os.environ

    key_id = environ.get("AWS_ACCESS_KEY_ID", "")
    secret = environ.get("AWS_SECRET_ACCESS_KEY", "")

    return bool(isinstance(key_id, str) and key_id.strip() and isinstance(secret, str) and secret.strip())


def upload_mode(environ=None):
    if token_map_from_env(environ):
        return "http"

    if station_token(environ) and len(stations_from_env(environ)) <= 1:
        return "http"

    if has_legacy_aws(environ):
        return "s3"

    return "none"


def mask_secret(value):
    if not value:
        return "not set"

    return "set"


def read_env_file(path):
    data = {}

    try:
        with open(path) as f:
            lines = f.read().splitlines()

    except OSError:
        return data

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        if "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()

    return data


def write_env_file(path, data, mode=None):
    if mode is None:
        mode = ENV_FILE_MODE

    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    keys = list(data.keys())
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for key in keys:
            f.write("%s=%s\n" % (key, data[key]))

    os.replace(tmp, path)

    if os.path.abspath(path) == os.path.abspath(SYSTEM_ENV_PATH):
        apply_system_env_permissions(path)

    else:
        try:
            os.chmod(path, mode)

        except OSError:
            pass

        _chown_service_user(path)


def env_file_access(path=None):
    if path is None:
        path = env_path()

    try:
        with open(path):
            return "ok"

    except PermissionError:
        return "unreadable"

    except FileNotFoundError:
        return "missing"

    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "unreadable"

        if exc.errno == errno.ENOENT:
            return "missing"

        return "unreadable"


def installing_desktop_user():
    sudo_user = os.environ.get("SUDO_USER")

    if isinstance(sudo_user, str) and sudo_user.strip() and sudo_user.strip() != "root":
        name = sudo_user.strip()

        if _valid_desktop_user(name):
            return name

    raw = os.environ.get("PKEXEC_UID")

    if raw is not None and str(raw).strip() != "":
        try:
            uid = int(raw)

        except (TypeError, ValueError):
            uid = None

        if uid is not None and uid != 0:
            name = _user_name_for_uid(uid)

            if _valid_desktop_user(name):
                return name

    if os.geteuid() != 0:
        name = _user_name_for_uid(os.geteuid())

        if _valid_desktop_user(name) and name != SERVICE_USER:
            return name

    return None


def _user_name_for_uid(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name

    except (ImportError, KeyError, OSError, OverflowError):
        return None


def _valid_desktop_user(name):
    if not name or not isinstance(name, str):
        return False

    if name in ("root", "nobody", SERVICE_USER):
        return False

    if len(name) > 32:
        return False

    return DESKTOP_USER_RE.match(name) is not None


def add_user_to_service_group(user):
    if not _valid_desktop_user(user):
        return False

    if os.geteuid() != 0:
        return False

    if _user_in_service_group(user):
        return True

    for cmd in (
        ["adduser", user, SERVICE_USER],
        ["usermod", "-aG", SERVICE_USER, user],
    ):
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )

        except OSError:
            continue

        if res.returncode == 0 or _user_in_service_group(user):
            return True

    return False


def _user_in_service_group(user):
    try:
        import grp
        import pwd
        info = pwd.getpwnam(user)
        group = grp.getgrnam(SERVICE_USER)

    except (ImportError, KeyError, OSError):
        return False

    if info.pw_gid == group.gr_gid:
        return True

    return user in group.gr_mem


def apply_system_env_permissions(path=None, user=None):
    if path is None:
        path = SYSTEM_ENV_PATH

    if os.path.isfile(path):
        try:
            os.chmod(path, ENV_FILE_MODE)

        except OSError:
            pass

        _chown_env_file(path)

    directory = os.path.dirname(path)
    system_dir = os.path.dirname(SYSTEM_ENV_PATH)

    if directory and os.path.isdir(directory) and os.path.abspath(directory) == os.path.abspath(system_dir):
        try:
            os.chmod(directory, 0o750)

        except OSError:
            pass

        _chown_service_user(directory)

    if user is None:
        user = installing_desktop_user()

    add_user_to_service_group(user)


def _chown_env_file(path):
    try:
        import grp
        gid = grp.getgrnam(SERVICE_USER).gr_gid
        os.chown(path, 0, gid)

    except (ImportError, KeyError, OSError, PermissionError):
        return False

    return True


def _chown_service_user(path):
    try:
        import pwd
        pw = pwd.getpwnam(SERVICE_USER)
        os.chown(path, pw.pw_uid, pw.pw_gid)

    except (ImportError, KeyError, OSError, PermissionError):
        return False

    return True


def set_env_value(path, key, value):
    data = read_env_file(path)

    if "ATIS_RELAY_URL" not in data:
        data["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    data[key] = value
    write_env_file(path, data)


def migrate_legacy_env(legacy_path=None, dest_path=None):
    if legacy_path is None:
        legacy_path = LEGACY_ENV_PATH

    if dest_path is None:
        dest_path = env_path()

    if not os.path.isfile(legacy_path):
        return 0

    src = read_env_file(legacy_path)
    dest = read_env_file(dest_path) if os.path.isfile(dest_path) else {}
    copied = 0

    for key in MIGRATE_KEYS:
        incoming = src.get(key)

        if incoming is None or incoming == "":
            continue

        current = dest.get(key)

        if current is None or current == "":
            dest[key] = incoming
            copied += 1

    if "ATIS_RELAY_URL" not in dest or dest.get("ATIS_RELAY_URL") == "":
        dest["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    if "STATION_TOKEN" not in dest:
        dest["STATION_TOKEN"] = ""

    if "STATION_TOKENS" not in dest:
        dest["STATION_TOKENS"] = ""

    if copied or not os.path.isfile(dest_path):
        write_env_file(dest_path, dest)
        write_public_config(dest)

    return copied


def candidate_legacy_env_paths():
    paths = []
    home_root = "/home"

    try:
        names = os.listdir(home_root)

    except OSError:
        names = []

    for name in names:
        if not name or name.startswith("."):
            continue

        candidate = os.path.join(home_root, name, ".config", APP_ID, "env")

        if os.path.isfile(candidate):
            paths.append(candidate)

    for item in (DEFAULT_ENV_PATH, LEGACY_ENV_PATH):
        if os.path.isfile(item) and item not in paths:
            paths.append(item)

    return paths


def migrate_to_system_env(dest_path=None, extra_sources=None):
    if dest_path is None:
        dest_path = SYSTEM_ENV_PATH

    sources = []

    if extra_sources is None:
        sources.extend(candidate_legacy_env_paths())

    else:
        sources.extend(extra_sources)

    dest = read_env_file(dest_path) if os.path.isfile(dest_path) else {}
    copied = 0

    for src_path in sources:
        if not src_path or src_path == dest_path:
            continue

        if not os.path.isfile(src_path):
            continue

        copied += _copy_unset_keys(src_path, dest)

    if "ATIS_RELAY_URL" not in dest or dest.get("ATIS_RELAY_URL") == "":
        dest["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    if "STATION_TOKEN" not in dest:
        dest["STATION_TOKEN"] = ""

    if "STATION_TOKENS" not in dest:
        dest["STATION_TOKENS"] = ""

    if copied or not os.path.isfile(dest_path):
        write_env_file(dest_path, dest)

    write_public_config(dest)
    return copied


def _copy_unset_keys(src_path, dest):
    src = read_env_file(src_path)
    copied = 0

    for key in MIGRATE_KEYS:
        incoming = src.get(key)

        if incoming is None or incoming == "":
            continue

        current = dest.get(key)

        if current is None or current == "":
            dest[key] = incoming
            copied += 1

    return copied


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "migrate":
        n = migrate_to_system_env()
        print("[migrate] copied %d unset keys from a previous config" % n)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
