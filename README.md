# Pi ATC

A Raspberry Pi-based ATC radio monitor. Records ATIS and other aviation frequencies using an RTL-SDR dongle, deduplicates consecutive recordings via spectral fingerprinting, and uploads unique audio chunks to S3. Supports monitoring up to two stations from a single device.

End users configure the device over WiFi through a captive portal -- no monitor, keyboard, or SSH required.

## Hardware

- Raspberry Pi 3, 4, or 5
- RTL-SDR USB dongle (RTL2832U-based)
- Antenna appropriate for the target frequency (aviation VHF: 108-137 MHz)
- SD card (16 GB+)
- Power supply

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Raspberry Pi                                            │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │  boot_check  │───▶│ setup_portal │    │   S3       │  │
│  │  (watchdog)  │    │ (Flask)      │    │            │  │
│  └──────┬───────┘    └──────────────┘    └─────▲──────┘  │
│         │                                      │         │
│         ▼                                      │         │
│  ┌──────────────┐    ┌──────────────┐    ┌─────┴──────┐  │
│  │  pi-atc      │───▶│ audio        │───▶│  upload    │  │
│  │  .service    │    │ _uploader.py │    │  (mp3)     │  │
│  └──────────────┘    └──────┬───────┘    └────────────┘  │
│                             │                            │
│                      ┌──────┴───────┐                    │
│                      │  RTL-SDR /   │                    │
│                      │  arecord     │                    │
│                      └──────────────┘                    │
│                                                          │
│  ┌──────────────┐                        ┌────────────┐  │
│  │  heartbeat   │───────────────────────▶│ pi-status/ │  │
│  │  .timer      │                        │ pi-commands│  │
│  └──────────────┘                        └────────────┘  │
└──────────────────────────────────────────────────────────┘
```

On boot, the watchdog (`boot_check.sh`) waits up to 2 minutes for WiFi. If connected, it starts the uploader service. If not, it starts a WiFi access point and captive portal for first-time setup. A separate heartbeat timer checks in to S3 every 5 minutes and honors remote reboot commands.

## Quick Start

### 1. Write Raspberry Pi OS to the SD Card

Download and open [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Select **Raspberry Pi OS Lite (64-bit)**, choose your SD card, and click the gear icon to configure:

- **Hostname**: `pi-atc`
- **Username**: `pi`, **Password**: `raspberry`
- **WiFi**: leave blank (the install script will prompt for WiFi)
- **Raspberry Pi Connect** (optional): enable for remote terminal access from a browser

Then click **Write**. This installs the Pi's operating system onto the card.

### 2. Install

Once the write is complete, remove and re-insert the SD card. A drive called `bootfs` will appear in Finder. Copy the project onto it:

```bash
rsync -a --exclude '.env' --exclude 'samples' --exclude 'samples_clean' ~/Development/pi-atc/ /Volumes/bootfs/pi-atc/
```

### 3. Bake AWS Credentials

Create the file `/Volumes/bootfs/pi-atc/.env` with your shared AWS credentials:

```
AWS_DEFAULT_REGION=us-east-2
AWS_ACCESS_KEY_ID=<your key>
AWS_SECRET_ACCESS_KEY=<your secret>
```

These are baked into the image and not exposed to end users, but should be restricted to the specific s3://ownnav-maps/raw_atc_stream/<icao>/ the end user is expected to upload to.

### 4. Boot and Install

Eject the SD card, insert it into the Pi, and plug in a keyboard and monitor. Power on the Pi, log in as `pi` or other configured user, and run:

```bash
sudo bash /boot/firmware/pi-atc/pi_setup/install.sh
```

The script will set the WiFi country code (required on Pi before WiFi will work), then prompt for your WiFi network name and password. Once connected, it downloads and installs all required packages. When it finishes, reboot:

```bash
sudo reboot
```

This only needs to be done once. After reboot, the Pi enters setup mode and you can unplug the keyboard and monitor. If you're distributing multiple units, image the SD card at this point and copy it to other cards.

## End-User Setup

1. Power on the Pi
2. On your phone, connect to the WiFi network **ATIS Relay Setup** (password: `aviaton`)
3. A setup page opens automatically (captive portal). If it doesn't, browse to any URL.
4. Fill in:
   - **WiFi Network** and **Password**
   - **Station 1**: airport ICAO code and ATIS frequency in MHz
   - **Station 2** (optional): second airport to monitor
5. Tap **Save & Connect**
6. The Pi reboots, joins your WiFi, and begins recording

## How It Works

### Recording Loop

The uploader runs in a continuous loop:

1. Record a 60-second audio chunk from the configured audio device
2. Compute a 64-band log-spectrum fingerprint of the recording
3. Compare the fingerprint to the previous chunk for the same station using cosine similarity
4. If the similarity is below the threshold (default: 0.85), convert to MP3 and upload to S3
5. If the audio is too similar, skip the upload to save bandwidth
6. If no upload has occurred for a station in 30 minutes, upload regardless (staleness override)
7. Sleep 60 seconds, then move to the next station

With two stations configured, the Pi alternates: record Station 1, sleep, record Station 2, sleep, repeat. Each station maintains independent fingerprint and staleness state.

### WiFi Watchdog

The watchdog (`boot_check.sh`) runs as a systemd service and monitors WiFi continuously:

- If WiFi drops for more than 2 minutes, the watchdog stops the uploader and starts the captive portal AP
- While in AP mode, the watchdog retries the previously configured WiFi every 5 minutes and waits 45 seconds for association + DHCP (the previous 15-second / 10-minute window could leave the Pi stranded long after Wi-Fi returned)
- When WiFi reconnects (e.g., after a router reboot), the AP is torn down and the uploader resumes automatically
- Entering and leaving setup mode is appended to a local episode log so the next status check-in can report those intervals

### Deduplication

Audio fingerprinting uses a lightweight spectral analysis pipeline:

1. STFT with a 1024-sample Hann window, 512-sample hop
2. Average magnitude spectrum across all frames
3. Split into 64 coarse frequency bands, take the mean per band
4. Log-compress and L2-normalize the band vector
5. Cosine similarity between consecutive chunks determines whether to upload

The threshold is controlled by `COSINE_THRESHOLD` (default `0.15`). A similarity of `1 - 0.15 = 0.85` or higher means the audio is considered unchanged.

## Project Structure

```
pi-atc/
├── audio_uploader.py          # Main recording/upload loop
├── heartbeat.py               # Independent 5-minute S3 status check-in
├── pi_atc_state.py            # Shared PI_ID, last-upload, and command state
├── transcribe_sample.py       # Dev tool: transcribe samples with Whisper
├── requirements.txt           # Python dependencies (dev machine)
├── .gitignore
├── tests/                     # Lightweight unit tests
├── pi_setup/
│   ├── install.sh             # One-shot installer for a fresh Pi
│   ├── deploy_update.sh       # Idempotent update of an existing install
│   ├── setup_portal.py        # Flask captive portal
│   ├── boot_check.sh          # WiFi watchdog (runs on boot)
│   ├── start_ap.sh            # Brings up hostapd + dnsmasq AP
│   ├── stop_ap.sh             # Tears down AP mode
│   ├── pi-atc-setup.service   # systemd unit for the watchdog
│   ├── pi-atc.service         # systemd unit for the uploader
│   ├── pi-atc-heartbeat.service
│   ├── pi-atc-heartbeat.timer
│   ├── pi-atc-reboot.service
│   ├── pi-atc-reboot.timer
│   ├── journald-pi-atc.conf   # Persistent journal, 50M cap
│   └── wifi-powersave-off.conf
```

## Configuration

All configuration lives in `/opt/pi-atc/.env` on the Pi. The captive portal writes user-facing values; AWS credentials are pre-baked.

| Variable | Description | Example |
|----------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | S3 upload credentials (baked) | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | S3 upload credentials (baked) | `dTYF...` |
| `AWS_DEFAULT_REGION` | S3 region | `us-east-2` |
| `STATIONS` | Comma-separated `ICAO:FREQ` pairs (1-2) | `KSAN:124.350,KCRQ:119.600` |
| `AUDIO_DEVICE` | ALSA device or avfoundation input | `plughw:1,0` |
| `SAMPLE_RATE` | Recording sample rate in Hz | `16000` |
| `CHUNK_SEC` | Recording duration per chunk in seconds | `60` |
| `COSINE_THRESHOLD` | Similarity threshold for dedup (0-1) | `0.15` |
| `BANDS` | Number of spectral bands for fingerprinting | `64` |
| `PI_ID` | Optional stable device id for status/command keys. Defaults to the hostname. Never randomized. | `nkx-relay` |
| `AP_PASSWORD` | Captive portal WiFi password (min 8 chars) | `aviation` |

## S3 Upload Path

Recordings are uploaded to:

```
s3://ownnav-maps/raw_atc_stream/<icao>/<filename>.mp3
```

Filenames are generated from `tempfile` with the ICAO code as a prefix (e.g., `KSAN_abc123.mp3`).

## Monitoring, reboot, and remote commands

The heartbeat is its own systemd service and timer (`pi-atc-heartbeat.timer`). It runs every 5 minutes even when `pi-atc.service` is stopped. It never blocks the uploader (`Nice=10`, 60s timeout).

`pi_id` is stable across reboots: `PI_ID` from `.env` if set, otherwise the hostname (`socket.gethostname()`, then `/etc/hostname`, then `pi-atc`). It is never a random per-boot value. Give each unit a unique hostname or set `PI_ID` if several Pis would otherwise share the same hostname.

Status is written to:

```
s3://ownnav-maps/pi-status/<pi_id>.json
```

Commands are read from:

```
s3://ownnav-maps/pi-commands/<pi_id>.json
```

Acknowledgements are written to the next status JSON and to:

```
s3://ownnav-maps/pi-commands/<pi_id>.ack.json
```

A nightly reboot runs at 03:00 America/Los_Angeles via `pi-atc-reboot.timer` (`OnCalendar=*-*-* 03:00:00 America/Los_Angeles`; timezone last, required on systemd 257). The Pi clock itself stays UTC.

### Status JSON schema (version 1)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | number | Currently `1` |
| `pi_id` | string | Stable id (hostname or `PI_ID`) |
| `hostname` | string \| null | Kernel hostname |
| `timestamp` | string | Check-in time, UTC ISO8601 with `Z` |
| `code_version` | string | Git SHA baked into `/opt/pi-atc/VERSION` at deploy, or `unknown` |
| `uptime_seconds` | number \| null | Seconds since boot |
| `boot_time` | string \| null | UTC ISO8601 boot time |
| `wifi` | object | `{ "ssid": string\|null, "signal": number\|null }` — `signal` is nmcli 0–100 when available |
| `ip` | string \| null | IPv4 of `wlan0`, else the first address from `hostname -I` |
| `mode` | string | `"online"` or `"setup"` (captive-portal hotspot) |
| `pi_atc_service` | object | `{ "active": string, "sub": string }` from systemd |
| `last_upload` | object \| null | Most recent successful audio upload: `{ "station", "key", "ts" }` |
| `last_uploads` | object | Per-station map of the same `{ "station", "key", "ts" }` objects, keyed by ICAO |
| `rtl_sdr` | boolean | RTL2832/2836/2838 detected on USB |
| `cpu_temp_c` | number \| null | SoC temperature |
| `throttled` | string \| null | `vcgencmd get_throttled` value, e.g. `"0x0"` |
| `memory_free_kb` | number \| null | `MemAvailable` from `/proc/meminfo` |
| `disk_free_bytes` | number \| null | Free bytes on `/` |
| `setup_episodes` | array | Hotspot episodes since the last successful check-in: `{ "start", "end" }` (`end` is `null` if still in setup) |
| `commands` | object | `{ "fetch": "ok"\|"no_such_key"\|"access_denied"\|"error"\|"skipped", "error"?: string }` |
| `last_command` | object \| null | Last handled command: `{ "id", "action", "acked_at", "result", "reason", "requested_by" }` |
| `pending_command` | object | Present only when a command is deferred on `not_before` |

`last_upload.station` is the ICAO (e.g. `KNKX`). `last_upload.key` is the S3 object key of that mp3 (`raw_atc_stream/<icao>/<filename>.mp3`). `last_upload.ts` is UTC ISO8601. `last_uploads` keeps one entry per station so a Pi that feeds several airports (e.g. KNKX and KMYF) does not overwrite the others.

Example:

```json
{
  "schema_version": 1,
  "pi_id": "nkx-relay",
  "hostname": "nkx-relay",
  "timestamp": "2026-10-23T21:05:00Z",
  "code_version": "34591a5c0f1e2d3a4b5c6d7e8f90123456789ab",
  "uptime_seconds": 86412.3,
  "boot_time": "2026-10-22T21:04:48Z",
  "wifi": {"ssid": "HomeNet", "signal": 72},
  "ip": "192.168.1.50",
  "mode": "online",
  "pi_atc_service": {"active": "active", "sub": "running"},
  "last_upload": {
    "station": "KMYF",
    "key": "raw_atc_stream/KMYF/KMYF_ab12cd.mp3",
    "ts": "2026-10-23T21:02:11Z"
  },
  "last_uploads": {
    "KNKX": {
      "station": "KNKX",
      "key": "raw_atc_stream/KNKX/KNKX_zz99yy.mp3",
      "ts": "2026-10-23T20:58:04Z"
    },
    "KMYF": {
      "station": "KMYF",
      "key": "raw_atc_stream/KMYF/KMYF_ab12cd.mp3",
      "ts": "2026-10-23T21:02:11Z"
    }
  },
  "rtl_sdr": true,
  "cpu_temp_c": 54.2,
  "throttled": "0x0",
  "memory_free_kb": 182448,
  "disk_free_bytes": 11832115200,
  "setup_episodes": [
    {"start": "2026-10-23T18:01:12Z", "end": "2026-10-23T18:14:03Z"}
  ],
  "commands": {"fetch": "ok"},
  "last_command": {
    "id": "11111111-1111-1111-1111-111111111111",
    "action": "reboot",
    "acked_at": "2026-10-23T12:00:02Z",
    "result": "execute",
    "reason": "ok",
    "requested_by": "ownnav"
  }
}
```

### Command file

Write this object to `s3://ownnav-maps/pi-commands/<pi_id>.json`:

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "action": "reboot",
  "not_before": "2026-10-24T10:00:00Z",
  "requested_by": "ownnav"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique id for this command. The Pi persists the last handled id so the same file cannot reboot-loop |
