from __future__ import annotations

from typing import Literal

ClipOutputLayout = Literal["portrait", "landscape"]
LandscapeSubtitleFont = Literal["wenkai", "serif", "sans"]

LANDSCAPE_CANVAS_WIDTH = 1920
LANDSCAPE_CANVAS_HEIGHT = 1080
LANDSCAPE_VIDEO_WIDTH = 608
LANDSCAPE_BACKGROUND_COLOR = "#EBE9E1"
LANDSCAPE_SUBTITLE_COLOR = "#E43D12"
LANDSCAPE_SUBTITLE_EN_COLOR = "#D6536D"
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
LANDSCAPE_WATERMARK_SIZE = 22
LANDSCAPE_WATERMARK_TOP = 26
LANDSCAPE_WATERMARK_LEFT = 72
LANDSCAPE_WATERMARK_RIGHT = 65
LANDSCAPE_WATERMARK_COLOR = "#5B3A42"
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
