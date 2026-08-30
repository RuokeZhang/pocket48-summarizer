#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def has_chromatic_pixels(image: Image.Image) -> bool:
    return any(
        alpha > 0 and (red != green or green != blue)
        for red, green, blue, alpha in image.get_flattened_data()
    )


if not features.check_module("freetype2"):
    fail("Pillow FreeType support is required for color emoji.")
if not features.check_feature("raqm"):
    fail("Pillow RAQM support is required for emoji sequences.")

result = subprocess.run(
    ["fc-match", "--format=%{file}\n", "Noto Color Emoji"],
    capture_output=True,
    text=True,
    check=False,
)
font_path = Path(result.stdout.splitlines()[0].strip()) if result.stdout else None
if result.returncode != 0 or font_path is None or not font_path.is_file():
    fail("Noto Color Emoji is unavailable.")
scan = subprocess.run(
    ["fc-scan", "--format=%{family}\n", str(font_path)],
    capture_output=True,
    text=True,
    check=False,
)
if scan.returncode != 0 or "Noto Color Emoji" not in scan.stdout:
    fail("fontconfig did not resolve Noto Color Emoji to the expected family.")

try:
    font = ImageFont.truetype(
        str(font_path),
        109,
        layout_engine=ImageFont.Layout.RAQM,
    )
except OSError as exc:
    fail(f"Noto Color Emoji cannot be loaded at its native strike: {exc}")

for index, value in enumerate(("🎉", "👨‍👩‍👧", "🇨🇳", "1️⃣", "👍🏽")):
    image = Image.new("RGBA", (512, 160))
    ImageDraw.Draw(image).text(
        (0, 0),
        value,
        font=font,
        embedded_color=True,
    )
    if image.getchannel("A").getbbox() is None:
        fail(f"Noto Color Emoji failed to render a glyph: {value}")
    # Some valid Noto glyphs, notably family silhouettes and keycaps, are
    # intentionally grayscale. One known multicolour glyph proves that Pillow
    # loaded the CBDT colour strike; every other probe only needs real ink.
    if index == 0 and not has_chromatic_pixels(image):
        fail("Pillow did not load Noto Color Emoji's colour strike.")

print(f"Color emoji renderer ready: {font_path}")
