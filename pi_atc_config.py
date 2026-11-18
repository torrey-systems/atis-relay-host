#!/usr/bin/env python3
import os
import stat
import sys

from dotenv import load_dotenv

DEFAULT_ENV_PATH = "/etc/pi-atc/env"
LEGACY_ENV_PATH = "/opt/pi-atc/.env"
DEFAULT_ATIS_RELAY_URL = "https://atisrelay.com"
APP_ID = "atis-relay"


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


def apply_user_paths():
    os.environ.setdefault("PI_ATC_ENV_FILE", user_env_path())
    os.environ.setdefault("PI_ATC_STATE_DIR", user_state_dir())
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
)


def env_path():
    override = os.environ.get("PI_ATC_ENV_FILE")

    if isinstance(override, str) and override.strip():
        return override.strip()

    user_path = user_env_path()

    if os.path.isfile(user_path):
        return user_path

    if os.path.isfile(DEFAULT_ENV_PATH) and os.access(DEFAULT_ENV_PATH, os.R_OK) and os.geteuid() == 0:
        return DEFAULT_ENV_PATH

    return user_path


def env_search_paths():
    paths = []
    primary = env_path()
    paths.append(primary)
    user_path = user_env_path()

    if user_path not in paths:
        paths.append(user_path)

    if LEGACY_ENV_PATH not in paths:
        paths.append(LEGACY_ENV_PATH)

    return paths


def load_config():
    for path in env_search_paths():

        if os.path.isfile(path):
            load_dotenv(path, override=False)


def parse_stations(raw):
    stations = []

    if not raw:
        return stations

    for entry in raw.split(","):
        entry = entry.strip()

        if ":" not in entry:
            continue

        icao, freq = entry.split(":", 1)
        stations.append((icao.strip().upper(), freq.strip()))

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
        icao = icao.strip().upper()
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

    return token_map_from_env(environ).get(str(icao).strip().upper(), "")


def set_station_token(path, token, icao=None):
    data = read_env_file(path)
    stations = parse_stations(data.get("STATIONS", ""))
    icaos = [item[0] for item in stations]
    tokens = parse_station_tokens(data.get("STATION_TOKENS", ""))

    if icao:
        icao = icao.strip().upper()

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
            return None

    data["STATION_TOKENS"] = format_station_tokens(tokens)

    if len(icaos) <= 1 and len(tokens) == 1:
        data["STATION_TOKEN"] = next(iter(tokens.values()))

    else:
        data["STATION_TOKEN"] = ""

    if "ATIS_RELAY_URL" not in data:
        data["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    write_env_file(path, data)
    return None


def save_desktop_settings(path, stations, tokens, audio_device="", capture_kind="sdr"):
    data = read_env_file(path) if os.path.isfile(path) else {}

    if "ATIS_RELAY_URL" not in data or data.get("ATIS_RELAY_URL") == "":
        data["ATIS_RELAY_URL"] = DEFAULT_ATIS_RELAY_URL

    data["STATIONS"] = ",".join("%s:%s" % (icao, freq) for icao, freq in stations)
    incoming = dict(tokens or {})
    existing = parse_station_tokens(data.get("STATION_TOKENS", ""))

    for icao, token in incoming.items():
        if token:
            existing[icao] = token

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
    return None


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


def write_env_file(path, data, mode=0o600):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    keys = list(data.keys())
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for key in keys:
            f.write("%s=%s\n" % (key, data[key]))

    os.replace(tmp, path)
    os.chmod(path, mode)

    try:
        os.chown(path, 0, 0)

    except OSError:
        pass

    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


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

    return copied


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "migrate":
        n = migrate_legacy_env()
        print("[migrate] copied %d unset keys from legacy env" % n)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
