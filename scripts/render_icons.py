#!/usr/bin/env python3
"""Render hicolor PNG sizes from the official 256px ATIS Relay logo.

The attached SVG export does not match the 256px render (white plane with a
leaf-like trail on a dark square). PNG sizes and the scalable SVG are built
from that 256px render so the menu, window, and README icons match.
"""
import base64
import io
import os
import sys

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE = os.path.join(ROOT, "packaging", "icons", "hicolor", "256x256", "apps", "atis-relay.png")
SIZES = (16, 32, 48, 64, 128, 256, 512)


def save_png(image, path):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    with open(path, "wb") as handle:
        handle.write(buf.getvalue())


def main():
    if not os.path.isfile(SOURCE):
        print("missing source icon %s" % SOURCE, file=sys.stderr)
        return 1

    source = Image.open(SOURCE).convert("RGBA")
    clean = Image.new("RGBA", source.size)
    clean.putdata(list(source.getdata()))

    for size in SIZES:
        if size == source.size[0]:
            out = clean

        else:
            out = clean.resize((size, size), Image.Resampling.LANCZOS)

        dest = os.path.join(
            ROOT,
            "packaging",
            "icons",
            "hicolor",
            "%sx%s" % (size, size),
            "apps",
            "atis-relay.png",
        )
        save_png(out, dest)

    docs = os.path.join(ROOT, "docs", "images")
    os.makedirs(docs, exist_ok=True)
    save_png(clean, os.path.join(docs, "logo.png"))
    png_512 = os.path.join(ROOT, "packaging", "icons", "hicolor", "512x512", "apps", "atis-relay.png")
    png_bytes = open(png_512, "rb").read()
    b64 = base64.b64encode(png_bytes).decode("ascii")
    svg_dir = os.path.join(ROOT, "packaging", "icons", "hicolor", "scalable", "apps")
    os.makedirs(svg_dir, exist_ok=True)
    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="512" height="512" viewBox="0 0 512 512">
  <image width="512" height="512" href="data:image/png;base64,%s" xlink:href="data:image/png;base64,%s"/>
</svg>
""" % (b64, b64)
    with open(os.path.join(svg_dir, "atis-relay.svg"), "w") as handle:
        handle.write(svg)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
