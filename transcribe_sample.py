#!/usr/bin/env python3
import os
import sys
import argparse
import random
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
SAMPLES_DIR = ROOT / "samples"
CLEAN_DIR = ROOT / "samples_clean"
MODEL_ID = "jacktol/whisper-large-v3-finetuned-for-ATC"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm"}

def pick_sample(name: str | None) -> Path:
    if name:
        path = SAMPLES_DIR / name if not os.path.isabs(name) else Path(name)
        if not path.exists():
            print(f"[error] sample not found: {path}")
            sys.exit(1)
        return path

    candidates = [p for p in SAMPLES_DIR.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    if not candidates:
        print(f"[error] no audio samples found in {SAMPLES_DIR}")
        sys.exit(1)
    return random.choice(candidates)

def preprocess_audio(src: Path) -> Path:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    out = CLEAN_DIR / (src.stem + ".clean.wav")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out

    af = (
        "highpass=f=250,"
        "lowpass=f=3400,"
        "afftdn=nr=12:nf=-25,"
        "dynaudnorm=f=200:g=15,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-ac", "1",
        "-ar", "16000",
        "-af", af,
        str(out),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        print(f"[error] ffmpeg preprocess failed: {res.stderr.decode(errors='ignore')}")
        sys.exit(1)
    return out

_pipe = None
def get_pipeline():
    global _pipe
    if _pipe is not None:
        return _pipe

    import torch
    from transformers import pipeline

    if torch.cuda.is_available():
        device = "cuda:0"
        dtype = torch.float16
    elif torch.backends.mps.is_available():
        device = "mps"
        dtype = torch.float16
    else:
        device = "cpu"
        dtype = torch.float32

    print(f"[model] loading {MODEL_ID} on {device} ({dtype})")
    _pipe = pipeline(
        task="automatic-speech-recognition",
        model=MODEL_ID,
        torch_dtype=dtype,
        device=device,
        chunk_length_s=30,
        return_timestamps=False,
    )
    return _pipe

def main():
    parser = argparse.ArgumentParser(description="Transcribe a sample with an ATC-finetuned Whisper model")
    parser.add_argument("sample", nargs="?", help="sample filename in ./samples or absolute path; random if omitted")
    parser.add_argument("--raw", action="store_true", help="skip audio pre-processing")
    args = parser.parse_args()

    path = pick_sample(args.sample)
    print(f"[transcribe] file={path}")

    send_path = path if args.raw else preprocess_audio(path)
    if not args.raw:
        print(f"[transcribe] preprocessed={send_path}")

    pipe = get_pipeline()
    out = pipe(str(send_path), generate_kwargs={"language": "en", "task": "transcribe"})
    text = out["text"] if isinstance(out, dict) else str(out)
    print(text.strip())

if __name__ == "__main__":
    main()