| `action` | yes | Allowlisted. Currently only `"reboot"` |
| `not_before` | no | UTC ISO8601; ignored until that time |
| `requested_by` | no | Echoed in the ack |

After a new `id` is accepted, the Pi writes `pi-commands/<pi_id>.ack.json` (`id`, `action`, `acked_at`, `pi_id`, `requested_by`) and the same info on `last_command` in the status JSON, then reboots. `AccessDenied` and `NoSuchKey` on the command object are logged and reported in `commands.fetch`; they do not fail the check-in. The existing IAM key may only allow `PutObject`.

### IAM

The Pi role/key needs:

```
s3:PutObject  arn:aws:s3:::ownnav-maps/pi-status/*
s3:GetObject  arn:aws:s3:::ownnav-maps/pi-commands/*
s3:PutObject  arn:aws:s3:::ownnav-maps/pi-commands/*.ack.json
```

Plus the existing `PutObject` on `raw_atc_stream/<icao>/*` for audio. Do not put secrets in this repo.

### Server-side alerting

Treat a Pi as offline when `s3://ownnav-maps/pi-status/<pi_id>.json` is missing or its `timestamp` is older than about 15 minutes (three missed 5-minute check-ins). Useful extra alerts: `mode` is `setup`, `pi_atc_service.active` is not `active`, `rtl_sdr` is false, `cpu_temp_c` is high, `throttled` is not `0x0`, or `last_upload.ts` / a `last_uploads.<icao>.ts` is stale.

