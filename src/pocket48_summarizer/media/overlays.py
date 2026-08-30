from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass
from typing import Literal, cast

from ..errors import AppError
from ..models import (
    AICoverLayoutStyle,
    DanmakuEntry,
    TranscriptSegment,
)
from ..datetimes import format_china_datetime
from ..security import strip_control_chars
from .fonts import contains_emoji, split_emoji_runs
from .layouts import (
    LANDSCAPE_CANVAS_WIDTH,
    LANDSCAPE_CANVAS_HEIGHT,
    LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT,
    LANDSCAPE_DANMAKU_AUTHOR_SIZE,
    LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT,
    LANDSCAPE_DANMAKU_BODY_SIZE,
    LANDSCAPE_DANMAKU_BOTTOM,
    LANDSCAPE_DANMAKU_GAP,
    LANDSCAPE_DANMAKU_PADDING_X,
    LANDSCAPE_DANMAKU_PADDING_Y,
    LANDSCAPE_DANMAKU_RADIUS,
    LANDSCAPE_DANMAKU_RIGHT,
    LANDSCAPE_DANMAKU_TEXT_GAP,
    LANDSCAPE_DANMAKU_TOP,
    LANDSCAPE_DANMAKU_WIDTH,
    LANDSCAPE_LIBASS_DANMAKU_AUTHOR_SCALE,
    LANDSCAPE_LIBASS_FONT_SCALE,
    LANDSCAPE_SUBTITLE_EN_SIZE,
    CLIP_WATERMARK_TEXT,
    LANDSCAPE_SUBTITLE_LEFT,
    LANDSCAPE_SUBTITLE_WIDTH,
    LANDSCAPE_WATERMARK_ALPHA,
    LandscapeTheme,
    resolve_landscape_theme,
    LANDSCAPE_WATERMARK_LEFT_TOP,
    LANDSCAPE_WATERMARK_TOP,
    LANDSCAPE_WATERMARK_LEFT,
    LANDSCAPE_WATERMARK_RIGHT,
    LANDSCAPE_WATERMARK_SIZE,
    LANDSCAPE_SUBTITLE_ZH_SIZE,
    LANDSCAPE_VIDEO_WIDTH,
    PORTRAIT_DANMAKU_AUTHOR_COLOR,
    PORTRAIT_DANMAKU_AUTHOR_LINE_HEIGHT,
    PORTRAIT_DANMAKU_AUTHOR_SIZE_RATIO,
    PORTRAIT_DANMAKU_BACKGROUND_COLOR,
    PORTRAIT_DANMAKU_BODY_SIZE_RATIO,
    PORTRAIT_DANMAKU_BOTTOM_RATIO,
    PORTRAIT_DANMAKU_GAP_RATIO,
    PORTRAIT_DANMAKU_LINE_HEIGHT,
    PORTRAIT_DANMAKU_PADDING_X_RATIO,
    PORTRAIT_DANMAKU_PADDING_Y_RATIO,
    PORTRAIT_DANMAKU_RADIUS_RATIO,
    PORTRAIT_DANMAKU_RIGHT_RATIO,
    PORTRAIT_DANMAKU_TEXT_COLOR,
    PORTRAIT_DANMAKU_TEXT_GAP_RATIO,
    PORTRAIT_DANMAKU_TOP_RATIO,
    PORTRAIT_DANMAKU_WIDTH_RATIO,
    DEFAULT_LANDSCAPE_SUBTITLE_FONT,
    ClipOutputLayout,
    LandscapeSubtitleFont,
    landscape_subtitle_font_name,
)
from .raster_overlays import (
    RasterAsset,
    RasterBox,
    RasterOverlayCue,
    RasterPlacement,
    RasterTextLine,
)

SubtitleMode = Literal["off", "zh", "en", "bilingual"]
CoverStyle = Literal["scrim", "display", "badge"]
DANMAKU_MIN_GAP_MS = 450
# Landscape cards vary in height, so the stack is bounded by the column
# instead of a fixed count; this only guards pathological input.
DANMAKU_MAX_STACK = 16
DANMAKU_RISE_MS = 220
SUBTITLE_FONT_SCALE_MIN = 50
SUBTITLE_FONT_SCALE_MAX = 150
DEFAULT_SUBTITLE_FONT_SCALE = 100
SUBTITLE_FONT_BASE_SCALE = 1.6
# Portrait subtitles are deliberately unstyled: white glyphs with a black
# outline stay legible over any frame without tinting the footage.
PORTRAIT_SUBTITLE_COLOR = "&H00FFFFFF"
PORTRAIT_SUBTITLE_OUTLINE_COLOR = "&H00000000"
PORTRAIT_SUBTITLE_OUTLINE_RATIO = 0.055
# An ASS font size is not an em: libass derives the pixel size from the
# face's ascender plus descender, so glyphs advance by noticeably less than
# the nominal size. Measured against Noto Sans CJK SC under libass 0.17.
LIBASS_CJK_ADVANCE_RATIO = 0.69
LIBASS_LATIN_ADVANCE_RATIO = 0.34
COVER_DURATION_MS = 1500
COVER_TITLE_MAX_LENGTH = 40
DEFAULT_COVER_STYLE: CoverStyle = "scrim"
COVER_LIBASS_FONT_SCALE = 1.45
AI_COVER_TITLE_MAX_LENGTH = 80
AI_COVER_HIGHLIGHT_MAX_LENGTH = 60
AI_COVER_EXTRA_TEXT_MAX_ITEMS = 4
AI_COVER_EXTRA_TEXT_MAX_LENGTH = 60
DEFAULT_AI_COVER_LAYOUT_STYLE: AICoverLayoutStyle = "sticker_pop"
LANDSCAPE_SUBTITLE_LINE_HEIGHT = 1.55
LANDSCAPE_SUBTITLE_PARAGRAPH_GAP = 8
LANDSCAPE_SUBTITLE_POSITION_OFFSET = 4
LANDSCAPE_SUBTITLE_ZH_SCALE_X = 94
LANDSCAPE_SUBTITLE_ZH_SCALE_Y = 94
LANDSCAPE_SUBTITLE_EN_SCALE_X = 95
LANDSCAPE_SUBTITLE_EN_SCALE_Y = 90
LANDSCAPE_SUBTITLE_EN_OPACITY = 0.88
LANDSCAPE_SUBTITLE_EN_WIDTH_FACTOR = 0.46
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
CJK_CLOSING_PUNCTUATION = frozenset("，。！？；：、）》】」』”’")


