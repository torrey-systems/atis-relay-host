# ATIS Relay host

Raspberry Pi station software for ATIS Relay. It records aviation frequencies with an RTL-SDR, skips duplicate loops, and uploads unique MP3 clips to ATIS Relay.

Supported hardware: Raspberry Pi 3, 4, and 5 on 64-bit Raspberry Pi OS (Bookworm or Trixie). Raspberry Pi Zero W is not supported.

## Install

Download the `atis-relay-host_<ver>_all.deb` package from GitHub Releases, copy it onto the Pi, and install it:

```bash
sudo apt install ./atis-relay-host_<ver>_all.deb
```

Replace `<ver>` with the version on the file, for example `0.1.0`.

Then set the station key issued by ATIS Relay. A key is scoped to one airport. If this Pi serves two airports, set a key for each:

```bash
sudo pi-atc set-key
sudo pi-atc set-key --icao KMYF
sudo pi-atc set-key --icao KSEE
```

`--icao` is optional when only one station is configured. The command reads the key from the argument or from stdin and does not print it. You can also pipe it:

```bash
printf '%s\n' "$STATION_TOKEN" | sudo pi-atc set-key
```

Check configuration without printing the key:

```bash
sudo pi-atc status
```

On first boot with no saved WiFi, the Pi raises a setup access point named **ATIS Relay Setup** (password `aviation`). Join it, open the captive portal, and enter WiFi plus station ICAO and frequency. Each station has its own key field; leave a field blank to keep a key already on the device.

## Layout

| Path | Purpose |
|------|---------|
| `/opt/pi-atc` | Application code |
| `/etc/pi-atc/env` | Configuration (conffile, mode 0600). Units load this file. |
| `/var/lib/pi-atc` | Runtime state (last upload, heartbeat, commands) |
| `/usr/bin/pi-atc` | `set-key` and `status` |

Factory image baking still uses `pi_setup/install.sh`. That script installs the same layout. The package postinst does not prompt for WiFi and does not remove NetworkManager connections.

## Configuration

Edit `/etc/pi-atc/env` or use `pi-atc set-key` and the setup portal.

| Variable | Description | Example |
|----------|-------------|---------|
| `STATION_TOKEN` | Station key from ATIS Relay when this Pi serves one airport | (set with `pi-atc set-key`) |
| `STATION_TOKENS` | Per-airport keys when this Pi serves two airports | `KMYF:<token>,KSEE:<token>` |
| `ATIS_RELAY_URL` | Relay base URL | `https://atisrelay.com` |
| `STATIONS` | Comma-separated `ICAO:FREQ` pairs | `KSAN:124.350,KCRQ:119.600` |
| `PI_ID` | Optional stable device id. Defaults to the hostname. | `nkx-relay` |
| `AUDIO_DEVICE` | ALSA device | `plughw:1,0` |
| `SAMPLE_RATE` | Recording sample rate in Hz | `16000` |
| `CHUNK_SEC` | Recording duration per chunk in seconds | `60` |
| `AP_PASSWORD` | Captive portal WiFi password (min 8 chars) | `aviation` |

If `/opt/pi-atc/.env` exists from an older install, the package copies unset keys such as `STATIONS` and `PI_ID` into `/etc/pi-atc/env` without printing values.

The uploader and heartbeat wait and log when no station key is configured. They do not crash-loop. Heartbeat and command polling use that airport's token for each configured ICAO. After a 401 they stop using that token until a valid key is set with `sudo pi-atc set-key`. Audio 403 means the token belongs to a different airport.

## Uploads, heartbeat, and commands

With a station key set, the Pi talks to ATIS Relay over HTTPS using `Authorization: Bearer <token>`:

- `POST {ATIS_RELAY_URL}/api/pi/{ICAO}/audio` multipart field `file` (MP3, same `ICAO_` tempfile name as before) and form field `recorded_at` (UTC ISO8601). Success is HTTP 201 `{key, icao, received_at}`. 10 MB cap. 30 uploads per station per minute.
- `POST /api/pi/{ICAO}/heartbeat` about every 5 minutes with `temp_c`, `throttled_flags`, `uptime_seconds`, `capture_status`, and `last_error`. Success is HTTP 200 `{"ok": true}`.
- `GET /api/pi/{ICAO}/commands` returns `{commands: [{id, command, queued_at, expires_at}]}` where command is `restart_capture`, `usb_reset`, or `reboot`. `POST /api/pi/{ICAO}/commands/{id}/ack` with `{ok, result}`.

Audio 401 is a bad token. Audio 403 is a token for a different ICAO. Heartbeat and commands return 401 when a valid token is used on another ICAO. 404 unknown ICAO, 413 too large, and 422 invalid file or recorded_at are not retried. 429 (plain text, back off) and 503 `upload_failed` are retried.

Legacy S3 uploads (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` to `s3://ownnav-maps`) remain only when those keys are present and no station token is configured, so existing factory Pis keep working until they are migrated. That path is deprecated. New installs should use a station key. `python3-boto3` is optional for the legacy path only.

## Hardware

- Raspberry Pi 3, 4, or 5
- RTL-SDR USB dongle (RTL2832U-based)
- Antenna for aviation VHF (108-137 MHz)
- SD card (16 GB+)
- Power supply

## How it works

On boot the watchdog (`boot_check.sh`) waits up to 2 minutes for WiFi. If connected, it starts the uploader. If not, it starts the setup access point and captive portal. A heartbeat timer checks in every 5 minutes and honors remote reboot, capture restart, and USB reset commands. A nightly reboot runs at 03:00 America/Los_Angeles (`OnCalendar=*-*-* 03:00:00 America/Los_Angeles`). The Pi clock itself stays UTC.

The uploader records a 60-second chunk, compares it to the previous loop, and uploads when the audio changed or when a station has been silent for 30 minutes.

## Services

| Unit | Role |
|------|------|
| `pi-atc.service` | Audio capture and upload |
| `pi-atc-setup.service` | WiFi watchdog and captive portal |
| `pi-atc-heartbeat.timer` | Status check-in every 5 minutes |
| `pi-atc-reboot.timer` | Nightly reboot |

```bash
journalctl -u pi-atc.service -f
sudo systemctl restart pi-atc.service
```

## Development

```bash
python3 -m unittest discover -s tests -v
shellcheck pi_setup/*.sh
```

GitHub Actions builds `atis-relay-host_<ver>_all.deb` on every push and pull request (artifact name `atis-relay-host-deb`) and attaches it to GitHub Releases for `v*` tags.
