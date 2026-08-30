from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ClipOutputLayout = Literal["portrait", "landscape"]
LandscapeSubtitleFont = Literal["wenkai", "serif", "sans"]
LandscapeThemeKey = Literal[
    "cream", "denim", "mint", "sakura", "matcha", "ink"
]


@dataclass(frozen=True, slots=True)
class LandscapeTheme:
    """Every colour the landscape canvas paints, in one place.

    The individual colours are deliberately not exported as module constants.
    A renderer that reached for a loose ``LANDSCAPE_..._COLOR`` would silently
    ignore the clip's chosen theme, so the only way to obtain a colour is to
    resolve a theme first.
    """

    key: LandscapeThemeKey
    background: str
    subtitle_zh: str
    subtitle_en: str
    danmaku_author: str
    danmaku_text: str
    danmaku_background: str
    watermark: str


LANDSCAPE_THEMES: dict[str, LandscapeTheme] = {
    "cream": LandscapeTheme(
        key="cream",
        background="#EBE9E1",
        subtitle_zh="#E43D12",
        subtitle_en="#D6536D",
        danmaku_author="#D6536D",
        danmaku_text="#5B3A42",
        danmaku_background="#FFF8F6",
        watermark="#5B3A42",
    ),
    # The reference palette pairs #D9D9D9, #286181 and #FF5757. The blue is
    # the only one dark enough to carry the primary subtitle over the grey,
    # so the red stays an accent on the secondary line and the danmaku names.
    "denim": LandscapeTheme(
        key="denim",
        background="#D9D9D9",
        subtitle_zh="#286181",
        subtitle_en="#FF5757",
        danmaku_author="#FF5757",
        danmaku_text="#286181",
        danmaku_background="#FFFFFF",
        watermark="#286181",
    ),
    # Built from the #D4EAE8 / #FF917A / #FCC439 pastel set. Pastels have no
    # colour dark enough to read as text on their own background, so the
    # coral and yellow are deepened for type while the mint stays the canvas.
    "mint": LandscapeTheme(
        key="mint",
        background="#D4EAE8",
        subtitle_zh="#D9512F",
        subtitle_en="#2F5551",
        danmaku_author="#D9512F",
        danmaku_text="#2F5551",
        danmaku_background="#FBFEFD",
        watermark="#2F5551",
    ),
    "sakura": LandscapeTheme(
        key="sakura",
        background="#FBE4EC",
        subtitle_zh="#C2185B",
        subtitle_en="#7A4A5C",
        danmaku_author="#C2185B",
        danmaku_text="#5A3644",
        danmaku_background="#FFFFFF",
        watermark="#5A3644",
    ),
    "matcha": LandscapeTheme(
        key="matcha",
        background="#E6EDD6",
        subtitle_zh="#4F6B25",
        subtitle_en="#A9541B",
        danmaku_author="#A9541B",
        danmaku_text="#3E4A2E",
        danmaku_background="#FCFEF7",
        watermark="#3E4A2E",
    ),
    # The only dark canvas in the set, so it is the one that proves every
    # colour is really driven by the theme: anything still hard-coded for a
    # light background shows up here immediately.
    "ink": LandscapeTheme(
        key="ink",
        background="#1C1D22",
        subtitle_zh="#F5E6C8",
        subtitle_en="#E0A96D",
        danmaku_author="#E0A96D",
        danmaku_text="#ECE7DE",
        danmaku_background="#2A2C34",
        watermark="#ECE7DE",
    ),
}
DEFAULT_LANDSCAPE_THEME: LandscapeThemeKey = "cream"


def resolve_landscape_theme(value: str | None) -> LandscapeTheme:
    if not value:
        return LANDSCAPE_THEMES[DEFAULT_LANDSCAPE_THEME]
    try:
        return LANDSCAPE_THEMES[value]
    except KeyError as exc:
        raise ValueError("unsupported landscape theme") from exc