@dataclass(frozen=True, slots=True)
class ClipOverlayDocument:
    content: str
    subtitle_event_count: int
    danmaku_event_count: int
    warning_message: str | None = None
    raster_cues: tuple[RasterOverlayCue, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverOverlayDocument:
    content: str
    title: str
    style: CoverStyle
    raster_cues: tuple[RasterOverlayCue, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedDanmaku:
    relative_ms: int
    author: str
    body: str
    author_plain: str
    body_lines_plain: tuple[str, ...]
    body_lines: int


def build_clip_overlay(
    *,
    width: int,
    height: int,
    clip_start_ms: int,
    clip_end_ms: int,
    subtitle_mode: SubtitleMode,
    include_danmaku: bool,
    font_name: str,
    transcript: list[TranscriptSegment],
    translations: dict[int, str],
    danmaku: list[DanmakuEntry],
    subtitle_font_scale: int = DEFAULT_SUBTITLE_FONT_SCALE,
    output_layout: ClipOutputLayout = "portrait",
    subtitle_font_family: LandscapeSubtitleFont = (
        DEFAULT_LANDSCAPE_SUBTITLE_FONT
    ),
    allow_empty_subtitles: bool = False,
    live_started_at: str | None = None,
    landscape_theme: str | None = None,
) -> ClipOverlayDocument:
    if width <= 0 or height <= 0 or clip_end_ms <= clip_start_ms:
        raise AppError(
            "clip_overlay_invalid",
            "字幕或弹幕渲染参数无效",
            False,
        )
    if (
        output_layout == "landscape"
        and (
            width != LANDSCAPE_CANVAS_WIDTH
            or height != LANDSCAPE_CANVAS_HEIGHT
        )
    ):
        raise AppError(
            "clip_layout_invalid",
            "横屏画布尺寸无效",
            False,
        )
    try:
        theme = resolve_landscape_theme(landscape_theme)
    except ValueError as exc:
        raise AppError(
            "clip_theme_invalid", "视频配色方案无效", False
        ) from exc
    subtitle_events, subtitle_raster_cues = _subtitle_events(
        width=width,
        height=height,
        reserve_danmaku=include_danmaku,
        clip_start_ms=clip_start_ms,
        clip_end_ms=clip_end_ms,
        subtitle_mode=subtitle_mode,
        transcript=transcript,
        translations=translations,
        output_layout=output_layout,
        subtitle_font_scale=subtitle_font_scale,
        allow_empty=allow_empty_subtitles,
        font_name=font_name,
        subtitle_font_family=subtitle_font_family,
        theme=theme,
    )
    danmaku_events, danmaku_count, danmaku_raster_cues = (
        _danmaku_events(
            width=width,
            height=height,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            danmaku=danmaku,
            output_layout=output_layout,
            font_name=font_name,
            theme=theme,
        )
        if include_danmaku
        else ([], 0, [])
    )
    warning = (
        "所选范围没有可渲染的弹幕"
        if include_danmaku and not danmaku_count
        else None
    )
    header = _ass_header(
        width=width,
        height=height,
        font_name=_plain_text(font_name).replace(",", " ") or "sans-serif",
        reserve_danmaku=include_danmaku,
        subtitle_font_scale=subtitle_font_scale,
        output_layout=output_layout,
        subtitle_font_family=subtitle_font_family,
        theme=theme,
    )
    watermark_events = (
        _landscape_watermark_events(
            duration_ms=clip_end_ms - clip_start_ms,
            live_started_at=live_started_at,
        )
        if output_layout == "landscape"
        else []
    )
    return ClipOverlayDocument(
        content="\n".join(
            [
                header,
                *subtitle_events,
                *danmaku_events,
                *watermark_events,
                "",
            ]
        ),
        subtitle_event_count=(
            len(subtitle_events) + len(subtitle_raster_cues)
        ),
        danmaku_event_count=danmaku_count,
        warning_message=warning,
        raster_cues=tuple(
            [*subtitle_raster_cues, *danmaku_raster_cues]
        ),
    )


def build_cover_overlay(
    *,
    width: int,
    height: int,
    title: str,
    style: CoverStyle = DEFAULT_COVER_STYLE,
    font_name: str = "Noto Sans CJK SC",
    output_layout: ClipOutputLayout = "portrait",
    duration_ms: int = COVER_DURATION_MS,
) -> CoverOverlayDocument:
    normalized_title = normalize_cover_title(title)
    if (
        width <= 0
        or height <= 0
        or duration_ms <= 0
        or not normalized_title
        or len(normalized_title) > COVER_TITLE_MAX_LENGTH
    ):
        raise AppError(
            "clip_cover_invalid",
            "封面标题或画面参数无效",
            False,
        )
    if style not in {"scrim", "display", "badge"}:
        raise AppError(
            "clip_cover_invalid",
            "封面标题样式无效",
            False,
        )
    if (
        output_layout == "landscape"
        and (
            width != LANDSCAPE_CANVAS_WIDTH
            or height != LANDSCAPE_CANVAS_HEIGHT
        )
    ):
        raise AppError(
            "clip_layout_invalid",
            "横屏画布尺寸无效",
            False,
        )
    if output_layout not in {"portrait", "landscape"}:
        raise AppError(
            "clip_layout_invalid",
            "视频画面方向无效",
            False,
        )

    video_width = (
        LANDSCAPE_VIDEO_WIDTH
        if output_layout == "landscape"
        else width
    )
    video_x = (width - video_width) // 2
    safe_x = round(video_width * 0.06)
    panel_x = video_x + safe_x
    panel_width = video_width - safe_x * 2
    center_x = panel_x + panel_width // 2
    if style == "scrim":
        box_y = round(height * 0.12)
        box_height = round(height * 0.22)
        title_y = box_y + box_height // 2
        visual_size = max(28, round(height * 0.041))
        text_color = "#FFF8F6"
        outline_color = "#08090C"
        outline_width = 2
        shadow = 2
        box_color = "#08090C"
        box_alpha = 100
        radius = round(height * 0.022)
    elif style == "display":
        box_y = None
        box_height = None
        title_y = round(height * 0.42)
        visual_size = max(30, round(height * 0.046))
        text_color = "#F6D365"
        outline_color = "#08090C"
        outline_width = 5
        shadow = 3
        box_color = "#08090C"
        box_alpha = 255
        radius = 0
    else:
        box_y = round(height * 0.62)
        box_height = round(height * 0.20)
        title_y = box_y + box_height // 2
        visual_size = max(26, round(height * 0.037))
        text_color = "#FFF8F6"
        outline_color = "#7A1837"
        outline_width = 1
        shadow = 1
        box_color = "#B8325B"
        box_alpha = 16
        radius = round(height * 0.026)

    contains_cjk = bool(re.search(r"[\u3400-\u9fff]", normalized_title))
    average_width = 1.0 if contains_cjk else 0.52
    text_inset = round(
        video_width * (0.05 if output_layout == "landscape" else 0.03)
    )
    wrap_width = max(
        6,
        round(
            (panel_width - text_inset * 2)
            / max(1, visual_size * average_width)
        ),
    )
    wrapped_title = _wrapped_ass_text(
        normalized_title,
        width=wrap_width,
        lines=2,
    )
    safe_font_name = _plain_text(font_name).replace(",", " ") or "sans-serif"
    ass_size = round(visual_size * COVER_LIBASS_FONT_SCALE)
    primary = _ass_color(text_color)
    outline = _ass_color(outline_color, alpha=24)
    back = _ass_color(outline_color, alpha=112)
    box_fill = _ass_color(box_color, alpha=box_alpha)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CoverTitle,{safe_font_name},{ass_size},{primary},{primary},{outline},{back},-1,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1
Style: CoverBox,{safe_font_name},1,{box_fill},{box_fill},{box_fill},{box_fill},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""
    events: list[str] = []
    raster_cues: list[RasterOverlayCue] = []
    if box_y is not None and box_height is not None:
        box = _ass_rounded_rect(panel_width, box_height, radius)
        events.append(
            _dialogue(
                layer=4,
                start_ms=0,
                end_ms=duration_ms,
                style="CoverBox",
                text=(
                    f"{{\\an7\\pos({panel_x},{box_y})\\p1}}"
                    f"{box}{{\\p0}}"
                ),
            )
        )
    if contains_emoji(normalized_title):
        title_lines_list = _wrapped_text(
            normalized_title,
            width=wrap_width,
        )
        if len(title_lines_list) > 2:
            title_lines_list = title_lines_list[:2]
            title_lines_list[-1] = (
                _truncate(
                    title_lines_list[-1],
                    max(1, wrap_width - 1),
                )
                + "…"
            )
        title_lines = tuple(title_lines_list)
        title_line_step = round(visual_size * 1.25)
        title_line_box_height = (
            round(visual_size * 1.6)
            + outline_width * 2
            + shadow
        )
        title_height = max(
            title_line_box_height,
            (len(title_lines) - 1) * title_line_step
            + title_line_box_height,
        )
        raster_cues.append(
            RasterOverlayCue(
                asset=RasterAsset(
                    width=panel_width,
                    height=title_height,
                    lines=tuple(
                        RasterTextLine(
                            text=line,
                            x=panel_width // 2,
                            y=index * title_line_step,
                            font_name=safe_font_name,
                            font_size=visual_size,
                            color=_rgba(text_color),
                            anchor="center",
                            stroke_width=outline_width,
                            stroke_color=_rgba(outline_color, alpha=231),
                            shadow_offset=shadow,
                            shadow_color=_rgba(
                                outline_color, alpha=143
                            ),
                        )
                        for index, line in enumerate(title_lines)
                    ),
                ),
                x=panel_x,
                placements=(
                    RasterPlacement(
                        start_ms=0,
                        end_ms=duration_ms,
                        y_from=title_y - title_height // 2,
                        y_to=title_y - title_height // 2,
                    ),
                ),
                layer=5,
            )
        )
    else:
        events.append(
            _dialogue(
                layer=5,
                start_ms=0,
                end_ms=duration_ms,
                style="CoverTitle",
                text=f"{{\\an5\\pos({center_x},{title_y})}}{wrapped_title}",
            )
        )
    return CoverOverlayDocument(
        content="\n".join([header, *events, ""]),
        title=normalized_title,
        style=style,
        raster_cues=tuple(raster_cues),
    )


@dataclass(frozen=True)
class _PortraitSubtitleMetrics:
    zh_size: int
    en_size: int
    zh_outline: int
    en_outline: int
    margin_l: int
    margin_r: int
    margin_v: int
    band_width: int

    @property
    def zh_line_width(self) -> int:
        return max(
            4,
            int(self.band_width / (self.zh_size * LIBASS_CJK_ADVANCE_RATIO)),
        )

    @property
    def en_line_width(self) -> int:
        return max(
            8,
            int(
                self.band_width
                / (self.en_size * LIBASS_LATIN_ADVANCE_RATIO)
            ),
        )


def _portrait_subtitle_metrics(
    *,
    width: int,
    height: int,
    subtitle_font_scale: int,
    reserve_danmaku: bool,
) -> _PortraitSubtitleMetrics:
    """Derive every portrait caption dimension from one place.

    The header and the dialogue lines both need these numbers, and computing
    them twice is what let the rendered text outgrow its margins unnoticed.
    """

    scale = _subtitle_scale(subtitle_font_scale)
    zh_size = max(14, round(max(20, height * 0.034) * scale))
    en_size = max(12, round(max(16, height * 0.025) * scale))
    margin_l = max(12, round(width * 0.04))
    margin_r = (
        max(12, round(width * 0.44))
        if reserve_danmaku
        else margin_l
    )
    return _PortraitSubtitleMetrics(
        zh_size=zh_size,
        en_size=en_size,
        zh_outline=max(2, round(zh_size * PORTRAIT_SUBTITLE_OUTLINE_RATIO)),
        en_outline=max(2, round(en_size * PORTRAIT_SUBTITLE_OUTLINE_RATIO)),
        margin_l=margin_l,
        margin_r=margin_r,
        margin_v=max(12, round(height * 0.025)),
        band_width=max(1, width - margin_l - margin_r),
    )


def _subtitle_events(
    *,
    width: int,
    height: int,
    clip_start_ms: int,
    clip_end_ms: int,
    subtitle_mode: SubtitleMode,
    transcript: list[TranscriptSegment],
    translations: dict[int, str],
    output_layout: ClipOutputLayout,
    subtitle_font_scale: int,
    reserve_danmaku: bool,
    allow_empty: bool,
    font_name: str,
    subtitle_font_family: LandscapeSubtitleFont,
    theme: LandscapeTheme,
) -> tuple[list[str], list[RasterOverlayCue]]:
    if subtitle_mode == "off":
        return ([], [])
    selected = [
        segment
        for segment in transcript
        if segment.end_ms > clip_start_ms
        and segment.start_ms < clip_end_ms
    ]
    if not selected:
        if allow_empty:
            return ([], [])
        raise AppError(
            "clip_subtitles_empty",
            "所选范围没有可渲染的字幕",
            False,
        )
    if subtitle_mode in {"en", "bilingual"}:
        missing = [
            segment.sequence
            for segment in selected
            if not _plain_text(translations.get(segment.sequence, ""))
        ]
        if missing:
            raise AppError(
                "clip_english_subtitles_not_ready",
                "所选范围的英文字幕尚未完整生成",
                True,
            )
    events: list[str] = []
    raster_cues: list[RasterOverlayCue] = []
    scale = _subtitle_scale(subtitle_font_scale)
    portrait = _portrait_subtitle_metrics(
        width=width,
        height=height,
        subtitle_font_scale=subtitle_font_scale,
        reserve_danmaku=reserve_danmaku,
    )
    landscape_zh_width = max(
        8,
        LANDSCAPE_SUBTITLE_WIDTH
        // max(1, round(LANDSCAPE_SUBTITLE_ZH_SIZE * scale)),
    )
    landscape_en_width = max(
        12,
        round(
            LANDSCAPE_SUBTITLE_WIDTH
            / max(
                1,
                LANDSCAPE_SUBTITLE_EN_SIZE
                * scale
                * LANDSCAPE_SUBTITLE_EN_WIDTH_FACTOR,
            )
        ),
    )
    for segment in selected:
        start_ms = max(0, segment.start_ms - clip_start_ms)
        end_ms = min(
            clip_end_ms - clip_start_ms,
            segment.end_ms - clip_start_ms,
        )
        if end_ms <= start_ms:
            continue
        if output_layout == "landscape":
            zh_lines = _wrapped_text(
                segment.text,
                width=landscape_zh_width,
                rebalance_cjk_orphan=False,
            )
            en_lines = _wrapped_text(
                translations.get(segment.sequence, ""),
                width=landscape_en_width,
            )
            ass_events, line_cues = _landscape_subtitle_events(
                start_ms=start_ms,
                end_ms=end_ms,
                subtitle_mode=subtitle_mode,
                zh_lines=zh_lines,
                en_lines=en_lines,
                scale=scale,
                font_name=landscape_subtitle_font_name(
                    subtitle_font_family
                ),
                theme=theme,
            )
            events.extend(ass_events)
            raster_cues.extend(line_cues)
            continue
        else:
            zh_lines = _wrapped_text(
                segment.text,
                width=portrait.zh_line_width,
            )
            en_lines = _wrapped_text(
                translations.get(segment.sequence, ""),
                width=portrait.en_line_width,
            )
            zh = r"\N".join(_ass_text(line) for line in zh_lines)
            en = r"\N".join(_ass_text(line) for line in en_lines)
        selected_lines: list[tuple[str, int, int]] = []
        if subtitle_mode in {"zh", "bilingual"}:
            selected_lines.extend(
                (
                    line,
                    max(
                        1,
                        round(
                            portrait.zh_size * LIBASS_CJK_ADVANCE_RATIO
                        ),
                    ),
                    portrait.zh_outline,
                )
                for line in zh_lines
            )
        if subtitle_mode in {"en", "bilingual"}:
            selected_lines.extend(
                (
                    line,
                    max(
                        1,
                        round(
                            portrait.en_size * LIBASS_CJK_ADVANCE_RATIO
                        ),
                    ),
                    portrait.en_outline,
                )
                for line in en_lines
            )
        if any(contains_emoji(line) for line, _, _ in selected_lines):
            line_gap = max(1, round(height * 0.003))
            line_heights = [
                max(size, round(size * 1.35))
                for _, size, _ in selected_lines
            ]
            asset_height = sum(line_heights) + line_gap * max(
                0, len(selected_lines) - 1
            )
            cursor_y = 0
            raster_lines: list[RasterTextLine] = []
            for (line, size, outline), line_height in zip(
                selected_lines, line_heights, strict=True
            ):
                raster_lines.append(
                    RasterTextLine(
                        text=line,
                        x=portrait.band_width // 2,
                        y=cursor_y,
                        font_name=font_name,
                        font_size=size,
                        color=(255, 255, 255, 255),
                        anchor="center",
                        stroke_width=outline,
                        stroke_color=(0, 0, 0, 255),
                    )
                )
                cursor_y += line_height + line_gap
            raster_cues.append(
                RasterOverlayCue(
                    asset=RasterAsset(
                        width=portrait.band_width,
                        height=max(1, asset_height),
                        lines=tuple(raster_lines),
                    ),
                    x=portrait.margin_l,
                    placements=(
                        RasterPlacement(
                            start_ms=start_ms,
                            end_ms=end_ms,
                            y_from=height - portrait.margin_v - asset_height,
                            y_to=height - portrait.margin_v - asset_height,
                        ),
                    ),
                    layer=20,
                )
            )
            continue
        if subtitle_mode == "zh":
            style = "SubtitleZh"
            text = zh
        elif subtitle_mode == "en":
            style = "SubtitleEn"
            text = en
        else:
            style = "SubtitleZh"
            text = f"{zh}\\N{{\\rSubtitleEn}}{en}"
        events.append(
            _dialogue(
                layer=20,
                start_ms=start_ms,
                end_ms=end_ms,
                style=style,
                text=text,
            )
        )
    if not events:
        if allow_empty and not raster_cues:
            return ([], [])
        if raster_cues:
            return (events, raster_cues)
        raise AppError(
            "clip_subtitles_empty",
            "所选范围没有可渲染的字幕",
            False,
        )
    return (events, raster_cues)


def _landscape_watermark_events(
    *,
    duration_ms: int,
    live_started_at: str | None,
) -> list[str]:
    """Credit the tool in one top corner and date the replay in the other.

    The top band of the cream columns is free by construction rather than by
    luck: the danmaku column is bounded below it and fills upward from the
    bottom, and subtitles are centred vertically. Nothing else can reach here
    however busy the clip gets.
    """

    corners = [
        (
            rf"{{\an7\pos("
            rf"{LANDSCAPE_WATERMARK_LEFT},{LANDSCAPE_WATERMARK_LEFT_TOP})}}",
            CLIP_WATERMARK_TEXT,
        )
    ]
    live_time = format_china_datetime(live_started_at)
    if live_time:
        corners.append(
            (
                rf"{{\an9\pos("
                rf"{LANDSCAPE_CANVAS_WIDTH - LANDSCAPE_WATERMARK_RIGHT},"
                rf"{LANDSCAPE_WATERMARK_TOP})}}",
                live_time,
            )
        )
    return [
        _dialogue(
            layer=10,
            start_ms=0,
            end_ms=duration_ms,
            style="LandscapeWatermark",
            text=f"{position}{_ass_text(text)}",
        )
        for position, text in corners
    ]


def _landscape_subtitle_events(
    *,
    start_ms: int,
    end_ms: int,
    subtitle_mode: SubtitleMode,
    zh_lines: list[str],
    en_lines: list[str],
    scale: float,
    font_name: str,
    theme: LandscapeTheme,
) -> tuple[list[str], list[RasterOverlayCue]]:
    paragraphs: list[
        tuple[
            str,
            list[str],
            float,
            int,
            tuple[int, int, int, int],
            int,
            int,
        ]
    ] = []
    if subtitle_mode in {"zh", "bilingual"} and zh_lines:
        paragraphs.append(
            (
                "LandscapeSubtitleZh",
                zh_lines,
                LANDSCAPE_SUBTITLE_ZH_SIZE
                * scale
                * LANDSCAPE_SUBTITLE_LINE_HEIGHT,
                max(16, round(LANDSCAPE_SUBTITLE_ZH_SIZE * scale)),
                _rgba(theme.subtitle_zh),
                LANDSCAPE_SUBTITLE_ZH_SCALE_X,
                LANDSCAPE_SUBTITLE_ZH_SCALE_Y,
            )
        )
    if subtitle_mode in {"en", "bilingual"} and en_lines:
        paragraphs.append(
            (
                "LandscapeSubtitleEn",
                en_lines,
                LANDSCAPE_SUBTITLE_EN_SIZE
                * scale
                * LANDSCAPE_SUBTITLE_LINE_HEIGHT,
                max(13, round(LANDSCAPE_SUBTITLE_EN_SIZE * scale)),
                _rgba(
                    theme.subtitle_en,
                    alpha=round(255 * LANDSCAPE_SUBTITLE_EN_OPACITY),
                ),
                LANDSCAPE_SUBTITLE_EN_SCALE_X,
                LANDSCAPE_SUBTITLE_EN_SCALE_Y,
            )
        )
    total_height = sum(
        (
            LANDSCAPE_SUBTITLE_PARAGRAPH_GAP
            + len(lines) * line_height
        )
        for _, lines, line_height, _, _, _, _ in paragraphs
    )
    cursor_y = (LANDSCAPE_CANVAS_HEIGHT - total_height) / 2
    events: list[str] = []
    raster_cues: list[RasterOverlayCue] = []
    for (
        style,
        lines,
        line_height,
        font_size,
        color,
        scale_x,
        scale_y,
    ) in paragraphs:
        cursor_y += LANDSCAPE_SUBTITLE_PARAGRAPH_GAP
        for line in lines:
            position_y = round(
                cursor_y + LANDSCAPE_SUBTITLE_POSITION_OFFSET
            )
            if contains_emoji(line):
                asset_height = max(
                    1, math.ceil(font_size * 1.3 * scale_y / 100)
                )
                raster_cues.append(
                    RasterOverlayCue(
                        asset=RasterAsset(
                            width=LANDSCAPE_SUBTITLE_WIDTH,
                            height=asset_height,
                            lines=(
                                RasterTextLine(
                                    text=line,
                                    x=0,
                                    y=0,
                                    font_name=font_name,
                                    font_size=font_size,
                                    color=color,
                                    scale_x=scale_x,
                                    scale_y=scale_y,
                                ),
                            ),
                        ),
                        x=LANDSCAPE_SUBTITLE_LEFT,
                        placements=(
                            RasterPlacement(
                                start_ms=start_ms,
                                end_ms=end_ms,
                                y_from=position_y,
                                y_to=position_y,
                            ),
                        ),
                        layer=20,
                    ),
                )
            else:
                events.append(
                    _dialogue(
                        layer=20,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        style=style,
                        text=(
                            rf"{{\pos({LANDSCAPE_SUBTITLE_LEFT},{position_y})}}"
                            f"{_ass_text(line)}"
                        ),
                    )
                )
            cursor_y += line_height
    return (events, raster_cues)


@dataclass(frozen=True)
class _DanmakuCardGeometry:
    """Card metrics shared by both layouts.

    Portrait and landscape drew their stacks from separate code paths, which
    is how the landscape redesign left portrait on the old fixed-slot boxes.
    One geometry object keeps a single renderer honest for both.
    """

    style_prefix: str
    card_width: int
    right: int
    bottom: int
    top: int
    gap: int
    padding_x: int
    padding_y: int
    radius: int
    author_line_height: int
    body_line_height: int
    body_line_width: int
    text_gap: int
    max_stack: int

    def card_height(self, body_lines: int) -> int:
        return (
            self.padding_y * 2
            + self.author_line_height
            + self.text_gap
            + body_lines * self.body_line_height
        )


def _landscape_card_geometry() -> _DanmakuCardGeometry:
    return _DanmakuCardGeometry(
        style_prefix="Landscape",
        card_width=LANDSCAPE_DANMAKU_WIDTH,
        right=LANDSCAPE_DANMAKU_RIGHT,
        bottom=LANDSCAPE_DANMAKU_BOTTOM,
        top=LANDSCAPE_DANMAKU_TOP,
        gap=LANDSCAPE_DANMAKU_GAP,
        padding_x=LANDSCAPE_DANMAKU_PADDING_X,
        padding_y=LANDSCAPE_DANMAKU_PADDING_Y,
        radius=LANDSCAPE_DANMAKU_RADIUS,
        author_line_height=LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT,
        body_line_height=LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT,
        body_line_width=max(
            8,
            (LANDSCAPE_DANMAKU_WIDTH - 2 * LANDSCAPE_DANMAKU_PADDING_X)
            // LANDSCAPE_DANMAKU_BODY_SIZE,
        ),
        text_gap=LANDSCAPE_DANMAKU_TEXT_GAP,
        max_stack=DANMAKU_MAX_STACK,
    )


def _portrait_card_geometry(width: int, height: int) -> _DanmakuCardGeometry:
    card_width = max(80, round(width * PORTRAIT_DANMAKU_WIDTH_RATIO))
    padding_x = max(6, round(width * PORTRAIT_DANMAKU_PADDING_X_RATIO))
    author_size = max(10, round(height * PORTRAIT_DANMAKU_AUTHOR_SIZE_RATIO))
    body_size = max(12, round(height * PORTRAIT_DANMAKU_BODY_SIZE_RATIO))
    return _DanmakuCardGeometry(
        style_prefix="Portrait",
        card_width=card_width,
        right=max(6, round(width * PORTRAIT_DANMAKU_RIGHT_RATIO)),
        bottom=max(6, round(height * PORTRAIT_DANMAKU_BOTTOM_RATIO)),
        top=max(6, round(height * PORTRAIT_DANMAKU_TOP_RATIO)),
        gap=max(3, round(height * PORTRAIT_DANMAKU_GAP_RATIO)),
        padding_x=padding_x,
        padding_y=max(4, round(height * PORTRAIT_DANMAKU_PADDING_Y_RATIO)),
        radius=max(4, round(height * PORTRAIT_DANMAKU_RADIUS_RATIO)),
        author_line_height=round(
            author_size * PORTRAIT_DANMAKU_AUTHOR_LINE_HEIGHT
        ),
        body_line_height=round(body_size * PORTRAIT_DANMAKU_LINE_HEIGHT),
        body_line_width=max(
            6,
            (card_width - 2 * padding_x) // body_size,
        ),
        text_gap=max(1, round(height * PORTRAIT_DANMAKU_TEXT_GAP_RATIO)),
        max_stack=DANMAKU_MAX_STACK,
    )


def _danmaku_events(
    *,
    width: int,
    height: int,
    clip_start_ms: int,
    clip_end_ms: int,
    danmaku: list[DanmakuEntry],
    output_layout: ClipOutputLayout,
    font_name: str,
    theme: LandscapeTheme,
) -> tuple[list[str], int, list[RasterOverlayCue]]:
    geometry = (
        _landscape_card_geometry()
        if output_layout == "landscape"
        else _portrait_card_geometry(width, height)
    )
    selected = sorted(
        (
            entry
            for entry in danmaku
            if clip_start_ms <= entry.timestamp_ms < clip_end_ms
        ),
        key=lambda entry: (entry.timestamp_ms, entry.sequence),
    )
    prepared: list[_PreparedDanmaku] = []
    last_accepted_ms = -DANMAKU_MIN_GAP_MS
    for entry in selected:
        relative_ms = entry.timestamp_ms - clip_start_ms
        if relative_ms - last_accepted_ms < DANMAKU_MIN_GAP_MS:
            continue
        author_plain = _truncate(_plain_text(entry.author), 18) or "匿名"
        body_lines_plain = _wrapped_text(
            _plain_text(entry.text),
            width=geometry.body_line_width,
        )
        if len(body_lines_plain) > 3:
            body_lines_plain = body_lines_plain[:3]
            body_lines_plain[-1] = (
                _truncate(
                    body_lines_plain[-1],
                    max(1, geometry.body_line_width - 1),
                )
                + "…"
            )
        if not body_lines_plain:
            continue
        prepared.append(
            _PreparedDanmaku(
                relative_ms=relative_ms,
                author=_ass_text(author_plain),
                body=r"\N".join(
                    _ass_text(line) for line in body_lines_plain
                ),
                author_plain=author_plain,
                body_lines_plain=tuple(body_lines_plain),
                body_lines=len(body_lines_plain),
            )
        )
        last_accepted_ms = relative_ms

    events: list[str] = []
    raster_cues: list[RasterOverlayCue] = []
    clip_duration_ms = clip_end_ms - clip_start_ms
    # Mirror the browser's variable-height, bottom-anchored card stack.
    heights = [geometry.card_height(item.body_lines) for item in prepared]
    budget = max(0, height - geometry.bottom - geometry.top)
    card_x = width - geometry.right - geometry.card_width
    box_style = f"{geometry.style_prefix}DanmakuBox"
    author_style = f"{geometry.style_prefix}DanmakuAuthor"
    body_style = f"{geometry.style_prefix}Danmaku"
    rasterize_all = any(
        contains_emoji(item.author_plain)
        or any(
            contains_emoji(line) for line in item.body_lines_plain
        )
        for item in prepared
    )
    for index, item in enumerate(prepared):
        raster_placements: list[RasterPlacement] = []
        rasterized = rasterize_all
        maximum_age = min(
            geometry.max_stack - 1,
            len(prepared) - index - 1,
        )
        for age in range(maximum_age + 1):
            latest_index = index + age
            stack_height = (
                sum(heights[index : latest_index + 1])
                + age * geometry.gap
            )
            if stack_height > budget:
                break
            segment_start_ms = prepared[latest_index].relative_ms
            segment_end_ms = (
                prepared[latest_index + 1].relative_ms
                if latest_index + 1 < len(prepared)
                else clip_duration_ms
            )
            if segment_end_ms <= segment_start_ms:
                continue
            rise_ms = min(
                DANMAKU_RISE_MS,
                segment_end_ms - segment_start_ms,
            )
            card_y = (
                height
                - geometry.bottom
                - sum(heights[index : latest_index + 1])
                - age * geometry.gap
            )
            text_x = card_x + geometry.padding_x
            if age == 0:
                card_position = f"\\pos({card_x},{card_y})\\fad(120,0)"
                text_position = (
                    f"\\pos({text_x},"
                    f"{card_y + geometry.padding_y})\\fad(120,0)"
                )
            else:
                previous_y = (
                    height
                    - geometry.bottom
                    - sum(heights[index:latest_index])
                    - (age - 1) * geometry.gap
                )
                card_position = (
                    f"\\move({card_x},{previous_y},"
                    f"{card_x},{card_y},0,{rise_ms})"
                )
                text_position = (
                    f"\\move({text_x},"
                    f"{previous_y + geometry.padding_y},"
                    f"{text_x},"
                    f"{card_y + geometry.padding_y},"
                    f"0,{rise_ms})"
                )
            box = _ass_rounded_rect(
                geometry.card_width,
                heights[index],
                geometry.radius,
            )
            if rasterized:
                raster_placements.append(
                    RasterPlacement(
                        start_ms=segment_start_ms,
                        end_ms=segment_end_ms,
                        y_from=previous_y if age else card_y,
                        y_to=card_y,
                        move_ms=rise_ms if age else 0,
                    )
                )
                continue
            # Fixed-width ASS cards need a vector box behind the text.
            events.append(
                _dialogue(
                    layer=9,
                    start_ms=segment_start_ms,
                    end_ms=segment_end_ms,
                    style=box_style,
                    text=(
                        f"{{\\an7{card_position}\\p1}}"
                        f"{box}{{\\p0}}"
                    ),
                )
            )
            events.append(
                _dialogue(
                    layer=10,
                    start_ms=segment_start_ms,
                    end_ms=segment_end_ms,
                    style=author_style,
                    text=(
                        f"{{\\an7{text_position}}}"
                        f"{item.author}\\N"
                        f"{{\\r{body_style}}}{item.body}"
                    ),
                )
            )
        if rasterized and raster_placements:
            raster_cues.append(
                _danmaku_raster_cue(
                    item=item,
                    geometry=geometry,
                    height=heights[index],
                    x=card_x,
                    placements=tuple(raster_placements),
                    output_layout=output_layout,
                    font_name=font_name,
                    theme=theme,
                )
            )
    return events, len(prepared), raster_cues


def _danmaku_raster_cue(
    *,
    item: _PreparedDanmaku,
    geometry: _DanmakuCardGeometry,
    height: int,
    x: int,
    placements: tuple[RasterPlacement, ...],
    output_layout: ClipOutputLayout,
    font_name: str,
    theme: LandscapeTheme,
) -> RasterOverlayCue:
    if output_layout == "landscape":
        author_size = LANDSCAPE_DANMAKU_AUTHOR_SIZE
        body_size = LANDSCAPE_DANMAKU_BODY_SIZE
        author_color = _rgba(theme.danmaku_author)
        body_color = _rgba(theme.danmaku_text)
        box = RasterBox(
            fill=_rgba(theme.danmaku_background, alpha=243),
            radius=geometry.radius,
            outline=_rgba(theme.danmaku_author, alpha=87),
            outline_width=2,
            shadow=_rgba(theme.danmaku_text, alpha=31),
            shadow_offset=3,
            shadow_blur=3,
        )
    else:
        author_size = max(
            10,
            round(
                geometry.author_line_height
                / PORTRAIT_DANMAKU_AUTHOR_LINE_HEIGHT
            ),
        )
        body_size = max(
            12,
            round(
                geometry.body_line_height / PORTRAIT_DANMAKU_LINE_HEIGHT
            ),
        )
        author_color = _rgba(PORTRAIT_DANMAKU_AUTHOR_COLOR, alpha=199)
        body_color = _rgba(PORTRAIT_DANMAKU_TEXT_COLOR, alpha=224)
        box = RasterBox(
            fill=_rgba(PORTRAIT_DANMAKU_BACKGROUND_COLOR, alpha=61),
            radius=geometry.radius,
            shadow=(0, 0, 0, 26),
            shadow_offset=max(1, round(geometry.radius * 0.2)),
            shadow_blur=max(1, round(geometry.radius * 0.3)),
        )
    lines = [
        RasterTextLine(
            text=item.author_plain,
            x=geometry.padding_x,
            y=geometry.padding_y,
            font_name=font_name,
            font_size=author_size,
            color=author_color,
        )
    ]
    body_y = (
        geometry.padding_y
        + geometry.author_line_height
        + geometry.text_gap
    )
    lines.extend(
        RasterTextLine(
            text=line,
            x=geometry.padding_x,
            y=body_y + index * geometry.body_line_height,
            font_name=font_name,
            font_size=body_size,
            color=body_color,
        )
        for index, line in enumerate(item.body_lines_plain)
    )
    return RasterOverlayCue(
        asset=RasterAsset(
            width=geometry.card_width,
            height=height,
            lines=tuple(lines),
            box=box,
        ),
        x=x,
        placements=placements,
        layer=10,
        fade_in_ms=120,
    )


def _ass_header(
    *,
    width: int,
    height: int,
    font_name: str,
    reserve_danmaku: bool,
    subtitle_font_scale: int,
    output_layout: ClipOutputLayout,
    subtitle_font_family: LandscapeSubtitleFont,
    theme: LandscapeTheme,
) -> str:
    if output_layout not in {"portrait", "landscape"}:
        raise AppError(
            "clip_layout_invalid",
            "视频画面方向无效",
            False,
        )
    if not (
        SUBTITLE_FONT_SCALE_MIN
        <= subtitle_font_scale
        <= SUBTITLE_FONT_SCALE_MAX
    ):
        raise AppError(
            "clip_subtitle_style_invalid",
            "字幕字号比例无效",
            False,
        )
    try:
        landscape_subtitle_color = _ass_color(theme.subtitle_zh)
        landscape_subtitle_en_color = _ass_color(
            theme.subtitle_en,
            alpha=round(255 * (1 - LANDSCAPE_SUBTITLE_EN_OPACITY)),
        )
        landscape_danmaku_text = _ass_color(theme.danmaku_text)
        landscape_danmaku_author = _ass_color(theme.danmaku_author)
        landscape_danmaku_background = _ass_color(
            theme.danmaku_background,
            alpha=12,
        )
    except ValueError as exc:
        raise AppError(
            "clip_subtitle_style_invalid",
            "字幕颜色格式无效",
            False,
        ) from exc
    try:
        landscape_font_name = landscape_subtitle_font_name(
            subtitle_font_family
        )
    except ValueError as exc:
        raise AppError(
            "clip_subtitle_style_invalid",
            "字幕字体无效",
            False,
        ) from exc
    scale = _subtitle_scale(subtitle_font_scale)
    portrait = _portrait_subtitle_metrics(
        width=width,
        height=height,
        subtitle_font_scale=subtitle_font_scale,
        reserve_danmaku=reserve_danmaku,
    )
    zh_size = portrait.zh_size
    en_size = portrait.en_size
    zh_outline = portrait.zh_outline
    en_outline = portrait.en_outline
    portrait_author_size = round(
        max(10, round(height * PORTRAIT_DANMAKU_AUTHOR_SIZE_RATIO))
        * LANDSCAPE_LIBASS_DANMAKU_AUTHOR_SCALE
    )
    portrait_body_size = round(
        max(12, round(height * PORTRAIT_DANMAKU_BODY_SIZE_RATIO))
        * LANDSCAPE_LIBASS_FONT_SCALE
    )
    portrait_danmaku_author = _ass_color(
        PORTRAIT_DANMAKU_AUTHOR_COLOR,
        alpha=56,
    )
    portrait_danmaku_text = _ass_color(PORTRAIT_DANMAKU_TEXT_COLOR, alpha=31)
    portrait_danmaku_background = _ass_color(
        PORTRAIT_DANMAKU_BACKGROUND_COLOR,
        alpha=194,
    )
    landscape_zh_size = max(
        16,
        round(
            LANDSCAPE_SUBTITLE_ZH_SIZE
            * scale
            * LANDSCAPE_LIBASS_FONT_SCALE
        ),
    )
    landscape_en_size = max(
        13,
        round(
            LANDSCAPE_SUBTITLE_EN_SIZE
            * scale
            * LANDSCAPE_LIBASS_FONT_SCALE
        ),
    )
    landscape_danmaku_size = round(
        LANDSCAPE_DANMAKU_BODY_SIZE * LANDSCAPE_LIBASS_FONT_SCALE
    )
    landscape_danmaku_author_size = round(
        LANDSCAPE_DANMAKU_AUTHOR_SIZE
        * LANDSCAPE_LIBASS_DANMAKU_AUTHOR_SCALE
    )
    margin_v = portrait.margin_v
    margin_l = portrait.margin_l
    margin_r = portrait.margin_r
    landscape_danmaku_border = _ass_color(
        theme.danmaku_author,
        alpha=168,
    )
    landscape_danmaku_box_shadow = _ass_color(
        theme.danmaku_text,
        alpha=224,
    )
    landscape_watermark_color = _ass_color(
        theme.watermark,
        alpha=LANDSCAPE_WATERMARK_ALPHA,
    )
    landscape_watermark_size = round(
        LANDSCAPE_WATERMARK_SIZE * LANDSCAPE_LIBASS_FONT_SCALE
    )
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SubtitleZh,{font_name},{zh_size},{PORTRAIT_SUBTITLE_COLOR},{PORTRAIT_SUBTITLE_COLOR},{PORTRAIT_SUBTITLE_OUTLINE_COLOR},{PORTRAIT_SUBTITLE_OUTLINE_COLOR},-1,0,0,0,100,100,0,0,1,{zh_outline},0,2,{margin_l},{margin_r},{margin_v},1
Style: SubtitleEn,{font_name},{en_size},{PORTRAIT_SUBTITLE_COLOR},{PORTRAIT_SUBTITLE_COLOR},{PORTRAIT_SUBTITLE_OUTLINE_COLOR},{PORTRAIT_SUBTITLE_OUTLINE_COLOR},0,0,0,0,100,100,0,0,1,{en_outline},0,2,{margin_l},{margin_r},{margin_v},1
Style: PortraitDanmaku,{font_name},{portrait_body_size},{portrait_danmaku_text},{portrait_danmaku_text},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: PortraitDanmakuAuthor,{font_name},{portrait_author_size},{portrait_danmaku_author},{portrait_danmaku_author},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: PortraitDanmakuBox,{font_name},1,{portrait_danmaku_background},{portrait_danmaku_background},{portrait_danmaku_background},{portrait_danmaku_background},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LandscapeSubtitleZh,{landscape_font_name},{landscape_zh_size},{landscape_subtitle_color},{landscape_subtitle_color},&H00000000,&H00000000,-1,0,0,0,{LANDSCAPE_SUBTITLE_ZH_SCALE_X},{LANDSCAPE_SUBTITLE_ZH_SCALE_Y},0,0,1,0,0,7,0,0,0,1
Style: LandscapeSubtitleEn,{landscape_font_name},{landscape_en_size},{landscape_subtitle_en_color},{landscape_subtitle_en_color},&H00000000,&H00000000,-1,0,0,0,{LANDSCAPE_SUBTITLE_EN_SCALE_X},{LANDSCAPE_SUBTITLE_EN_SCALE_Y},0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmaku,{font_name},{landscape_danmaku_size},{landscape_danmaku_text},{landscape_danmaku_text},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmakuAuthor,{font_name},{landscape_danmaku_author_size},{landscape_danmaku_author},{landscape_danmaku_author},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmakuBox,{font_name},1,{landscape_danmaku_background},{landscape_danmaku_background},{landscape_danmaku_border},{landscape_danmaku_box_shadow},0,0,0,0,100,100,0,0,1,2,3,7,0,0,0,1
Style: LandscapeWatermark,{landscape_font_name},{landscape_watermark_size},{landscape_watermark_color},{landscape_watermark_color},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def normalize_subtitle_color(value: str) -> str:
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("expected #RRGGBB")
    return value.upper()


def normalize_cover_title(value: str) -> str:
    return _plain_text(value)


def normalize_ai_cover_title(value: str) -> str:
    normalized = _plain_text(value)
    if not normalized or len(normalized) > AI_COVER_TITLE_MAX_LENGTH:
        raise ValueError(
            f"AI cover title must contain 1-{AI_COVER_TITLE_MAX_LENGTH} characters"
        )
    return normalized


def normalize_ai_cover_highlight(value: str) -> str:
    normalized = _plain_text(value)
    if len(normalized) > AI_COVER_HIGHLIGHT_MAX_LENGTH:
        raise ValueError(
            "AI cover highlight must not exceed "
            f"{AI_COVER_HIGHLIGHT_MAX_LENGTH} characters"
        )
    return normalized


def normalize_ai_cover_layout_style(
    value: str,
) -> AICoverLayoutStyle:
    if value not in {
        "sticker_pop",
        "editorial_arc",
        "banner_energy",
    }:
        raise ValueError("unsupported AI cover layout style")
    return cast(AICoverLayoutStyle, value)


def normalize_ai_cover_extra_text(
    values: list[str] | tuple[str, ...],
) -> list[str]:
    if len(values) > AI_COVER_EXTRA_TEXT_MAX_ITEMS:
        raise ValueError(
            "AI cover supports at most "
            f"{AI_COVER_EXTRA_TEXT_MAX_ITEMS} extra text lines"
        )
    normalized: list[str] = []
    for value in values:
        item = _plain_text(str(value))
        if not item:
            continue
        if len(item) > AI_COVER_EXTRA_TEXT_MAX_LENGTH:
            raise ValueError(
                "AI cover extra text must not exceed "
                f"{AI_COVER_EXTRA_TEXT_MAX_LENGTH} characters"
            )
        normalized.append(item)
    return normalized


def _ass_color(value: str, *, alpha: int = 0) -> str:
    normalized = normalize_subtitle_color(value)
    if not 0 <= alpha <= 255:
        raise ValueError("alpha must be between 0 and 255")
    red = normalized[1:3]
    green = normalized[3:5]
    blue = normalized[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}"


def _rgba(
    value: str, *, alpha: int = 255
) -> tuple[int, int, int, int]:
    normalized = normalize_subtitle_color(value)
    if not 0 <= alpha <= 255:
        raise ValueError("alpha must be between 0 and 255")
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
        alpha,
    )


def _subtitle_scale(value: int) -> float:
    return value / 100 * SUBTITLE_FONT_BASE_SCALE


def _ass_rounded_rect(width: int, height: int, radius: int) -> str:
    radius = max(0, min(radius, width // 2, height // 2))
    right = width
    bottom = height
    return (
        f"m {radius} 0 "
        f"l {right - radius} 0 "
        f"b {right} 0 {right} 0 {right} {radius} "
        f"l {right} {bottom - radius} "
        f"b {right} {bottom} {right} {bottom} "
        f"{right - radius} {bottom} "
        f"l {radius} {bottom} "
        f"b 0 {bottom} 0 {bottom} 0 {bottom - radius} "
        f"l 0 {radius} "
        f"b 0 0 0 0 {radius} 0"
    )


def _dialogue(
    *,
    layer: int,
    start_ms: int,
    end_ms: int,
    style: str,
    text: str,
) -> str:
    return (
        f"Dialogue: {layer},{_ass_time(start_ms)},{_ass_time(end_ms)},"
        f"{style},,0,0,0,,{text}"
    )


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, milliseconds) // 10
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _plain_text(value: str) -> str:
    return " ".join(
        strip_control_chars(value).replace("\r", " ").replace("\n", " ").split()
    )


def _ass_text(value: str) -> str:
    return (
        _plain_text(value)
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def _wrapped_ass_text(value: str, *, width: int, lines: int) -> str:
    wrapped = _wrapped_text(value, width=width)
    if not wrapped:
        return ""
    if len(wrapped) > lines:
        wrapped = wrapped[:lines]
        wrapped[-1] = _truncate(wrapped[-1], max(1, width - 1)) + "…"
    return r"\N".join(_ass_text(line) for line in wrapped)


def _ass_wrapped_text(value: str, *, width: int) -> str:
    return r"\N".join(
        _ass_text(line)
        for line in _wrapped_text(value, width=width)
    )


def _wrapped_text(
    value: str,
    *,
    width: int,
    rebalance_cjk_orphan: bool = True,
) -> list[str]:
    normalized = _plain_text(value)
    emoji_placeholders: dict[str, str] = {}
    protected_parts: list[str] = []
    for is_emoji, part in split_emoji_runs(normalized):
        if not is_emoji:
            protected_parts.append(part)
            continue
        placeholder = chr(0xE000 + len(emoji_placeholders))
        emoji_placeholders[placeholder] = part
        protected_parts.append(placeholder)
    protected = "".join(protected_parts)
    wrapped = textwrap.wrap(
        protected,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    wrapped = [
        "".join(emoji_placeholders.get(char, char) for char in line)
        for line in wrapped
    ]
    for index in range(1, len(wrapped)):
        while (
            wrapped[index]
            and wrapped[index][0] in CJK_CLOSING_PUNCTUATION
            and wrapped[index - 1]
        ):
            wrapped[index] = wrapped[index - 1][-1] + wrapped[index]
            wrapped[index - 1] = wrapped[index - 1][:-1]
    if (
        rebalance_cjk_orphan
        and len(wrapped) > 1
        and " " not in normalized
        and re.search(r"[\u3400-\u9fff]", normalized)
        and len(wrapped[-1]) < 4
    ):
        move_count = min(4 - len(wrapped[-1]), len(wrapped[-2]) - 1)
        if move_count > 0:
            wrapped[-1] = wrapped[-2][-move_count:] + wrapped[-1]
            wrapped[-2] = wrapped[-2][:-move_count]
    return [line for line in wrapped if line]


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"
