#!/usr/bin/env python3
"""Render hicolor icons from the official ATIS Relay square.

The Figma SVG export embeds a taller PNG and a pattern transform. librsvg
treats that as a zoomed crop of the plane. The attached 256px render is the
full square as drawn (white plane and leaf trail on a dark background).

Every PNG size and the scalable SVG is that square scaled uniformly. There
is no extra crop or rotation.
"""
import base64
import io
import os
import shutil
import subprocess
import sys

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE = os.path.join(ROOT, "packaging", "icons", "source", "atis-relay.png")
SIZES = (16, 32, 48, 64, 128, 256, 512)


def save_png(image, path):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    with open(path, "wb") as handle:
        handle.write(buf.getvalue())


def write_square_svg(png_path, svg_path, side):
    png_bytes = open(png_path, "rb").read()
    b64 = base64.b64encode(png_bytes).decode("ascii")
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)
    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="%s" height="%s" viewBox="0 0 %s %s">
  <image x="0" y="0" width="%s" height="%s" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,%s" xlink:href="data:image/png;base64,%s"/>
</svg>
""" % (side, side, side, side, side, side, b64, b64)
    with open(svg_path, "w") as handle:
        handle.write(svg)


def hicolor_png(size):
    return os.path.join(
        ROOT,
        "packaging",
        "icons",
        "hicolor",
        "%sx%s" % (size, size),
        "apps",
        "atis-relay.png",
    )


def main():
    if not os.path.isfile(SOURCE):
        print("missing source icon %s" % SOURCE, file=sys.stderr)
        return 1

    source = Image.open(SOURCE).convert("RGBA")

    if source.size[0] != source.size[1]:
        print("source icon must be square, got %s" % (source.size,), file=sys.stderr)
        return 1

    clean = Image.new("RGBA", source.size)
    clean.putdata(list(source.getdata()))
    png_256 = hicolor_png(256)
    os.makedirs(os.path.dirname(png_256), exist_ok=True)
    shutil.copyfile(SOURCE, png_256)

    docs = os.path.join(ROOT, "docs", "images")
    os.makedirs(docs, exist_ok=True)
    shutil.copyfile(SOURCE, os.path.join(docs, "logo.png"))

    svg_path = os.path.join(ROOT, "packaging", "icons", "hicolor", "scalable", "apps", "atis-relay.svg")
    write_square_svg(SOURCE, svg_path, source.size[0])

    for size in SIZES:
        dest = hicolor_png(size)

        if size == source.size[0]:
            continue

        rendered = None
        rsvg = shutil.which("rsvg-convert")

        if rsvg:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            result = subprocess.run(
                [rsvg, "-w", str(size), "-h", str(size), svg_path, "-o", dest],
                check=False,
            )

            if result.returncode == 0 and os.path.isfile(dest):
                rendered = Image.open(dest).convert("RGBA")

                if rendered.size != (size, size):
                    rendered = None

        if rendered is None:
            rendered = clean.resize((size, size), Image.Resampling.LANCZOS)
            save_png(rendered, dest)

        else:
            save_png(rendered, dest)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
