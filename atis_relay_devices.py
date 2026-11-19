#!/usr/bin/env python3
import os
import re
import subprocess
import tempfile
import wave

RTL_VENDOR = "0bda"
RTL_PRODUCTS = {"2832", "2836", "2838"}


def _run(args, timeout=8):
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


def rtl_sdr_present():
    res = _run(["lsusb"])

    if res is None or res.returncode != 0:
        return False

    for line in (res.stdout or "").splitlines():
        lower = line.lower()

        if RTL_VENDOR not in lower:
            continue

        for product in RTL_PRODUCTS:
            if product in lower:
                return True

    return False


def list_alsa_capture():
    devices = []
    res = _run(["arecord", "-l"])

    if res is None or res.returncode != 0:
        return devices

    for line in (res.stdout or "").splitlines():
        match = re.match(r"^card (\d+):\s+(\S+)\s+\[([^\]]+)\],\s+device (\d+):\s+([^\[]+)", line)

        if not match:
            continue

        card, ident, name, device, device_name = match.groups()
        alsa_id = "plughw:%s,%s" % (card, device)
        label = "USB audio: %s" % name.strip()
        devices.append({
            "id": alsa_id,
            "label": label,
            "kind": "alsa",
            "detail": "%s %s" % (ident, device_name.strip()),
        })

    return devices


def list_capture_devices():
    devices = []

    if rtl_sdr_present():
        devices.append({
            "id": "sdr",
            "label": "USB radio (RTL-SDR)",
            "kind": "sdr",
            "detail": "Tunes aviation frequencies",
        })

    else:
        devices.append({
            "id": "sdr",
            "label": "USB radio (RTL-SDR), plug it in",
            "kind": "sdr",
            "detail": "Not detected yet",
        })

    devices.extend(list_alsa_capture())
    return devices


def wav_peak_level(path):
    try:
        with wave.open(path, "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            width = handle.getsampwidth()

    except (OSError, wave.Error):
        return 0.0

    if not frames or width != 2:
        return 0.0

    peak = 0

    for i in range(0, len(frames) - 1, 2):
        sample = int.from_bytes(frames[i:i + 2], "little", signed=True)
        value = abs(sample)

        if value > peak:
            peak = value

    return min(1.0, peak / 32767.0)


def test_capture(device, kind="sdr", freq="126.9", seconds=5, sample_rate=16000):
    fd, path = tempfile.mkstemp(prefix="atis-relay-test-", suffix=".wav")
    os.close(fd)
    ok = False
    error = ""

    try:
        if kind == "alsa":
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "alsa",
                "-i", device,
                "-ac", "1",
                "-ar", str(sample_rate),
                "-t", str(seconds),
                path,
            ]
            res = _run(cmd, timeout=seconds + 8)

        else:
            cmd = (
                "timeout %s rtl_fm -f %sM -M am -s 24000 -r %s - | "
                "ffmpeg -hide_banner -loglevel error -y "
                "-t %s -f s16le -ar %s -ac 1 -i - %s"
            ) % (seconds + 8, float(freq), sample_rate, seconds, sample_rate, path)
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=seconds + 12,
                check=False,
            )

        if res is None:
            error = "Could not start a test recording."

        elif not os.path.exists(path) or os.path.getsize(path) < 1000:
            err = ""

            if res.stderr:
                err = str(res.stderr)[-200:]

            error = err or "No sound was captured. Check the USB plug and try again."

        else:
            ok = True
            play = _run(["aplay", "-q", path], timeout=seconds + 8)

            if play is not None and play.returncode != 0:
                error = "Recorded a clip but could not play it back. Check the speakers."

        level = wav_peak_level(path) if ok else 0.0
        return ok, level, error

    except (OSError, subprocess.SubprocessError, ValueError):
        return False, 0.0, "Could not run the test. Plug in the device and try again."

    finally:
        try:
            os.remove(path)

        except OSError:
            pass
