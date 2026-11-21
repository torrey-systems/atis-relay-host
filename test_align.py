#!/usr/bin/env python3
import subprocess
import struct
import math
import os
import sys

SR = 8000
WIN_MS = 100

def raw_envelope(path):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    res = subprocess.run(cmd, capture_output=True, check=False)
    n = len(res.stdout) // 2
    samples = struct.unpack(f"<{n}h", res.stdout[:n * 2])
    win = SR * WIN_MS // 1000
    env = []
    for start in range(0, len(samples) - win + 1, win):
        acc = 0.0
        for i in range(start, start + win):
            acc += samples[i] * samples[i]
        env.append(math.sqrt(acc / win))
    return env

def znorm(env):
    mean = sum(env) / len(env)
    std = math.sqrt(sum((v - mean) ** 2 for v in env) / len(env)) + 1e-8
    return [(v - mean) / std for v in env]

def find_loop_start(env):
    """Index just after the end of the quietest sustained run."""
    lo = min(env)
    hi = max(env)
    thresh = lo + 0.15 * (hi - lo)
    quiet = [v <= thresh for v in env]

    best_len = 0
    best_end = 0
    run = 0
    n = len(env)
    for i in range(2 * n):
        if quiet[i % n]:
            run += 1
            if run > best_len:
                best_len = run
                best_end = i
        else:
            run = 0
    return (best_end + 1) % n, best_len

def rotate(env, start):
    return env[start:] + env[:start]

def corr(a, b):
    n = min(len(a), len(b))
    a = znorm(a[:n])
    b = znorm(b[:n])
    return sum(a[i] * b[i] for i in range(n)) / n

def corr_with_jitter(a, b, max_lag=5):
    """Correlation allowing +/- max_lag windows of misalignment."""
    best = -1.0
    n = min(len(a), len(b))
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            aa = a[lag:n]
            bb = b[0:n - lag]
        else:
            aa = a[0:n + lag]
            bb = b[-lag:n]
        m = min(len(aa), len(bb))
        if m < 10:
            continue
        best = max(best, corr(aa[:m], bb[:m]))
    return best

def circular_xcorr(a, b):
    a = znorm(a)
    b = znorm(b)
    n = min(len(a), len(b))
    a = a[:n]
    b = b[:n]
    best = -1.0
    for lag in range(n):
        acc = sum(a[i] * b[(i + lag) % n] for i in range(n))
        best = max(best, acc / n)
    return best

def parse_pairs(argv):
    if len(argv) < 3 or len(argv) % 3 != 0:
        return None

    pairs = []

    for i in range(0, len(argv), 3):
        pairs.append((argv[i], argv[i + 1], argv[i + 2]))

    return pairs


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    pairs = parse_pairs(argv)

    if pairs is None:
        print("usage: test_align.py LABEL FILE1 FILE2 [LABEL FILE1 FILE2 ...]", file=sys.stderr)
        return 2

    for label, f1, f2 in pairs:
        if not (os.path.exists(f1) and os.path.exists(f2)):
            print(f"{label}: files missing, skipped")
            continue

        e1 = raw_envelope(f1)
        e2 = raw_envelope(f2)

        s1, len1 = find_loop_start(e1)
        s2, len2 = find_loop_start(e2)
        r1 = rotate(e1, s1)
        r2 = rotate(e2, s2)

        aligned = corr_with_jitter(r1, r2)
        circ = circular_xcorr(e1, e2)

        print(f"{label}:")
        print(f"  silence run: {len1} and {len2} windows ({len1*WIN_MS/1000:.1f}s / {len2*WIN_MS/1000:.1f}s)")
        print(f"  silence-aligned corr: {aligned:.4f}")
        print(f"  circular xcorr (old): {circ:.4f}")
        print()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
