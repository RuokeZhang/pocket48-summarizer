from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Literal

from ..errors import AppError
from ..models import DanmakuEntry, TranscriptSegment
from ..security import strip_control_chars
from .layouts import (
    LANDSCAPE_CANVAS_WIDTH,
    LANDSCAPE_CANVAS_HEIGHT,
    LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT,
    LANDSCAPE_DANMAKU_AUTHOR_SIZE,
    LANDSCAPE_DANMAKU_AUTHOR_COLOR,
    LANDSCAPE_DANMAKU_BACKGROUND_COLOR,
    LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT,
    LANDSCAPE_DANMAKU_BODY_SIZE,
    LANDSCAPE_DANMAKU_BOTTOM,
    LANDSCAPE_DANMAKU_GAP,
    LANDSCAPE_DANMAKU_PADDING_X,
    LANDSCAPE_DANMAKU_PADDING_Y,
    LANDSCAPE_DANMAKU_RADIUS,
    LANDSCAPE_DANMAKU_RIGHT,
    LANDSCAPE_DANMAKU_TEXT_COLOR,
    LANDSCAPE_DANMAKU_TEXT_GAP,
    LANDSCAPE_DANMAKU_WIDTH,
    LANDSCAPE_LIBASS_DANMAKU_AUTHOR_SCALE,
    LANDSCAPE_LIBASS_FONT_SCALE,
    LANDSCAPE_SUBTITLE_COLOR,
    LANDSCAPE_SUBTITLE_EN_COLOR,
    LANDSCAPE_SUBTITLE_EN_SIZE,
    LANDSCAPE_SUBTITLE_LEFT,
    LANDSCAPE_SUBTITLE_WIDTH,
    LANDSCAPE_SUBTITLE_ZH_SIZE,
    LANDSCAPE_VIDEO_WIDTH,
    DEFAULT_LANDSCAPE_SUBTITLE_FONT,
    ClipOutputLayout,
    LandscapeSubtitleFont,
    landscape_subtitle_font_name,
)

SubtitleMode = Literal["off", "zh", "en", "bilingual"]
CoverStyle = Literal["scrim", "display", "badge"]
DANMAKU_MIN_GAP_MS = 450
DANMAKU_MAX_VISIBLE = 5
DANMAKU_RISE_MS = 220
SUBTITLE_FONT_SCALE_MIN = 50
SUBTITLE_FONT_SCALE_MAX = 150
DEFAULT_SUBTITLE_FONT_SCALE = 100
SUBTITLE_FONT_BASE_SCALE = 1.6
DEFAULT_SUBTITLE_TEXT_COLOR = "#E43D12"
DEFAULT_SUBTITLE_BACKGROUND_COLOR = "#EBE9E1"
MIN_SUBTITLE_CONTRAST_RATIO = 3.0
COVER_DURATION_MS = 1500
COVER_TITLE_MAX_LENGTH = 40
DEFAULT_COVER_STYLE: CoverStyle = "scrim"
COVER_LIBASS_FONT_SCALE = 1.45
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


@dataclass(frozen=True, slots=True)
class CoverOverlayDocument:
    content: str
    title: str
    style: CoverStyle


@dataclass(frozen=True, slots=True)
class _PreparedDanmaku:
    relative_ms: int
    author: str
    body: str
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
    subtitle_text_color: str = DEFAULT_SUBTITLE_TEXT_COLOR,
    subtitle_background_color: str = DEFAULT_SUBTITLE_BACKGROUND_COLOR,
    output_layout: ClipOutputLayout = "portrait",
    subtitle_font_family: LandscapeSubtitleFont = (
        DEFAULT_LANDSCAPE_SUBTITLE_FONT
    ),
    allow_empty_subtitles: bool = False,
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
    subtitle_events = _subtitle_events(
        clip_start_ms=clip_start_ms,
        clip_end_ms=clip_end_ms,
        subtitle_mode=subtitle_mode,
        transcript=transcript,
        translations=translations,
        output_layout=output_layout,
        subtitle_font_scale=subtitle_font_scale,
        allow_empty=allow_empty_subtitles,
    )
    danmaku_events, danmaku_count = (
        _danmaku_events(
            width=width,
            height=height,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            danmaku=danmaku,
            output_layout=output_layout,
        )
        if include_danmaku
        else ([], 0)
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
        subtitle_text_color=subtitle_text_color,
        subtitle_background_color=subtitle_background_color,
        output_layout=output_layout,
        subtitle_font_family=subtitle_font_family,
    )
    return ClipOverlayDocument(
        content="\n".join([header, *subtitle_events, *danmaku_events, ""]),
        subtitle_event_count=len(subtitle_events),
        danmaku_event_count=danmaku_count,
        warning_message=warning,
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
    )


