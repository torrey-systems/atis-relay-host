#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

from pi_atc_config import (
    atis_relay_url,
    load_config,
    station_token_for,
    stations_from_env,
    upload_mode,
)
from pi_atc_state import (
    clear_completed_setup_episodes,
    derive_pi_id,
    evaluate_command,
    read_last_command,
    read_last_upload,
    read_last_uploads,
    read_mode,
    read_setup_episodes,
    record_last_heartbeat,
    should_persist_command,
    utc_now_iso,
    write_last_command,
)

BUCKET = "ownnav-maps"
STATUS_PREFIX = "pi-status/"
COMMAND_PREFIX = "pi-commands/"
SCHEMA_VERSION = 1
RTL_VENDOR = "0bda"
RTL_PRODUCTS = {"2832", "2836", "2838"}


def _run(args, timeout=5):
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    except (OSError, subprocess.SubprocessError):
        return None


def read_code_version():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(here, "VERSION"),
        "/opt/pi-atc/VERSION",
    )

    for path in candidates:
        try:
            value = open(path).read().strip()

        except OSError:
            continue

        if value:
            return value

    return "unknown"


def read_uptime_seconds():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])

    except (OSError, ValueError, IndexError):
        return None


def read_boot_time():
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    ts = int(line.split()[1])
                    return utc_now_iso(datetime.fromtimestamp(ts, tz=timezone.utc))

    except (OSError, ValueError, IndexError):
        return None

    return None


def read_cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0

    except (OSError, ValueError):
        return None


def read_memory_free_kb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])

    except (OSError, ValueError, IndexError):
        return None

    return None


def read_disk_free_bytes(path="/"):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    except OSError:
        return None


def read_throttled():
    res = _run(["vcgencmd", "get_throttled"], timeout=3)

    if res is None or res.returncode != 0:
        return None

    out = (res.stdout or "").strip()

    if "=" in out:
        return out.split("=", 1)[1]

    return out or None


def read_primary_ip():
    res = _run(["ip", "-4", "-o", "addr", "show", "wlan0"], timeout=3)

    if res is not None and res.returncode == 0:
        parts = (res.stdout or "").split()

        if "inet" in parts:
            idx = parts.index("inet")

            if idx + 1 < len(parts):
                return parts[idx + 1].split("/")[0]

    res = _run(["hostname", "-I"], timeout=3)

    if res is None:
        return None

    addrs = (res.stdout or "").split()
    return addrs[0] if addrs else None


def read_wifi():
    ssid = None
    signal = None
    res = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"], timeout=5)

    if res is not None and res.returncode == 0:
        for line in (res.stdout or "").splitlines():
            if not line.startswith("yes:"):
                continue

            rest = line[4:]
            bits = rest.rsplit(":", 1)
            ssid = bits[0] or None

            if len(bits) == 2 and bits[1].isdigit():
                signal = int(bits[1])

            break

    if ssid is None:
        res = _run(["iwgetid", "-r"], timeout=3)

        if res is not None and res.returncode == 0:
            ssid = (res.stdout or "").strip() or None

    if signal is None:
        try:
            with open("/proc/net/wireless") as f:
                for line in f:
                    if "wlan0" not in line:
                        continue

                    cols = line.replace(":", " ").split()

                    if len(cols) >= 4:
                        signal = int(float(cols[3]))

        except (OSError, ValueError, IndexError):
            pass

    return {"ssid": ssid, "signal": signal}


def rtl_sdr_present():
    root = "/sys/bus/usb/devices"

    try:
        names = os.listdir(root)

    except OSError:
        return False

    for name in names:
        vendor_path = os.path.join(root, name, "idVendor")
        product_path = os.path.join(root, name, "idProduct")

        try:
            vendor = open(vendor_path).read().strip().lower()
            product = open(product_path).read().strip().lower()

        except OSError:
            continue

        if vendor == RTL_VENDOR and product in RTL_PRODUCTS:
            return True

    return False


def read_service_state(name="pi-atc.service"):
    res = _run(
        ["systemctl", "show", "-p", "ActiveState", "-p", "SubState", "--value", name],
        timeout=3,
    )

    if res is None:
        return {"active": "unknown", "sub": "unknown"}

    lines = [line for line in (res.stdout or "").splitlines() if line]

    if len(lines) >= 2:
        return {"active": lines[0], "sub": lines[1]}

    if lines:
        return {"active": lines[0], "sub": "unknown"}

    return {"active": "unknown", "sub": "unknown"}


