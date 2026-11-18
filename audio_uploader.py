#!/usr/bin/env python3
import os
import sys
import time
import tempfile
import subprocess
import struct
import math
from atis_relay_status import suggestion_for_error, write_status
from pi_atc_config import (
    apply_user_paths,
    atis_relay_url,
    load_config,
    parse_stations,
    station_token_for,
    upload_mode,
)
from pi_atc_state import derive_pi_id, record_last_upload, utc_now_iso

WAIT_SEC = 60

apply_user_paths()
load_config()

STATIONS = parse_stations(os.environ.get("STATIONS", ""))

AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", ":0" if sys.platform == "darwin" else "plughw:1,0")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
CHUNK_SEC = int(os.environ.get("CHUNK_SEC", "60"))
BITS = 16
CHANNELS = 1

SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.7"))

PI_ID = derive_pi_id()
TMP_DIR = os.environ.get("TMP_DIR", "/tmp/audio_uploader")

ENVELOPE_SR = 8000
ENVELOPE_WIN_MS = 100
MIN_WAV_BYTES = SAMPLE_RATE * (BITS // 8) * CHANNELS * CHUNK_SEC // 10

def find_loop_start(env):
    """Index just after the longest quiet run, treating the recording as circular.

    ATIS repeats on a loop with a silence gap between message repeats; the end
    of that gap marks the start of the message.
    """
    lo = min(env)
    hi = max(env)
    thresh = lo + 0.15 * (hi - lo)
    quiet = [v <= thresh for v in env]

    n = len(env)
    best_len = 0
    best_end = 0
    run = 0
    for i in range(2 * n):
        if quiet[i % n]:
            run += 1
            if run > best_len:
                best_len = run
                best_end = i
        else:
            run = 0
    return (best_end + 1) % n

def wav_to_envelope(path):
    """Loudness envelope (RMS per 100ms window), rotated to start at the loop boundary."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path, "-ac", "1", "-ar", str(ENVELOPE_SR),
        "-f", "s16le", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)
    if not res.stdout:
        return None

    n = len(res.stdout) // 2
    samples = struct.unpack(f"<{n}h", res.stdout[:n * 2])

    win = ENVELOPE_SR * ENVELOPE_WIN_MS // 1000
    env = []
    for start in range(0, len(samples) - win + 1, win):
        acc = 0.0
        for i in range(start, start + win):
            acc += samples[i] * samples[i]
        env.append(math.sqrt(acc / win))

    if not env:
        return None

    start = find_loop_start(env)
    return env[start:] + env[:start]

def _znorm_corr(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    a = a[:n]
    b = b[:n]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    std_a = math.sqrt(sum((v - mean_a) ** 2 for v in a) / n) + 1e-8
    std_b = math.sqrt(sum((v - mean_b) ** 2 for v in b) / n) + 1e-8
    return sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / (n * std_a * std_b)

def compare_envelopes(a, b, max_lag=5):
    """Correlation of loop-aligned envelopes, allowing small residual misalignment."""
    if a is None or b is None:
        return 0.0

    n = min(len(a), len(b))
    if n < 2 * max_lag + 10:
        return 0.0

    best = -1.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            best = max(best, _znorm_corr(a[lag:], b))
        else:
            best = max(best, _znorm_corr(a, b[-lag:]))
    return max(0.0, min(1.0, best))

TEMP_SHUTDOWN_C = float(os.environ.get("TEMP_SHUTDOWN_C", "78"))
TEMP_RESUME_C = float(os.environ.get("TEMP_RESUME_C", "75"))
THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
USB_AUTH_PATHS = [
    "/sys/bus/usb/devices/usb1/authorized",
    "/sys/bus/usb/devices/usb2/authorized",
]

def read_cpu_temp():
    try:
        with open(THERMAL_PATH) as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None

USB_DRIVER_UNBIND = "/sys/bus/usb/drivers/usb/unbind"
USB_DRIVER_BIND = "/sys/bus/usb/drivers/usb/bind"

def set_usb_power(on):
    if os.geteuid() != 0:
        return

    if not on:
        for path in USB_AUTH_PATHS:
            try:
                with open(path, "w") as f:
                    f.write("0")
            except Exception:
                pass
        return

    for path in USB_AUTH_PATHS:
        try:
            with open(path, "w") as f:
                f.write("1")
        except Exception:
            pass

    for hub in ["usb1", "usb2"]:
        try:
            with open(USB_DRIVER_UNBIND, "w") as f:
                f.write(hub)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            with open(USB_DRIVER_BIND, "w") as f:
                f.write(hub)
        except Exception:
            pass

def record_wav(out_path: str, freq_mhz: str) -> bool:
    kind = os.environ.get("CAPTURE_KIND", "sdr").strip().lower()
    device = os.environ.get("AUDIO_DEVICE", AUDIO_DEVICE)

    if kind == "alsa":
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "alsa",
            "-i", device,
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-t", str(CHUNK_SEC),
            out_path,
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            return res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > MIN_WAV_BYTES

        except Exception:
            return False

    if sys.platform == "darwin":
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "avfoundation",
            "-i", AUDIO_DEVICE,
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-t", str(CHUNK_SEC),
            out_path,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            return res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > MIN_WAV_BYTES
        except Exception:
            return False

    freq = float(freq_mhz)
    cmd = (
        f"timeout {CHUNK_SEC + 10} rtl_fm -f {freq}M -M am -s 24000 -r {SAMPLE_RATE} - | "
        f"ffmpeg -hide_banner -loglevel error -y "
        f"-t {CHUNK_SEC} -f s16le -ar {SAMPLE_RATE} -ac 1 -i - {out_path}"
    )
    try:
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if res.returncode != 0:
            err = res.stderr.decode(errors="ignore").strip()
            if err:
                print(f"[capture] {err[-300:]}")
        return os.path.exists(out_path) and os.path.getsize(out_path) > MIN_WAV_BYTES
    except Exception as e:
        print(f"[capture] {e}")
        return False

bucket = "ownnav-maps"
prefix = "raw_atc_stream/"
_s3 = None
_rejected_tokens = {}


def s3_client():
    global _s3

    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))

    return _s3


def wait_for_config():
    global STATIONS

    while True:
        load_config()
        STATIONS = parse_stations(os.environ.get("STATIONS", ""))
        mode = upload_mode()

        if STATIONS and mode != "none":
            return mode

        message, fix = suggestion_for_error("missing_key")
        write_status(state="waiting", error=message, fix=fix)

        if not STATIONS:
            print("[wait] no stations configured; open the ATIS Relay app")

        else:
            print("[wait] station token not configured; open the ATIS Relay app")

        time.sleep(WAIT_SEC)


def upload_file_s3(mp3_path: str, icao: str) -> str | None:
    key = prefix + icao + "/" + os.path.basename(mp3_path)

    try:
        s3_client().upload_file(mp3_path, bucket, key)

    except Exception as e:
        print(f"[error] Failed to upload file: {e}")
        return None

    url = f"https://{bucket}.s3.amazonaws.com/{key}"
    print(f"[upload] {url}")
    return key


def upload_file_http(mp3_path: str, icao: str) -> str | None:
    from pi_atc_http import AuthRejected, ClientError, Forbidden, TransientError, upload_audio

    token = station_token_for(icao)

    if not token:
        message, fix = suggestion_for_error("missing_key")
        write_status(state="error", error=message, fix=fix)
        print("[wait] %s: station token not configured; open the ATIS Relay app" % icao)
        return None

    if _rejected_tokens.get(icao) == token:
        message, fix = suggestion_for_error("bad_key")
        write_status(state="error", error=message, fix=fix)
        print("[wait] %s: station token rejected; open the ATIS Relay app" % icao)
        return None

    filename = os.path.basename(mp3_path)
    recorded_at = utc_now_iso()

    try:
        key, _body = upload_audio(
            atis_relay_url(),
            token,
            icao,
            mp3_path,
            recorded_at,
            filename=filename,
        )
        print(f"[upload] {icao} {filename}")
        write_status(state="listening", last_upload=utc_now_iso())
        return key

    except AuthRejected as exc:
        _rejected_tokens[icao] = token
        message, fix = suggestion_for_error("bad_key")
        write_status(state="error", error=message, fix=fix)
        print("[error] %s: %s; open the ATIS Relay app to paste a new key" % (icao, exc))
        return None

    except Forbidden as exc:
        _rejected_tokens[icao] = token
        message, fix = suggestion_for_error("wrong_airport")
        write_status(state="error", error=message, fix=fix)
        print("[error] %s: %s; open the ATIS Relay app to paste this airport's key" % (icao, exc))
        return None

    except TransientError as exc:
        message, fix = suggestion_for_error("upload_failed")
        write_status(state="error", error=message, fix=fix)
        print(f"[error] {icao}: upload retry exhausted: {exc}")
        return None

    except ClientError as exc:
        kind = "upload_failed"

        if getattr(exc, "status_code", None) == 413:
            kind = "too_large"

        if getattr(exc, "status_code", None) == 429:
            kind = "rate_limited"

        message, fix = suggestion_for_error(kind)
        write_status(state="error", error=message, fix=fix)
        print(f"[error] {icao}: {exc}")
        return None


def upload_file(path: str, icao: str) -> str | None:
    mp3_path = os.path.splitext(path)[0] + ".mp3"
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", path,
            "-acodec", "libmp3lame",
            "-b:a", "64k",
            mp3_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if res.returncode != 0 or not os.path.exists(mp3_path):
            print("[error] ffmpeg conversion failed")
            return None

        mode = upload_mode()

        if mode == "http":
            return upload_file_http(mp3_path, icao)

        if mode == "s3":
            return upload_file_s3(mp3_path, icao)

        print("[wait] %s: station token not configured; open the ATIS Relay app" % icao)
        return None
    except Exception as e:
        print(f"[error] Failed to upload file: {e}")
        return None
    finally:
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except Exception:
            pass

def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    max_stale_sec = 1800
    usb_disabled = False
    thermal_recovery = False
    global STATIONS

    wait_for_config()

    station_state = {}
    for icao, freq in STATIONS:
        station_state[icao] = {"last_upload": 0.0, "prev_envelope": None}

    station_idx = 0

    while True:
        temp = read_cpu_temp()
        if temp is not None:
            print(f"[temp] {temp:.1f}°C")

        if temp is not None and temp >= TEMP_SHUTDOWN_C and not usb_disabled:
            print(f"[temp] {temp:.1f}°C exceeds {TEMP_SHUTDOWN_C}°C, disabling USB")
            set_usb_power(False)
            usb_disabled = True

        if usb_disabled:
            if temp is not None and temp <= TEMP_RESUME_C:
                print(f"[temp] {temp:.1f}°C below {TEMP_RESUME_C}°C, re-enabling USB")
                set_usb_power(True)
                usb_disabled = False
                thermal_recovery = True
                time.sleep(5)
            else:
                time.sleep(30)
                continue

        load_config()
        STATIONS = parse_stations(os.environ.get("STATIONS", ""))

        if not STATIONS:
            message, fix = suggestion_for_error("missing_key")
            write_status(state="waiting", error=message, fix=fix)
            print("[wait] no stations configured; open the ATIS Relay app")
            time.sleep(WAIT_SEC)
            continue

        if station_idx >= len(STATIONS):
            station_idx = 0

        for sid, _freq in STATIONS:
            station_state.setdefault(sid, {"last_upload": 0.0, "prev_envelope": None})

        icao, freq = STATIONS[station_idx]
        print(f"[station] {icao} @ {freq} MHz")
        write_status(state="listening")

        with tempfile.NamedTemporaryFile(prefix=f"{icao}_", suffix=".wav", dir=TMP_DIR, delete=False) as tf:
            wav_path = tf.name

        ok = record_wav(wav_path, freq)
        if not ok:
            if thermal_recovery and os.geteuid() == 0:
                print("[temp] recording failed after USB re-enable, rebooting")
                subprocess.run(["reboot"], check=False)
                time.sleep(120)
            message, fix = suggestion_for_error("capture_failed")
            write_status(state="error", error=message, fix=fix)
            print(f"[capture] recording failed for {icao}; retrying in 3s")
            try:
                os.remove(wav_path)
            except Exception:
                pass
            time.sleep(3)
            continue

        thermal_recovery = False

        try:
            st = station_state[icao]
            env = wav_to_envelope(wav_path)
            sim = compare_envelopes(st["prev_envelope"], env)
            stale = st["last_upload"] > 0 and (time.time() - st["last_upload"]) >= max_stale_sec
            print(f"[compare] {icao} similarity={sim:.4f} threshold={SIMILARITY_THRESHOLD} stale={stale}")

            if st["prev_envelope"] is not None and sim >= SIMILARITY_THRESHOLD and not stale:
                print(f"[skip] {icao} audio too similar to previous chunk")
                st["prev_envelope"] = env
            else:
                write_status(state="uploading")
                key = upload_file(wav_path, icao)
                if key:
                    st["prev_envelope"] = env
                    st["last_upload"] = time.time()
                    try:
                        record_last_upload(icao, key)
                    except Exception:
                        pass
                else:
                    print(f"[info] {icao} upload failed")

        except Exception as e:
            print(f"[error] {e}")
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass

        station_idx = (station_idx + 1) % len(STATIONS)
        time.sleep(60)

if __name__ == "__main__":
    main()
