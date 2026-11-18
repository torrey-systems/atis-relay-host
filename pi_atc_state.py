#!/usr/bin/env python3
import json
import os
import socket
from datetime import datetime, timezone

LAST_UPLOAD_FILE = "last_upload.json"
LAST_COMMAND_FILE = "last_command.json"
LAST_HEARTBEAT_FILE = "last_heartbeat.json"
SETUP_CURRENT_FILE = "setup_current"
SETUP_EPISODES_FILE = "setup_episodes.jsonl"
MODE_FILE = "mode"
ALLOWED_ACTIONS = ("reboot",)
DEFAULT_PI_ID = "pi-atc"


def state_dir():
    override = os.environ.get("PI_ATC_STATE_DIR")

    if isinstance(override, str) and override.strip():
        return override.strip()

    from pi_atc_config import user_state_dir

    return user_state_dir()


def utc_now_iso(now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_pi_id():
    override = os.environ.get("PI_ID")

    if isinstance(override, str) and override.strip():
        return override.strip()

    try:
        hostname = socket.gethostname().strip()

        if hostname:
            return hostname

    except OSError:
        pass

    try:
        with open("/etc/hostname") as f:
            hostname = f.read().strip()

        if hostname:
            return hostname

    except OSError:
        pass

    return DEFAULT_PI_ID


def state_path(name):
    return os.path.join(state_dir(), name)


def ensure_state_dir():
    os.makedirs(state_dir(), exist_ok=True)


def write_json_atomic(path, data):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.write("\n")

    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)

    except (OSError, json.JSONDecodeError):
        return default


def upload_entry(station, key, ts):
    return {
        "station": station,
        "key": key,
        "ts": ts,
    }


def normalize_upload_entry(data):
    if not isinstance(data, dict):
        return None

    station = data.get("station")
    key = data.get("key")
    ts = data.get("ts") or data.get("timestamp")

    if not station and not key and not ts:
        return None

    return upload_entry(station, key, ts)


def read_upload_state():
    data = read_json(state_path(LAST_UPLOAD_FILE))

    if not isinstance(data, dict):
        return None, {}

    if "stations" in data or "last" in data:
        last = normalize_upload_entry(data.get("last"))
        raw_stations = data.get("stations")
        stations = {}

        if isinstance(raw_stations, dict):
            for icao, item in raw_stations.items():
                entry = normalize_upload_entry(item)

                if entry:
                    stations[str(icao)] = entry

        return last, stations

    entry = normalize_upload_entry(data)

    if entry is None:
        return None, {}

    station = entry.get("station")
    stations = {station: entry} if station else {}
    return entry, stations


def record_last_upload(station, key, now=None):
    ensure_state_dir()
    entry = upload_entry(station, key, utc_now_iso(now))
    _, stations = read_upload_state()
    stations[station] = entry
    write_json_atomic(state_path(LAST_UPLOAD_FILE), {
        "last": entry,
        "stations": stations,
    })


def read_last_upload():
    last, _ = read_upload_state()
    return last


def read_last_uploads():
    _, stations = read_upload_state()
    return stations


def record_last_heartbeat(now=None, icao=None):
    ensure_state_dir()
    entry = {"ts": utc_now_iso(now)}

    if icao:
        entry["station"] = icao

    data = read_json(state_path(LAST_HEARTBEAT_FILE), default={})

    if not isinstance(data, dict):
        data = {}

    stations = data.get("stations")

    if not isinstance(stations, dict):
        stations = {}

    if icao:
        stations[icao] = entry

    data["last"] = entry
    data["stations"] = stations
    write_json_atomic(state_path(LAST_HEARTBEAT_FILE), data)


def read_last_heartbeat():
    data = read_json(state_path(LAST_HEARTBEAT_FILE))

    if not isinstance(data, dict):
        return None

    last = data.get("last")

    if isinstance(last, dict):
        return last

    return None


def write_last_command(payload):
    ensure_state_dir()
    write_json_atomic(state_path(LAST_COMMAND_FILE), payload)


def read_last_command():
    data = read_json(state_path(LAST_COMMAND_FILE))

    if not isinstance(data, dict):
        return None

    return data


def read_mode():
    try:
        with open(state_path(MODE_FILE)) as f:
            mode = f.read().strip()

    except OSError:
        return None

    if mode in ("online", "setup"):
        return mode

    return None


def parse_iso8601(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty")

    text = value.strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def evaluate_command(command, last_id=None, now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if not isinstance(command, dict):
        return {"decision": "ignore", "reason": "invalid"}

    cmd_id = command.get("id")
    action = command.get("action")

    if not isinstance(cmd_id, str) or not cmd_id.strip():
        return {"decision": "ignore", "reason": "missing_id"}

    cmd_id = cmd_id.strip()

    if last_id is not None and cmd_id == last_id:
        return {
            "decision": "skip",
            "reason": "already_handled",
            "id": cmd_id,
            "action": action,
        }

    not_before_raw = command.get("not_before")
    not_before = None

    if not_before_raw:
        try:
            not_before = parse_iso8601(not_before_raw)

        except (ValueError, TypeError):
            return {
                "decision": "ignore",
                "reason": "bad_not_before",
                "id": cmd_id,
                "action": action,
            }

        if now < not_before:
            return {
                "decision": "defer",
                "reason": "not_before",
                "id": cmd_id,
                "action": action,
                "not_before": utc_now_iso(not_before),
            }

    if action not in ALLOWED_ACTIONS:
        return {
            "decision": "ignore",
            "reason": "unsupported_action",
            "id": cmd_id,
            "action": action,
        }

    return {
        "decision": "execute",
        "reason": "ok",
        "id": cmd_id,
        "action": action,
        "requested_by": command.get("requested_by"),
        "not_before": utc_now_iso(not_before) if not_before else None,
    }


def should_persist_command(result):
    if not isinstance(result, dict):
        return False

    if result.get("decision") not in ("execute", "ignore"):
        return False

    return bool(result.get("id"))


def read_setup_episodes():
    episodes = []
    path = state_path(SETUP_EPISODES_FILE)

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                try:
                    item = json.loads(line)

                except json.JSONDecodeError:
                    continue

                if isinstance(item, dict) and item.get("start"):
                    episodes.append({
                        "start": item.get("start"),
                        "end": item.get("end"),
                    })

    except OSError:
        pass

    try:
        with open(state_path(SETUP_CURRENT_FILE)) as f:
            start = f.read().strip()

        if start:
            episodes.append({"start": start, "end": None})

    except OSError:
        pass

    return episodes


def clear_completed_setup_episodes():
    path = state_path(SETUP_EPISODES_FILE)

    try:
        os.remove(path)

    except OSError:
        pass