def detect_mode():
    mode = read_mode()

    if mode:
        return mode

    if os.path.exists("/tmp/pi-atc-hostapd.conf"):
        return "setup"

    return "online"


def collect_status(now=None, pi_id=None):
    if now is None:
        now = datetime.now(timezone.utc)

    if pi_id is None:
        pi_id = derive_pi_id()

    try:
        hostname = socket.gethostname()

    except OSError:
        hostname = None

    return {
        "schema_version": SCHEMA_VERSION,
        "pi_id": pi_id,
        "hostname": hostname,
        "timestamp": utc_now_iso(now),
        "code_version": read_code_version(),
        "uptime_seconds": read_uptime_seconds(),
        "boot_time": read_boot_time(),
        "wifi": read_wifi(),
        "ip": read_primary_ip(),
        "mode": detect_mode(),
        "pi_atc_service": read_service_state(),
        "last_upload": read_last_upload(),
        "last_uploads": read_last_uploads(),
        "rtl_sdr": rtl_sdr_present(),
        "cpu_temp_c": read_cpu_temp_c(),
        "throttled": read_throttled(),
        "memory_free_kb": read_memory_free_kb(),
        "disk_free_bytes": read_disk_free_bytes(),
        "setup_episodes": read_setup_episodes(),
        "commands": {"fetch": "skipped"},
        "last_command": read_last_command(),
    }


def classify_client_error(exc):
    code = ""
    response = getattr(exc, "response", None)

    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))

    if code in ("NoSuchKey", "404", "NotFound"):
        return "no_such_key", code

    if code in ("AccessDenied", "403", "Forbidden"):
        return "access_denied", code

    return "error", code or exc.__class__.__name__


def fetch_command(s3, pi_id):
    key = COMMAND_PREFIX + pi_id + ".json"

    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        body = obj["Body"].read()

        if isinstance(body, bytes):
            body = body.decode("utf-8")

        data = json.loads(body)

        if not isinstance(data, dict):
            return None, "error", "invalid_json"

        return data, "ok", None

    except Exception as exc:
        if exc.__class__.__name__ == "ClientError" or "ClientError" in [c.__name__ for c in exc.__class__.__mro__]:
            status, code = classify_client_error(exc)

            if status in ("no_such_key", "access_denied"):
                print(f"[heartbeat] command fetch {status}" + (f" ({code})" if code else ""))
                return None, status, code or None

            print(f"[heartbeat] command fetch error {code or exc}")
            return None, "error", code or str(exc)

        if isinstance(exc, json.JSONDecodeError):
            print("[heartbeat] command fetch invalid json")
            return None, "error", "invalid_json"

        print(f"[heartbeat] command fetch error {exc}")
        return None, "error", str(exc)


def put_json(s3, key, payload):
    body = json.dumps(payload, indent=2) + "\n"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )


def default_s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"),
        config=Config(
            connect_timeout=8,
            read_timeout=15,
            retries={"max_attempts": 2},
        ),
    )


def default_reboot():
    subprocess.run(["reboot"], check=False)


def reset_usb():
    unbind = "/sys/bus/usb/drivers/usb/unbind"
    bind = "/sys/bus/usb/drivers/usb/bind"

    for hub in ("usb1", "usb2"):
        try:
            with open(unbind, "w") as f:
                f.write(hub)

        except OSError:
            pass

        time.sleep(0.5)

        try:
            with open(bind, "w") as f:
                f.write(hub)

        except OSError:
            pass

    return True


def restart_capture():
    res = subprocess.run(
        ["systemctl", "restart", "pi-atc.service"],
        capture_output=True,
        check=False,
    )
    return res.returncode == 0


def persist_command_result(result, now=None):
    payload = {
        "id": result.get("id"),
        "action": result.get("action"),
        "acked_at": utc_now_iso(now),
        "result": result.get("decision"),
        "reason": result.get("reason"),
        "requested_by": result.get("requested_by"),
    }
    write_last_command(payload)
    return payload


