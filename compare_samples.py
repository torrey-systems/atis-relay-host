#!/usr/bin/env python3
import sys
import os
import json
import math
import subprocess

BANDS = 64
COSINE_THRESHOLD = 0.15

def get_spectrum(path, bands=BANDS):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-ac", "1", "-ar", "16000",
        "-af", f"aspectralstats=win_size=1024:measure=mean",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)

    cmd = [
        "ffprobe", "-hide_banner",
        "-i", path,
        "-show_entries", "format=duration",
        "-of", "json"
    ]
    dur_res = subprocess.run(cmd, capture_output=True, check=False)
    duration = 0
    try:
        duration = float(json.loads(dur_res.stdout)["format"]["duration"])
    except Exception:
        pass

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-ac", "1", "-ar", "16000",
        "-filter_complex", f"showfreqs=s=1x{bands}:mode=bar:fscale=lin,format=gray8",
        "-frames:v", "1",
        "-f", "rawvideo", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)

    if len(res.stdout) >= bands:
        raw = list(res.stdout[:bands])
        log_bands = [math.log1p(v) for v in raw]
        norm = math.sqrt(sum(v**2 for v in log_bands)) + 1e-8
        return [v / norm for v in log_bands], duration

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-ac", "1", "-ar", "16000",
        "-af", f"astats=metadata=1:measure_perchannel=RMS_level+Peak_level",
        "-f", "null", "-"
    ]
    subprocess.run(cmd, capture_output=True, check=False)
    return None, duration

def compute_features_raw(path, bands=BANDS):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", path, "-ac", "1", "-ar", "16000",
        "-t", "10",
        "-f", "s16le", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)
    if not res.stdout:
        return None, 0

    import struct
    n = len(res.stdout) // 2
    samples = struct.unpack(f"<{n}h", res.stdout[:n*2])
    audio = [s / 32768.0 for s in samples]
    duration_sec = n / 16000.0

    win = 1024
    hop = 512

    if len(audio) < win:
        audio = audio + [0.0] * (win - len(audio))

    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (win - 1)) for i in range(win)]

    n_bins = win // 2 + 1
    avg_mag = [0.0] * n_bins
    n_frames = 0

    for start in range(0, len(audio) - win + 1, hop):
        real = [0.0] * n_bins
        imag = [0.0] * n_bins
        for k in range(n_bins):
            ang_step = -2.0 * math.pi * k / win
            for j in range(win):
                val = audio[start + j] * window[j]
                angle = ang_step * j
                real[k] += val * math.cos(angle)
                imag[k] += val * math.sin(angle)
        for k in range(n_bins):
            avg_mag[k] += math.sqrt(real[k]**2 + imag[k]**2)
        n_frames += 1

    if n_frames == 0:
        return None, duration_sec

    avg_mag = [v / n_frames for v in avg_mag]

    band_size = n_bins // bands
    band_means = []
    for b in range(bands):
        s = b * band_size
        e = s + band_size if b < bands - 1 else n_bins
        band_means.append(sum(avg_mag[s:e]) / max(1, e - s))

    band_means = [math.log1p(v) for v in band_means]
    norm_val = math.sqrt(sum(v**2 for v in band_means)) + 1e-8
    return [v / norm_val for v in band_means], duration_sec

def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <file1> <file2> [file3 ...]")
        sys.exit(1)

    files = sys.argv[1:]
    features = {}

    print("Loading (first 10s of each file)...")
    for path in files:
        feat, dur = compute_features_raw(path)
        if feat is None:
            print(f"  FAILED: {os.path.basename(path)}")
            sys.exit(1)
        features[path] = feat
        print(f"  {os.path.basename(path)}: {dur:.1f}s")

    print()
    threshold = 1 - COSINE_THRESHOLD
    print(f"Threshold: {threshold:.2f} (COSINE_THRESHOLD={COSINE_THRESHOLD})")
    print()
    for i, a in enumerate(files):
        for b in files[i+1:]:
            sim = cosine_similarity(features[a], features[b])
            would_skip = sim >= threshold
            label = "SKIP" if would_skip else "UPLOAD"
            print(f"  {sim:.4f}  {label}  {os.path.basename(a)} vs {os.path.basename(b)}")

if __name__ == "__main__":
    main()
