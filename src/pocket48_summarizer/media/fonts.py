"""Font discovery and emoji-run detection for clip overlays."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, features

from ..errors import AppError

EMOJI_FONT_FAMILY = "Noto Color Emoji"
COLOR_EMOJI_NATIVE_SIZE = 109

# Unicode gives every emoji-capable codepoint a default presentation, and only
# the ones defaulting to *emoji* presentation break: those are the ones
# fontconfig answers with a colour font. Characters defaulting to text
# presentation (☀ ★ ☺ ♥ ✔ ➡ ...) resolve to the installed CJK font and already
# render, so routing them here would restyle glyphs that are not broken.
_EMOJI_PRESENTATION_SUPPLEMENTARY = "\U0001f000-\U0001faff"
_EMOJI_PRESENTATION_BMP = (
    "\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2614\u2615"
    "\u2648-\u2653\u267f\u2693\u26a1\u26aa\u26ab\u26bd\u26be"
    "\u26c4\u26c5\u26ce\u26d4\u26ea\u26f2\u26f3\u26f5\u26fa"
    "\u26fd\u2705\u270a\u270b\u2728\u274c\u274e\u2753-\u2755"
    "\u2757\u2795-\u2797\u27b0\u27bf\u2b1b\u2b1c\u2b50\u2b55"
)
# A text-presentation character followed by U+FE0F is an explicit request for
# the colour glyph, so it fails the same way and belongs to the same run.
_EMOJI_TEXT_DEFAULT = "\u0023\u002a\u0030-\u0039\u00a9\u00ae\u203c-\u3299"
# Variation selectors, skin tones and subdivision-flag tags extend one
# grapheme. ZWJ is handled separately because it joins another full base.
_EMOJI_EXTENDER = (
    "\ufe0e\ufe0f\U0001f3fb-\U0001f3ff"
    "\U000e0020-\U000e007f"
)
_EMOJI_REGIONAL_INDICATOR = "\U0001f1e6-\U0001f1ff"

_EMOJI_ATOM = (
    f"(?:[{_EMOJI_PRESENTATION_SUPPLEMENTARY}{_EMOJI_PRESENTATION_BMP}]"
    f"|[{_EMOJI_TEXT_DEFAULT}]\ufe0f)"
)
# Match one grapheme at a time. In particular, adjacent emoji must remain
# separate cells while ZWJ families, flags, keycaps and skin tones stay whole.
_EMOJI_RE = re.compile(
    f"(?:"
    f"[{_EMOJI_REGIONAL_INDICATOR}][{_EMOJI_REGIONAL_INDICATOR}]"
    f"|[0-9#*]\ufe0f?\u20e3"
    f"|{_EMOJI_ATOM}[{_EMOJI_EXTENDER}]*"
    f"(?:\u200d{_EMOJI_ATOM}[{_EMOJI_EXTENDER}]*)*"
    f")"
)

# 🎉 PARTY POPPER, a plain single-codepoint emoji that every emoji font ships.
EMOJI_PROBE_CODEPOINT = 0x1F389

EmojiFontStatus = Literal["available", "missing", "unknown"]


@dataclass(frozen=True, slots=True)
class FontFace:
    path: Path
    index: int


def has_chromatic_pixels(image: Image.Image) -> bool:
    return any(
        alpha > 0 and (red != green or green != blue)
        for red, green, blue, alpha in image.get_flattened_data()
    )


def contains_emoji(value: str) -> bool:
    return bool(_EMOJI_RE.search(value))


def split_emoji_runs(value: str) -> Iterator[tuple[bool, str]]:
    """Yield ``(is_emoji, text)`` chunks covering ``value`` in order."""

    cursor = 0
    for match in _EMOJI_RE.finditer(value):
        if match.start() > cursor:
            yield (False, value[cursor : match.start()])
        yield (True, match.group())
        cursor = match.end()
    if cursor < len(value):
        yield (False, value[cursor:])


@lru_cache(maxsize=1)
def emoji_font_family() -> str | None:
    return _probe_emoji_font()[1]


@lru_cache(maxsize=1)
def emoji_font_status() -> EmojiFontStatus:
    probed, family = _probe_emoji_font()
    if not probed:
        return "unknown"
    return "available" if family else "missing"


def reset_font_probe_cache() -> None:
    _probe_emoji_font.cache_clear()
    resolve_font_face.cache_clear()
    resolve_font_path.cache_clear()
    emoji_font_family.cache_clear()
    emoji_font_status.cache_clear()


@lru_cache(maxsize=1)
def _probe_emoji_font() -> tuple[bool, str | None]:
    try:
        path = resolve_font_path(EMOJI_FONT_FAMILY)
    except AppError:
        return (True, None)
    try:
        if not features.check_module("freetype2") or not features.check_feature(
            "raqm"
        ):
            return (True, None)
        font = ImageFont.truetype(
            str(path),
            COLOR_EMOJI_NATIVE_SIZE,
            layout_engine=ImageFont.Layout.RAQM,
        )
        image = Image.new("RGBA", (180, 150))
        ImageDraw.Draw(image).text(
            (0, 0),
            chr(EMOJI_PROBE_CODEPOINT),
            font=font,
            embedded_color=True,
        )
    except (OSError, ValueError):
        return (True, None)
    if image.getchannel("A").getbbox() and has_chromatic_pixels(image):
        return (True, EMOJI_FONT_FAMILY)
    return (True, None)


@lru_cache(maxsize=32)
def resolve_font_path(family: str) -> Path:
    return resolve_font_face(family).path


@lru_cache(maxsize=32)
def resolve_font_face(family: str) -> FontFace:
    try:
        result = subprocess.run(
            ["fc-match", "--format=%{file}\t%{index}\n", family],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "clip_font_unavailable",
            f"无法检查剪辑字体：{family}",
            False,
        ) from exc
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    path_value, _, index_value = first_line.partition("\t")
    path = Path(path_value)
    if result.returncode != 0 or not path.is_file():
        raise AppError(
            "clip_font_unavailable",
            f"服务器缺少剪辑字体：{family}",
            False,
        )
    if family == EMOJI_FONT_FAMILY:
        try:
            family_result = subprocess.run(
                ["fc-scan", "--format=%{family}\n", str(path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AppError(
                "color_emoji_unavailable",
                "无法验证服务器彩色 emoji 字体",
                False,
            ) from exc
        if (
            family_result.returncode != 0
            or EMOJI_FONT_FAMILY not in family_result.stdout
        ):
            raise AppError(
                "color_emoji_unavailable",
                "服务器没有安装 Noto Color Emoji",
                False,
            )
    try:
        index = int(index_value or "0")
    except ValueError:
        index = 0
    return FontFace(path=path, index=index)


def require_color_emoji_font_path() -> Path:
    if emoji_font_status() != "available":
        raise AppError(
            "color_emoji_unavailable",
            "服务器缺少可用的 Noto Color Emoji 彩色字体或 RAQM 支持",
            False,
        )
    try:
        return resolve_font_path(EMOJI_FONT_FAMILY)
    except AppError as exc:
        raise AppError(
            "color_emoji_unavailable",
            "服务器缺少可用的 Noto Color Emoji 彩色字体",
            False,
        ) from exc