LANDSCAPE_CANVAS_WIDTH = 1920
LANDSCAPE_CANVAS_HEIGHT = 1080
LANDSCAPE_VIDEO_WIDTH = 608
LANDSCAPE_SUBTITLE_LEFT = 72
LANDSCAPE_SUBTITLE_WIDTH = 509
LANDSCAPE_SUBTITLE_ZH_SIZE = 23
LANDSCAPE_SUBTITLE_EN_SIZE = 18
LANDSCAPE_DANMAKU_WIDTH = 518
LANDSCAPE_DANMAKU_RIGHT = 65
LANDSCAPE_DANMAKU_BOTTOM = 76
# Matches the preview column's 7% top inset so the ASS stack can grow
# upwards until it reaches the same ceiling the browser clips at.
LANDSCAPE_DANMAKU_TOP = 76
LANDSCAPE_DANMAKU_GAP = 13
LANDSCAPE_DANMAKU_PADDING_X = 14
LANDSCAPE_DANMAKU_PADDING_Y = 12
LANDSCAPE_DANMAKU_AUTHOR_SIZE = 18
LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT = 22
LANDSCAPE_DANMAKU_BODY_SIZE = 22
LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT = 29
LANDSCAPE_DANMAKU_TEXT_GAP = 3
LANDSCAPE_DANMAKU_RADIUS = 20
# Portrait clips keep the source video's own resolution instead of the fixed
# landscape canvas, so the same card design has to be expressed as fractions
# of the frame. Ratios are taken from the preview column, which is authored
# against a 1080x1920 stage.
PORTRAIT_DANMAKU_WIDTH_RATIO = 0.42
PORTRAIT_DANMAKU_RIGHT_RATIO = 0.022
PORTRAIT_DANMAKU_BOTTOM_RATIO = 0.0125
PORTRAIT_DANMAKU_TOP_RATIO = 0.0125
PORTRAIT_DANMAKU_GAP_RATIO = 0.0063
PORTRAIT_DANMAKU_PADDING_X_RATIO = 0.0194
PORTRAIT_DANMAKU_PADDING_Y_RATIO = 0.0078
PORTRAIT_DANMAKU_RADIUS_RATIO = 0.0156
PORTRAIT_DANMAKU_AUTHOR_SIZE_RATIO = 0.0141
PORTRAIT_DANMAKU_BODY_SIZE_RATIO = 0.0172
PORTRAIT_DANMAKU_TEXT_GAP_RATIO = 0.0016
PORTRAIT_DANMAKU_LINE_HEIGHT = 1.3
PORTRAIT_DANMAKU_AUTHOR_LINE_HEIGHT = 1.22
PORTRAIT_DANMAKU_AUTHOR_COLOR = "#D9D0FF"
PORTRAIT_DANMAKU_TEXT_COLOR = "#FFFFFF"
PORTRAIT_DANMAKU_BACKGROUND_COLOR = "#0A0C12"

# libass glyphs render smaller than CSS pixels at the same numeric size.
# The 1080-tall video fills the middle 608px of the canvas, so a watermark can
# only live in the cream columns beside it. The top band is the one strip that
# is free by construction: the danmaku column is bounded at
# LANDSCAPE_DANMAKU_TOP and stacks upward from the bottom, and subtitles are
# centred vertically, so neither can ever reach above it however full the clip
# gets.
LANDSCAPE_WATERMARK_SIZE = 30
# The right mark has to stay clear of LANDSCAPE_DANMAKU_TOP, which is the
# ceiling a full danmaku column grows up to. The left column has no such
# ceiling -- subtitles are centred vertically and would need most of the
# canvas before they reached the top -- so that mark can sit lower and look
# placed rather than jammed into the corner.
LANDSCAPE_WATERMARK_TOP = 40
LANDSCAPE_WATERMARK_LEFT_TOP = 88
LANDSCAPE_WATERMARK_LEFT = 72
LANDSCAPE_WATERMARK_RIGHT = 65
# ASS alpha out of 255, where 0 is opaque.
LANDSCAPE_WATERMARK_ALPHA = 64
CLIP_WATERMARK_TEXT = "AI剪切片工具 p48.ruokezhang.com"

LANDSCAPE_LIBASS_FONT_SCALE = 1.4
LANDSCAPE_LIBASS_DANMAKU_AUTHOR_SCALE = 1.5
DEFAULT_LANDSCAPE_SUBTITLE_FONT: LandscapeSubtitleFont = "wenkai"
LANDSCAPE_SUBTITLE_FONT_NAMES: dict[str, str] = {
    "wenkai": "LXGW WenKai",
    "serif": "Noto Serif CJK SC",
    "sans": "Noto Sans CJK SC",
}


def landscape_subtitle_font_name(value: str) -> str:
    try:
        return LANDSCAPE_SUBTITLE_FONT_NAMES[value]
    except KeyError as exc:
        raise ValueError("unsupported landscape subtitle font") from exc


def landscape_video_filters(theme: LandscapeTheme) -> tuple[str, ...]:
    return (
        (
            f"scale={LANDSCAPE_VIDEO_WIDTH}:{LANDSCAPE_CANVAS_HEIGHT}:"
            "force_original_aspect_ratio=decrease"
        ),
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        (
            f"pad={LANDSCAPE_VIDEO_WIDTH}:{LANDSCAPE_CANVAS_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2:color=0x08090C"
        ),
        (
            f"pad={LANDSCAPE_CANVAS_WIDTH}:{LANDSCAPE_CANVAS_HEIGHT}:"
            f"(ow-iw)/2:0:color=0x{theme.background[1:]}"
        ),
        "setsar=1",
    )
