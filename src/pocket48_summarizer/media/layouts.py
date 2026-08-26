from __future__ import annotations

from typing import Literal

ClipOutputLayout = Literal["portrait", "landscape"]
LandscapeSubtitleFont = Literal["wenkai", "serif", "sans"]

LANDSCAPE_CANVAS_WIDTH = 1920
LANDSCAPE_CANVAS_HEIGHT = 1080
LANDSCAPE_VIDEO_WIDTH = 608
LANDSCAPE_BACKGROUND_COLOR = "#EBE9E1"
LANDSCAPE_SUBTITLE_COLOR = "#E43D12"
LANDSCAPE_SUBTITLE_LEFT = 72
LANDSCAPE_SUBTITLE_WIDTH = 509
LANDSCAPE_SUBTITLE_ZH_SIZE = 23
LANDSCAPE_SUBTITLE_EN_SIZE = 18
LANDSCAPE_DANMAKU_AUTHOR_COLOR = "#D6536D"
LANDSCAPE_DANMAKU_TEXT_COLOR = "#5B3A42"
LANDSCAPE_DANMAKU_BACKGROUND_COLOR = "#FFF8F6"
LANDSCAPE_DANMAKU_WIDTH = 518
LANDSCAPE_DANMAKU_RIGHT = 65
LANDSCAPE_DANMAKU_BOTTOM = 76
LANDSCAPE_DANMAKU_GAP = 13
LANDSCAPE_DANMAKU_PADDING_X = 14
LANDSCAPE_DANMAKU_PADDING_Y = 12
LANDSCAPE_DANMAKU_AUTHOR_SIZE = 18
LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT = 22
LANDSCAPE_DANMAKU_BODY_SIZE = 22
LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT = 29
LANDSCAPE_DANMAKU_TEXT_GAP = 3
LANDSCAPE_DANMAKU_RADIUS = 20
# libass glyphs render smaller than CSS pixels at the same numeric size.
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


def landscape_video_filters() -> tuple[str, ...]:
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
            f"(ow-iw)/2:0:color=0x{LANDSCAPE_BACKGROUND_COLOR[1:]}"
        ),
        "setsar=1",
    )