### Updating an existing Pi

From a copied-over checkout, as root:

```bash
sudo bash pi_setup/deploy_update.sh
```

The script backs up `/opt/pi-atc` and the unit files under `/var/backups/pi-atc/<utc-stamp>/`, installs code, units, timers, journald, and NetworkManager wifi-powersave config, and leaves `/opt/pi-atc/.env` and NetworkManager connection profiles untouched. It restarts `pi-atc.service` only. If watchdog/portal scripts changed, it also restarts `pi-atc-setup.service` (that runs `stop_ap.sh`, which is harmless when not in AP mode).

## Reconfiguration

To change WiFi or station settings after initial setup, connect a monitor and keyboard to the Pi and either:

- Edit `/opt/pi-atc/.env` directly and restart the service:
  ```bash
  sudo systemctl restart pi-atc.service
  ```
- Re-enter setup mode:
  ```bash
  sudo systemctl start pi-atc-setup.service
  ```

## Development

`transcribe_sample.py` is a development tool for testing transcription locally. It uses a [Whisper Large v3 model fine-tuned for ATC audio](https://huggingface.co/jacktol/whisper-large-v3-finetuned-for-ATC) and requires a machine with a GPU or Apple Silicon. It is not intended to run on the Pi.

```bash
python3 -m unittest discover -s tests -v
shellcheck pi_setup/*.sh
```

```bash
pip install -r requirements.txt
python transcribe_sample.py [sample_file]
```

If no sample file is specified, a random file from `./samples/` is used. Audio is preprocessed with ffmpeg (bandpass filter, noise reduction, loudness normalization) before transcription unless `--raw` is passed.

## System Dependencies (installed by install.sh)

- `hostapd` -- WiFi access point
- `dnsmasq` -- DHCP and DNS for AP mode
- `python3-flask` -- Captive portal web server
- `python3-dotenv` -- .env file parsing
- `python3-numpy` -- Audio fingerprinting
- `python3-boto3` -- S3 uploads
- `rtl-sdr` -- RTL-SDR driver and utilities
- `ffmpeg` -- Audio recording and MP3 conversion
- `iptables` -- Traffic redirection for captive portal
