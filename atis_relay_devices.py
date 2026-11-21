#!/usr/bin/env python3
import fcntl
import os
import re
import shutil
import subprocess
import tempfile
import wave

RTL_VENDOR = "0bda"
RTL_PRODUCTS = {"2832", "2836", "2838"}
USB_DEVICES_DIR = "/sys/bus/usb/devices"
USB_NODES_DIR = "/dev/bus/usb"
USB_ROOT_HUB_RE = re.compile(r"^usb(\d+)$")
USBDEVFS_RESET = 0x5514
V4_MANUFACTURER = "RTLSDRBlog"
V4_PRODUCT = "Blog V4"
V4_DRIVER_MARK = b"Blog V4"
PRIVATE_RTL_DIR = "/opt/pi-atc/rtl-sdr"
LIBRTLSDR_RE = re.compile(r"librtlsdr\S*\s+=>\s+(\S+)")


def usb_root_hubs(root=USB_DEVICES_DIR):
    try:
        names = os.listdir(root)

    except OSError:
        return []

    hubs = [name for name in names if USB_ROOT_HUB_RE.match(name)]
    return sorted(hubs, key=lambda name: int(USB_ROOT_HUB_RE.match(name).group(1)))


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


def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()

    except OSError:
        return ""


def rtl_sdr_devices(root=USB_DEVICES_DIR):
    found = []

    try:
        names = sorted(os.listdir(root))

    except OSError:
        return found

    for name in names:
        base = os.path.join(root, name)
        vendor = _read_text(os.path.join(base, "idVendor")).lower()
        product = _read_text(os.path.join(base, "idProduct")).lower()

        if vendor != RTL_VENDOR or product not in RTL_PRODUCTS:
            continue

        found.append({
            "name": name,
            "manufacturer": _read_text(os.path.join(base, "manufacturer")),
            "product": _read_text(os.path.join(base, "product")),
            "busnum": _read_text(os.path.join(base, "busnum")),
            "devnum": _read_text(os.path.join(base, "devnum")),
        })

    return found


def rtl_sdr_v4_present(root=USB_DEVICES_DIR):
    for item in rtl_sdr_devices(root):
        if item["manufacturer"] == V4_MANUFACTURER and item["product"] == V4_PRODUCT:
            return True

    return False


def private_rtl_fm():
    return os.path.join(PRIVATE_RTL_DIR, "bin", "rtl_fm")


def rtl_fm_path():
    private = private_rtl_fm()

    if os.path.isfile(private) and os.access(private, os.X_OK):
        return private

    return "rtl_fm"


def rtl_fm_library(binary):
    res = _run(["ldd", binary])

    if res is None or res.returncode != 0:
        return ""

    match = LIBRTLSDR_RE.search(res.stdout or "")
    return match.group(1) if match else ""


def rtl_fm_supports_v4():
    binary = rtl_fm_path()

    if binary == private_rtl_fm():
        return True

    binary = shutil.which(binary)

    if not binary:
        return None

    library = rtl_fm_library(binary)

    if not library:
        return None

    try:
        with open(library, "rb") as f:
            return V4_DRIVER_MARK in f.read()

    except OSError:
        return None


def v4_driver_missing():
    return rtl_sdr_v4_present() and rtl_fm_supports_v4() is False


def reset_rtl_sdr(root=USB_DEVICES_DIR, nodes=USB_NODES_DIR):
    done = False

    for item in rtl_sdr_devices(root):
        try:
            node = os.path.join(nodes, "%03d" % int(item["busnum"]), "%03d" % int(item["devnum"]))
            fd = os.open(node, os.O_WRONLY)

        except (OSError, ValueError):
            continue

        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            done = True

        except OSError:
            pass

        finally:
            os.close(fd)

    return done


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
                "timeout %s %s -f %sM -M am -s 24000 -r %s - | "
                "ffmpeg -hide_banner -loglevel error -y "
                "-t %s -f s16le -ar %s -ac 1 -i - %s"
            ) % (seconds + 8, rtl_fm_path(), float(freq), sample_rate, seconds, sample_rate, path)
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
