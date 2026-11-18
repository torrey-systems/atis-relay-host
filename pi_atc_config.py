#!/usr/bin/env python3
import os
import stat
import sys

from dotenv import load_dotenv

DEFAULT_ENV_PATH = "/etc/pi-atc/env"
LEGACY_ENV_PATH = "/opt/pi-atc/.env"
DEFAULT_ATIS_RELAY_URL = "https://atisrelay.com"
SECRET_KEYS = (
    "STATION_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AP_PASSWORD",
)
MIGRATE_KEYS = (
    "ATIS_RELAY_URL",
    "STATION_TOKEN",
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

    return DEFAULT_ENV_PATH


def env_search_paths():
    paths = []
    primary = env_path()
    paths.append(primary)

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
    if station_token(environ):
        return "http"

    if has_legacy_aws(environ):
        return "s3"

    return "none"


def mask_secret(value):
    if not value:
        return "not set"

    text = str(value)

    if len(text) <= 4:
        return "set (****)"

    return "set (****%s)" % text[-4:]


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