def parse_throttled_flags(raw):
    if raw is None or raw == "":
        return None

    text = str(raw).strip()

    try:
        if text.lower().startswith("0x"):
            return int(text, 16)

        return int(text)

    except (TypeError, ValueError):
        return None


def heartbeat_payload_from_status(status):
    service = status.get("pi_atc_service") or {}
    active = service.get("active")
    capture = "running" if active == "active" else "stopped"
    payload = {
        "temp_c": status.get("cpu_temp_c"),
        "throttled_flags": parse_throttled_flags(status.get("throttled")),
        "capture_status": capture,
        "last_error": None,
    }
    uptime = status.get("uptime_seconds")

    if uptime is not None:
        try:
            payload["uptime_seconds"] = int(uptime)

        except (TypeError, ValueError):
            pass

    return {key: value for key, value in payload.items() if value is not None or key == "last_error"}


def run_http_checkin(reboot_fn=None, now=None, pi_id=None, session=None, sleep_fn=None):
    from pi_atc_http import (
        AuthRejected,
        ClientError,
        Forbidden,
        TransientError,
        ack_command,
        fetch_commands,
        post_heartbeat,
    )

    if now is None:
        now = datetime.now(timezone.utc)

    if pi_id is None:
        pi_id = derive_pi_id()

    if reboot_fn is None:
        reboot_fn = default_reboot

    status = collect_status(now=now, pi_id=pi_id)
    base_url = atis_relay_url()
    stations = stations_from_env()
    payload = heartbeat_payload_from_status(status)
    last = read_last_command()
    last_id = last.get("id") if isinstance(last, dict) else None
    commands_info = {"fetch": "ok"}
    reboot_after = None

    if not stations:
        print("[heartbeat] no stations configured; skipping HTTP check-in")
        status["commands"] = {"fetch": "skipped"}
        return status

    if session is None:
        import requests
        session = requests.Session()

    if sleep_fn is None:
        sleep_fn = time.sleep

    for icao, _freq in stations:
        token = station_token_for(icao)

        if not token:
            print("[heartbeat] %s: station token not configured; set a key with: sudo pi-atc set-key --icao %s" % (icao, icao))
            commands_info = {"fetch": "skipped"}
            continue

        try:
            post_heartbeat(base_url, token, icao, payload, session=session, sleep_fn=sleep_fn)
            record_last_heartbeat(now=now, icao=icao)

        except (AuthRejected, Forbidden) as exc:
            print("[heartbeat] %s for %s; set a valid key with: sudo pi-atc set-key --icao %s" % (exc, icao, icao))
            commands_info = {"fetch": "access_denied"}
            continue

        except (TransientError, ClientError) as exc:
            print("[heartbeat] heartbeat failed for %s: %s" % (icao, exc))
            commands_info = {"fetch": "error", "error": str(exc)}
            continue

        try:
            pending = fetch_commands(base_url, token, icao, session=session, sleep_fn=sleep_fn)

        except (AuthRejected, Forbidden) as exc:
            print("[heartbeat] %s for %s; set a valid key with: sudo pi-atc set-key --icao %s" % (exc, icao, icao))
            commands_info = {"fetch": "access_denied"}
            continue

        except (TransientError, ClientError) as exc:
            print("[heartbeat] command fetch failed for %s: %s" % (icao, exc))
            commands_info = {"fetch": "error", "error": str(exc)}
            continue

        commands_info = {"fetch": "ok"}

        for command in pending:
            if not isinstance(command, dict):
                continue

            cmd_id = command.get("id")
            action = command.get("command")
            ident = "%s:%s" % (icao, cmd_id)

            if last_id is not None and ident == str(last_id):
                continue

            if action not in ("reboot", "restart_capture", "usb_reset"):
                persist_command_result({
                    "id": ident,
                    "action": action,
                    "decision": "ignore",
                    "reason": "unsupported_action",
                }, now=now)
                continue

            ok = True
            result_text = action

            if action == "restart_capture":
                ok = restart_capture()
                result_text = "restarted" if ok else "restart failed"

            elif action == "usb_reset":
                ok = reset_usb()
                result_text = "usb reset" if ok else "usb reset failed"

            elif action == "reboot":
                result_text = "rebooting"

            try:
                ack_command(
                    base_url,
                    token,
                    icao,
                    cmd_id,
                    ok=ok,
                    result=result_text,
                    session=session,
                    sleep_fn=sleep_fn,
                )

            except (AuthRejected, Forbidden, TransientError, ClientError) as exc:
                print("[heartbeat] command ack failed for %s: %s" % (icao, exc))

            last = persist_command_result({
                "id": ident,
                "action": action,
                "decision": "execute" if ok else "ignore",
                "reason": result_text,
            }, now=now)
            last_id = ident
            status["last_command"] = last

            if action == "reboot" and ok:
                reboot_after = ident

    status["commands"] = commands_info
    status["last_command"] = last

    try:
        clear_completed_setup_episodes()

    except Exception as exc:
        print("[heartbeat] setup episode clear failed: %s" % exc)

    if reboot_after:
        print("[heartbeat] executing reboot for command %s" % reboot_after)
        reboot_fn()

    return status


