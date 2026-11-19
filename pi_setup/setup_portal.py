#!/usr/bin/env python3
import os
import re
import subprocess
import sys
from pathlib import Path
from flask import Flask, request, redirect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pi_atc_config import (
    format_station_tokens,
    normalize_airport_code,
    parse_station_tokens,
    parse_stations,
    valid_airport_code,
)

APP_DIR = Path("/opt/pi-atc")
DEFAULT_ENV_PATH = Path("/etc/pi-atc/env")
LEGACY_ENV_PATH = APP_DIR / ".env"


def env_file_path():
    override = os.environ.get("PI_ATC_ENV_FILE")

    if override:
        return Path(override)

    return DEFAULT_ENV_PATH

app = Flask(__name__)

SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATIS Relay Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#1e293b;border-radius:12px;padding:2rem;width:100%;max-width:400px;
  box-shadow:0 4px 24px rgba(0,0,0,.4)}
h1{font-size:1.4rem;margin-bottom:.25rem;color:#f8fafc}
.sub{font-size:.85rem;color:#94a3b8;margin-bottom:1.5rem}
label{display:block;font-size:.8rem;font-weight:600;color:#94a3b8;
  text-transform:uppercase;letter-spacing:.05em;margin-bottom:.3rem;margin-top:1rem}
input{width:100%;padding:.6rem .75rem;border:1px solid #334155;border-radius:6px;
  background:#0f172a;color:#f8fafc;font-size:.95rem;outline:none}
input:focus{border-color:#3b82f6}
button{width:100%;margin-top:1.5rem;padding:.75rem;border:none;border-radius:6px;
  background:#3b82f6;color:#fff;font-size:1rem;font-weight:600;cursor:pointer}
button:hover{background:#2563eb}
.err{background:#7f1d1d;color:#fca5a5;padding:.6rem .75rem;border-radius:6px;
  font-size:.85rem;margin-bottom:1rem}
</style>
</head>
<body>
<div class="card">
  <h1>ATIS Relay Setup</h1>
  <p class="sub">Connect this unit to your network and configure your station.</p>
  {{ERROR}}
  <form method="post" action="/">
    <label for="ssid">WiFi Network</label>
    <input id="ssid" name="ssid" required value="{{SSID}}">

    <label for="psk">WiFi Password</label>
    <input id="psk" name="psk" type="password" required>

    <label for="icao1">Station 1 &mdash; Airport Code</label>
    <input id="icao1" name="icao1" required placeholder="KMYF" maxlength="4"
      pattern="[A-Za-z0-9]{3,4}" value="{{ICAO1}}">

    <label for="freq1">Station 1 &mdash; Frequency (118.000 &ndash; 136.990 MHz)</label>
    <input id="freq1" name="freq1" required placeholder="124.35"
      inputmode="decimal" pattern="[0-9]{3}\\.[0-9]{1,3}" maxlength="7" value="{{FREQ1}}">

    <label for="station_key1">Station 1 key <span style="font-weight:400;text-transform:none">(optional; leave blank to keep the current key)</span></label>
    <input id="station_key1" name="station_key1" type="password" autocomplete="off">

    <label for="icao2">Station 2 &mdash; Airport Code <span style="font-weight:400;text-transform:none">(optional)</span></label>
    <input id="icao2" name="icao2" placeholder="1G0" maxlength="4"
      pattern="[A-Za-z0-9]{3,4}" value="{{ICAO2}}">

    <label for="freq2">Station 2 &mdash; Frequency (118.000 &ndash; 136.990 MHz)</label>
    <input id="freq2" name="freq2" placeholder="119.60"
      inputmode="decimal" pattern="[0-9]{3}\\.[0-9]{1,3}" maxlength="7" value="{{FREQ2}}">

    <label for="station_key2">Station 2 key <span style="font-weight:400;text-transform:none">(optional; leave blank to keep the current key)</span></label>
    <input id="station_key2" name="station_key2" type="password" autocomplete="off">

    <button type="submit">Save &amp; Connect</button>
  </form>
</div>
</body>
</html>"""

DONE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATIS Relay Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#1e293b;border-radius:12px;padding:2rem;width:100%;max-width:400px;
  text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.4)}
h1{font-size:1.4rem;margin-bottom:.5rem;color:#f8fafc}
p{color:#94a3b8;font-size:.95rem;line-height:1.5}
</style>
</head>
<body>
<div class="card">
  <h1>Configuration Saved</h1>
  <p>The unit is rebooting and will connect to your WiFi network.
     You can disconnect from ATIS Relay Setup now.</p>
</div>
</body>
</html>"""


def render_form(error="", ssid="", icao1="", freq1="", icao2="", freq2=""):
    err_block = f'<div class="err">{error}</div>' if error else ""
    html = SETUP_HTML
    html = html.replace("{{ERROR}}", err_block)
    html = html.replace("{{SSID}}", ssid)
    html = html.replace("{{ICAO1}}", icao1)
    html = html.replace("{{FREQ1}}", freq1)
    html = html.replace("{{ICAO2}}", icao2)
    html = html.replace("{{FREQ2}}", freq2)
    return html


def validate_freq(val):
    if not re.match(r"^\d{3}\.\d{1,3}$", val):
        return False
    try:
        f = float(val)
        return 118.0 <= f <= 136.999
    except (ValueError, TypeError):
        return False


def write_env(stations, keys=None):
    existing = {}
    path = env_file_path()
    sources = []
    keys = keys or {}

    if path.exists():
        sources.append(path)

    if LEGACY_ENV_PATH.exists() and LEGACY_ENV_PATH != path:
        sources.append(LEGACY_ENV_PATH)

    for src in sources:
        for line in src.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                if key not in existing or existing.get(key) == "":
                    existing[key] = v.strip()

    old_stations = parse_stations(existing.get("STATIONS", ""))
    tokens = parse_station_tokens(existing.get("STATION_TOKENS", ""))
    shorthand = (existing.get("STATION_TOKEN") or "").strip()

    if shorthand and len(old_stations) == 1 and old_stations[0][0] not in tokens:
        tokens[old_stations[0][0]] = shorthand

    for icao, token in keys.items():
        if icao and token:
            tokens[icao] = token

    existing["STATIONS"] = ",".join(f"{icao}:{freq}" for icao, freq in stations)

    if "ATIS_RELAY_URL" not in existing or existing.get("ATIS_RELAY_URL") == "":
        existing["ATIS_RELAY_URL"] = "https://atisrelay.com"

    existing["STATION_TOKENS"] = format_station_tokens(tokens)

    if len(stations) == 1 and stations[0][0] in tokens:
        existing["STATION_TOKEN"] = tokens[stations[0][0]]

    elif len(stations) <= 1 and len(tokens) == 1:
        existing["STATION_TOKEN"] = next(iter(tokens.values()))

    else:
        existing["STATION_TOKEN"] = ""

    path.parent.mkdir(parents=True, exist_ok=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def configure_wifi(ssid, psk):
    def run(args):
        res = subprocess.run(args, capture_output=True, check=False)
        if res.returncode != 0:
            err = res.stderr.decode(errors="ignore").strip()
            print(f"[wifi] {' '.join(args[:3])} failed: {err}")
        return res.returncode == 0

    run(["nmcli", "connection", "delete", ssid])
    run(["nmcli", "connection", "add",
         "type", "wifi",
         "con-name", ssid,
         "ssid", ssid])
    run(["nmcli", "connection", "modify", ssid,
         "802-11-wireless-security.key-mgmt", "wpa-psk"])
    run(["nmcli", "connection", "modify", ssid,
         "802-11-wireless-security.psk", psk])
    run(["nmcli", "connection", "modify", ssid,
         "connection.autoconnect", "yes"])


@app.route("/", methods=["GET"])
def index():
    return render_form()


@app.route("/", methods=["POST"])
def submit():
    ssid = request.form.get("ssid", "").strip()
    psk = request.form.get("psk", "").strip()
    icao1 = normalize_airport_code(request.form.get("icao1", ""))
    freq1 = request.form.get("freq1", "").strip()
    icao2 = normalize_airport_code(request.form.get("icao2", ""))
    freq2 = request.form.get("freq2", "").strip()
    station_key1 = request.form.get("station_key1", "").strip()
    station_key2 = request.form.get("station_key2", "").strip()
    fields = dict(ssid=ssid, icao1=icao1, freq1=freq1, icao2=icao2, freq2=freq2)

    if not ssid or not psk:
        return render_form("WiFi network and password are required.", **fields)

    if not valid_airport_code(icao1):
        return render_form("Station 1: enter a valid 3- or 4-character airport code.", **fields)

    if not validate_freq(freq1):
        return render_form("Station 1: enter a frequency between 118.00 and 136.99 MHz.", **fields)

    stations = [(icao1, freq1)]

    if icao2 or freq2:
        if not valid_airport_code(icao2):
            return render_form("Station 2: enter a valid 3- or 4-character airport code.", **fields)

        if not validate_freq(freq2):
            return render_form("Station 2: enter a frequency between 118.00 and 136.99 MHz.", **fields)

        stations.append((icao2, freq2))

    keys = {}

    if station_key1:
        keys[icao1] = station_key1

    if icao2 and station_key2:
        keys[icao2] = station_key2

    write_env(stations, keys=keys)
    configure_wifi(ssid, psk)

    subprocess.Popen(["bash", "-c", "sleep 3 && reboot"], close_fds=True)
    return DONE_HTML


@app.route("/generate_204")
@app.route("/gen_204")
def android_portal():
    return redirect("/", code=302)


@app.route("/hotspot-detect.html")
def apple_portal():
    return render_form()


@app.route("/connecttest.txt")
def windows_portal():
    return redirect("/", code=302)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