def _subtitle_events(
    *,
    clip_start_ms: int,
    clip_end_ms: int,
    subtitle_mode: SubtitleMode,
    transcript: list[TranscriptSegment],
    translations: dict[int, str],
    output_layout: ClipOutputLayout,
    subtitle_font_scale: int,
    allow_empty: bool,
) -> list[str]:
    if subtitle_mode == "off":
        return []
    selected = [
        segment
        for segment in transcript
        if segment.end_ms > clip_start_ms
        and segment.start_ms < clip_end_ms
    ]
    if not selected:
        if allow_empty:
            return []
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
    scale = _subtitle_scale(subtitle_font_scale)
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
            events.extend(
                _landscape_subtitle_events(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    subtitle_mode=subtitle_mode,
                    zh_lines=zh_lines,
                    en_lines=en_lines,
                    scale=scale,
                )
            )
            continue
        else:
            zh = _ass_text(segment.text)
            en = _ass_text(translations.get(segment.sequence, ""))
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
        if allow_empty:
            return []
        raise AppError(
            "clip_subtitles_empty",
            "所选范围没有可渲染的字幕",
            False,
        )
    return events


def _landscape_subtitle_events(
    *,
    start_ms: int,
    end_ms: int,
    subtitle_mode: SubtitleMode,
    zh_lines: list[str],
    en_lines: list[str],
    scale: float,
) -> list[str]:
    paragraphs: list[tuple[str, list[str], float]] = []
    if subtitle_mode in {"zh", "bilingual"} and zh_lines:
        paragraphs.append(
            (
                "LandscapeSubtitleZh",
                zh_lines,
                LANDSCAPE_SUBTITLE_ZH_SIZE
                * scale
                * LANDSCAPE_SUBTITLE_LINE_HEIGHT,
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
            )
        )
    total_height = sum(
        (
            LANDSCAPE_SUBTITLE_PARAGRAPH_GAP
            + len(lines) * line_height
        )
        for _, lines, line_height in paragraphs
    )
    cursor_y = (LANDSCAPE_CANVAS_HEIGHT - total_height) / 2
    events: list[str] = []
    for style, lines, line_height in paragraphs:
        cursor_y += LANDSCAPE_SUBTITLE_PARAGRAPH_GAP
        for line in lines:
            position_y = round(
                cursor_y + LANDSCAPE_SUBTITLE_POSITION_OFFSET
            )
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
    return events