def run_s3_checkin(s3_client=None, reboot_fn=None, now=None, pi_id=None):
    if now is None:
        now = datetime.now(timezone.utc)

    if pi_id is None:
        pi_id = derive_pi_id()

    if reboot_fn is None:
        reboot_fn = default_reboot

    own_s3 = False

    if s3_client is None:
        s3_client = default_s3_client()
        own_s3 = True

    status = collect_status(now=now, pi_id=pi_id)
    command, fetch_status, fetch_detail = fetch_command(s3_client, pi_id)
    commands_info = {"fetch": fetch_status}

    if fetch_detail:
        commands_info["error"] = fetch_detail

    status["commands"] = commands_info
    last = read_last_command()
    last_id = last.get("id") if isinstance(last, dict) else None
    result = None

    if command is not None:
        result = evaluate_command(command, last_id=last_id, now=now)

        if result.get("decision") == "defer":
            status["pending_command"] = {
                "id": result.get("id"),
                "action": result.get("action"),
                "not_before": result.get("not_before"),
            }

        elif should_persist_command(result):
            status["last_command"] = persist_command_result(result, now=now)

        else:
            status["last_command"] = last

    else:
        status["last_command"] = last

    uploaded = False

    try:
        put_json(s3_client, STATUS_PREFIX + pi_id + ".json", status)
        uploaded = True

    except Exception as exc:
        print(f"[heartbeat] status upload failed: {exc}")

    if uploaded:
        try:
            record_last_heartbeat(now=now)

        except Exception:
            pass

        try:
            clear_completed_setup_episodes()

        except Exception as exc:
            print(f"[heartbeat] setup episode clear failed: {exc}")

    if result and result.get("decision") == "execute" and result.get("action") == "reboot":
        ack = {
            "id": result.get("id"),
            "action": result.get("action"),
            "acked_at": status.get("last_command", {}).get("acked_at") or utc_now_iso(now),
            "pi_id": pi_id,
            "requested_by": result.get("requested_by"),
        }

        try:
            put_json(s3_client, COMMAND_PREFIX + pi_id + ".ack.json", ack)

        except Exception as exc:
            print(f"[heartbeat] command ack upload failed: {exc}")

        print(f"[heartbeat] executing reboot for command {result.get('id')}")
        reboot_fn()

    if own_s3:
        try:
            s3_client.close()

        except Exception:
            pass

    return status


def run_checkin(s3_client=None, reboot_fn=None, now=None, pi_id=None):
    load_config()

    if s3_client is not None:
        return run_s3_checkin(
            s3_client=s3_client,
            reboot_fn=reboot_fn,
            now=now,
            pi_id=pi_id,
        )

    mode = upload_mode()

    if mode == "http":
        return run_http_checkin(reboot_fn=reboot_fn, now=now, pi_id=pi_id)

    if mode == "s3":
        return run_s3_checkin(reboot_fn=reboot_fn, now=now, pi_id=pi_id)

    print("[heartbeat] station token not configured; skipping check-in. Set a key with: sudo pi-atc set-key")
    status = collect_status(now=now, pi_id=pi_id)
    status["commands"] = {"fetch": "skipped"}
    return status


def main():
    try:
        load_config()
        run_checkin()

    except Exception as exc:
        print(f"[heartbeat] {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