def _danmaku_events(
    *,
    width: int,
    height: int,
    clip_start_ms: int,
    clip_end_ms: int,
    danmaku: list[DanmakuEntry],
    output_layout: ClipOutputLayout,
) -> tuple[list[str], int]:
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
    landscape = output_layout == "landscape"
    right_margin = max(14, round(width * 0.025))
    top_margin = max(14, round(height * 0.035))
    slot_step = max(48, round(height * 0.115))
    x = width - right_margin
    author_size = max(13, round(height * 0.016))
    landscape_body_width = max(
        8,
        (
            LANDSCAPE_DANMAKU_WIDTH
            - 2 * LANDSCAPE_DANMAKU_PADDING_X
        )
        // LANDSCAPE_DANMAKU_BODY_SIZE,
    )
    for entry in selected:
        relative_ms = entry.timestamp_ms - clip_start_ms
        if relative_ms - last_accepted_ms < DANMAKU_MIN_GAP_MS:
            continue
        author = _ass_text(_truncate(_plain_text(entry.author), 18))
        body = _wrapped_ass_text(
            _plain_text(entry.text),
            width=landscape_body_width if landscape else 18,
            lines=3,
        )
        if not body:
            continue
        prepared.append(
            _PreparedDanmaku(
                relative_ms=relative_ms,
                author=author or "匿名",
                body=body,
                body_lines=body.count(r"\N") + 1,
            )
        )
        last_accepted_ms = relative_ms

    events: list[str] = []
    clip_duration_ms = clip_end_ms - clip_start_ms
    bottom_y = top_margin + (DANMAKU_MAX_VISIBLE - 1) * slot_step
    # Mirror the browser's variable-height, bottom-anchored card stack.
    landscape_heights = (
        [
            (
                LANDSCAPE_DANMAKU_PADDING_Y * 2
                + LANDSCAPE_DANMAKU_AUTHOR_LINE_HEIGHT
                + LANDSCAPE_DANMAKU_TEXT_GAP
                + item.body_lines * LANDSCAPE_DANMAKU_BODY_LINE_HEIGHT
            )
            for item in prepared
        ]
        if landscape
        else []
    )
    for index, item in enumerate(prepared):
        style = "LandscapeDanmakuAuthor" if landscape else "Danmaku"
        body_style = "LandscapeDanmaku" if landscape else "Danmaku"
        maximum_age = min(
            DANMAKU_MAX_VISIBLE - 1,
            len(prepared) - index - 1,
        )
        for age in range(maximum_age + 1):
            latest_index = index + age
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
            if landscape:
                bubble_x = (
                    width
                    - LANDSCAPE_DANMAKU_RIGHT
                    - LANDSCAPE_DANMAKU_WIDTH
                )
                bubble_y = (
                    height
                    - LANDSCAPE_DANMAKU_BOTTOM
                    - sum(landscape_heights[index : latest_index + 1])
                    - age * LANDSCAPE_DANMAKU_GAP
                )
                if age == 0:
                    bubble_position = (
                        f"\\pos({bubble_x},{bubble_y})\\fad(120,0)"
                    )
                    text_position = (
                        "\\pos("
                        f"{bubble_x + LANDSCAPE_DANMAKU_PADDING_X},"
                        f"{bubble_y + LANDSCAPE_DANMAKU_PADDING_Y}"
                        ")\\fad(120,0)"
                    )
                else:
                    previous_bubble_y = (
                        height
                        - LANDSCAPE_DANMAKU_BOTTOM
                        - sum(
                            landscape_heights[index:latest_index]
                        )
                        - (age - 1) * LANDSCAPE_DANMAKU_GAP
                    )
                    bubble_position = (
                        f"\\move({bubble_x},{previous_bubble_y},"
                        f"{bubble_x},{bubble_y},0,{rise_ms})"
                    )
                    text_x = (
                        bubble_x + LANDSCAPE_DANMAKU_PADDING_X
                    )
                    text_position = (
                        f"\\move({text_x},"
                        f"{previous_bubble_y + LANDSCAPE_DANMAKU_PADDING_Y},"
                        f"{text_x},"
                        f"{bubble_y + LANDSCAPE_DANMAKU_PADDING_Y},"
                        f"0,{rise_ms})"
                    )
                box = _ass_rounded_rect(
                    LANDSCAPE_DANMAKU_WIDTH,
                    landscape_heights[index],
                    LANDSCAPE_DANMAKU_RADIUS,
                )
                # Fixed-width ASS cards need a vector box behind the text.
                events.append(
                    _dialogue(
                        layer=9,
                        start_ms=segment_start_ms,
                        end_ms=segment_end_ms,
                        style="LandscapeDanmakuBox",
                        text=(
                            f"{{\\an7{bubble_position}\\p1}}"
                            f"{box}{{\\p0}}"
                        ),
                    )
                )
                events.append(
                    _dialogue(
                        layer=10,
                        start_ms=segment_start_ms,
                        end_ms=segment_end_ms,
                        style=style,
                        text=(
                            f"{{\\an7{text_position}}}"
                            f"{item.author}\\N"
                            f"{{\\r{body_style}}}{item.body}"
                        ),
                    )
                )
                continue
            y = bottom_y - age * slot_step
            if age == 0:
                position = f"\\pos({x},{y})\\fad(120,0)"
            else:
                previous_y = y + slot_step
                position = (
                    f"\\move({x},{previous_y},{x},{y},0,{rise_ms})"
                )
            events.append(
                _dialogue(
                    layer=10,
                    start_ms=segment_start_ms,
                    end_ms=segment_end_ms,
                    style=style,
                    text=(
                        f"{{\\an9{position}\\fs{author_size}}}"
                        f"{item.author}\\N"
                        f"{{\\r{body_style}}}{item.body}"
                    ),
                )
            )
    return events, len(prepared)


def _ass_header(
    *,
    width: int,
    height: int,
    font_name: str,
    reserve_danmaku: bool,
    subtitle_font_scale: int,
    subtitle_text_color: str,
    subtitle_background_color: str,
    output_layout: ClipOutputLayout,
    subtitle_font_family: LandscapeSubtitleFont,
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
        text_color = _ass_color(subtitle_text_color)
        background_color = _ass_color(
            subtitle_background_color, alpha=24
        )
        background_shadow = _ass_color(
            subtitle_background_color, alpha=56
        )
        landscape_subtitle_color = _ass_color(
            LANDSCAPE_SUBTITLE_COLOR
        )
        landscape_subtitle_en_color = _ass_color(
            LANDSCAPE_SUBTITLE_EN_COLOR,
            alpha=round(255 * (1 - LANDSCAPE_SUBTITLE_EN_OPACITY)),
        )
        landscape_danmaku_text = _ass_color(
            LANDSCAPE_DANMAKU_TEXT_COLOR
        )
        landscape_danmaku_author = _ass_color(
            LANDSCAPE_DANMAKU_AUTHOR_COLOR
        )
        landscape_danmaku_background = _ass_color(
            LANDSCAPE_DANMAKU_BACKGROUND_COLOR,
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
    zh_size = max(14, round(max(20, height * 0.034) * scale))
    en_size = max(12, round(max(16, height * 0.025) * scale))
    danmaku_size = max(15, round(height * 0.020))
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
    margin_v = max(12, round(height * 0.025))
    margin_l = max(12, round(width * 0.04))
    margin_r = (
        max(12, round(width * 0.44))
        if reserve_danmaku
        else margin_l
    )
    landscape_danmaku_border = _ass_color(
        LANDSCAPE_DANMAKU_AUTHOR_COLOR,
        alpha=168,
    )
    landscape_danmaku_box_shadow = _ass_color(
        LANDSCAPE_DANMAKU_TEXT_COLOR,
        alpha=224,
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
Style: SubtitleZh,{font_name},{zh_size},{text_color},{text_color},{background_color},{background_shadow},-1,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: SubtitleEn,{font_name},{en_size},{text_color},{text_color},{background_color},{background_shadow},0,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: Danmaku,{font_name},{danmaku_size},&H00FFFFFF,&H00FFFFFF,&H40000000,&HA8000000,0,0,0,0,100,100,0,0,3,1,1,9,0,0,0,1
Style: LandscapeSubtitleZh,{landscape_font_name},{landscape_zh_size},{landscape_subtitle_color},{landscape_subtitle_color},&H00000000,&H00000000,-1,0,0,0,{LANDSCAPE_SUBTITLE_ZH_SCALE_X},{LANDSCAPE_SUBTITLE_ZH_SCALE_Y},0,0,1,0,0,7,0,0,0,1
Style: LandscapeSubtitleEn,{landscape_font_name},{landscape_en_size},{landscape_subtitle_en_color},{landscape_subtitle_en_color},&H00000000,&H00000000,-1,0,0,0,{LANDSCAPE_SUBTITLE_EN_SCALE_X},{LANDSCAPE_SUBTITLE_EN_SCALE_Y},0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmaku,{font_name},{landscape_danmaku_size},{landscape_danmaku_text},{landscape_danmaku_text},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmakuAuthor,{font_name},{landscape_danmaku_author_size},{landscape_danmaku_author},{landscape_danmaku_author},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LandscapeDanmakuBox,{font_name},1,{landscape_danmaku_background},{landscape_danmaku_background},{landscape_danmaku_border},{landscape_danmaku_box_shadow},0,0,0,0,100,100,0,0,1,2,3,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def normalize_subtitle_color(value: str) -> str:
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("expected #RRGGBB")
    return value.upper()


def normalize_cover_title(value: str) -> str:
    return _plain_text(value)


def subtitle_contrast_ratio(
    text_color: str,
    background_color: str,
) -> float:
    text_luminance = _relative_luminance(
        normalize_subtitle_color(text_color)
    )
    background_luminance = _relative_luminance(
        normalize_subtitle_color(background_color)
    )
    lighter = max(text_luminance, background_luminance)
    darker = min(text_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _ass_color(value: str, *, alpha: int = 0) -> str:
    normalized = normalize_subtitle_color(value)
    if not 0 <= alpha <= 255:
        raise ValueError("alpha must be between 0 and 255")
    red = normalized[1:3]
    green = normalized[3:5]
    blue = normalized[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}"


def _relative_luminance(value: str) -> float:
    channels = [
        int(value[index : index + 2], 16) / 255
        for index in (1, 3, 5)
    ]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


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
    wrapped = textwrap.wrap(
        normalized,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
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
