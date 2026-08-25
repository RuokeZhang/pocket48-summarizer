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
    LANDSCAPE_DANMAKU_AUTHOR_COLOR,
    LANDSCAPE_DANMAKU_BACKGROUND_COLOR,
    LANDSCAPE_DANMAKU_TEXT_COLOR,
    LANDSCAPE_SUBTITLE_COLOR,
    LANDSCAPE_VIDEO_WIDTH,
    DEFAULT_LANDSCAPE_SUBTITLE_FONT,
    ClipOutputLayout,
    LandscapeSubtitleFont,
    landscape_subtitle_font_name,
)

SubtitleMode = Literal["off", "zh", "en", "bilingual"]
DANMAKU_LIFETIME_MS = 5000
DANMAKU_MIN_GAP_MS = 450
DANMAKU_SLOT_COUNT = 5
SUBTITLE_FONT_SCALE_MIN = 70
SUBTITLE_FONT_SCALE_MAX = 160
DEFAULT_SUBTITLE_FONT_SCALE = 100
DEFAULT_SUBTITLE_TEXT_COLOR = "#E43D12"
DEFAULT_SUBTITLE_BACKGROUND_COLOR = "#EBE9E1"
MIN_SUBTITLE_CONTRAST_RATIO = 3.0
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True, slots=True)
class ClipOverlayDocument:
    content: str
    subtitle_event_count: int
    danmaku_event_count: int
    warning_message: str | None = None


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
    )
    danmaku_events = (
        _danmaku_events(
            width=width,
            height=height,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            danmaku=danmaku,
            output_layout=output_layout,
        )
        if include_danmaku
        else []
    )
    warning = (
        "所选范围没有可渲染的弹幕"
        if include_danmaku and not danmaku_events
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
        danmaku_event_count=len(danmaku_events),
        warning_message=warning,
    )


def _subtitle_events(
    *,
    clip_start_ms: int,
    clip_end_ms: int,
    subtitle_mode: SubtitleMode,
    transcript: list[TranscriptSegment],
    translations: dict[int, str],
    output_layout: ClipOutputLayout,
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
    for segment in selected:
        start_ms = max(0, segment.start_ms - clip_start_ms)
        end_ms = min(
            clip_end_ms - clip_start_ms,
            segment.end_ms - clip_start_ms,
        )
        if end_ms <= start_ms:
            continue
        zh = _ass_text(segment.text)
        en = _ass_text(translations.get(segment.sequence, ""))
        style_prefix = (
            "LandscapeSubtitle"
            if output_layout == "landscape"
            else "Subtitle"
        )
        if subtitle_mode == "zh":
            style = f"{style_prefix}Zh"
            text = zh
        elif subtitle_mode == "en":
            style = f"{style_prefix}En"
            text = en
        else:
            style = f"{style_prefix}Zh"
            text = f"{zh}\\N{{\\r{style_prefix}En}}{en}"
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
        raise AppError(
            "clip_subtitles_empty",
            "所选范围没有可渲染的字幕",
            False,
        )
    return events


def _danmaku_events(
    *,
    width: int,
    height: int,
    clip_start_ms: int,
    clip_end_ms: int,
    danmaku: list[DanmakuEntry],
    output_layout: ClipOutputLayout,
) -> list[str]:
    selected = sorted(
        (
            entry
            for entry in danmaku
            if clip_start_ms <= entry.timestamp_ms < clip_end_ms
        ),
        key=lambda entry: (entry.timestamp_ms, entry.sequence),
    )
    slot_available_ms = [0] * DANMAKU_SLOT_COUNT
    events: list[str] = []
    last_accepted_ms = -DANMAKU_MIN_GAP_MS
    landscape = output_layout == "landscape"
    right_margin = 58 if landscape else max(14, round(width * 0.025))
    top_margin = 70 if landscape else max(14, round(height * 0.035))
    slot_step = 180 if landscape else max(48, round(height * 0.115))
    x = width - right_margin
    author_size = 18 if landscape else max(13, round(height * 0.016))
    for entry in selected:
        relative_ms = entry.timestamp_ms - clip_start_ms
        if relative_ms - last_accepted_ms < DANMAKU_MIN_GAP_MS:
            continue
        slot = next(
            (
                index
                for index, available_ms in enumerate(slot_available_ms)
                if available_ms <= relative_ms
            ),
            None,
        )
        if slot is None:
            continue
        event_end_ms = min(
            clip_end_ms - clip_start_ms,
            relative_ms + DANMAKU_LIFETIME_MS,
        )
        if event_end_ms <= relative_ms:
            continue
        author = _ass_text(_truncate(_plain_text(entry.author), 18))
        body = _wrapped_ass_text(
            _plain_text(entry.text),
            width=17 if landscape else 18,
            lines=3,
        )
        if not body:
            continue
        y = top_margin + slot * slot_step
        style = "LandscapeDanmakuAuthor" if landscape else "Danmaku"
        body_style = "LandscapeDanmaku" if landscape else "Danmaku"
        text = (
            f"{{\\an9\\pos({x},{y})\\fs{author_size}}}"
            f"{author or '匿名'}\\N{{\\r{body_style}}}{body}"
        )
        events.append(
            _dialogue(
                layer=10,
                start_ms=relative_ms,
                end_ms=event_end_ms,
                style=style,
                text=text,
            )
        )
        slot_available_ms[slot] = event_end_ms
        last_accepted_ms = relative_ms
    return events


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
        landscape_danmaku_shadow = _ass_color(
            LANDSCAPE_DANMAKU_BACKGROUND_COLOR,
            alpha=44,
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
    scale = subtitle_font_scale / 100
    zh_size = max(14, round(max(20, height * 0.034) * scale))
    en_size = max(12, round(max(16, height * 0.025) * scale))
    danmaku_size = max(15, round(height * 0.020))
    landscape_zh_size = max(26, round(height * 0.032 * scale))
    landscape_en_size = max(20, round(height * 0.024 * scale))
    landscape_danmaku_size = max(18, round(height * 0.020))
    margin_v = max(12, round(height * 0.025))
    margin_l = max(12, round(width * 0.04))
    margin_r = (
        max(12, round(width * 0.44))
        if reserve_danmaku
        else margin_l
    )
    landscape_side_width = (
        LANDSCAPE_CANVAS_WIDTH - LANDSCAPE_VIDEO_WIDTH
    ) // 2
    landscape_margin_l = 72
    landscape_margin_r = width - (
        landscape_side_width - landscape_margin_l
    )
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SubtitleZh,{font_name},{zh_size},{text_color},{text_color},{background_color},{background_shadow},-1,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: SubtitleEn,{font_name},{en_size},{text_color},{text_color},{background_color},{background_shadow},0,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: Danmaku,{font_name},{danmaku_size},&H00FFFFFF,&H00FFFFFF,&H40000000,&HA8000000,0,0,0,0,100,100,0,0,3,1,1,9,0,0,0,1
Style: LandscapeSubtitleZh,{landscape_font_name},{landscape_zh_size},{landscape_subtitle_color},{landscape_subtitle_color},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,4,{landscape_margin_l},{landscape_margin_r},0,1
Style: LandscapeSubtitleEn,{landscape_font_name},{landscape_en_size},{landscape_subtitle_color},{landscape_subtitle_color},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,{landscape_margin_l},{landscape_margin_r},0,1
Style: LandscapeDanmaku,{font_name},{landscape_danmaku_size},{landscape_danmaku_text},{landscape_danmaku_text},{landscape_danmaku_background},{landscape_danmaku_shadow},0,0,0,0,100,100,0,0,3,2,0,9,0,0,0,1
Style: LandscapeDanmakuAuthor,{font_name},{max(18, landscape_danmaku_size - 4)},{landscape_danmaku_author},{landscape_danmaku_author},{landscape_danmaku_background},{landscape_danmaku_shadow},-1,0,0,0,100,100,0,0,3,2,0,9,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def normalize_subtitle_color(value: str) -> str:
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("expected #RRGGBB")
    return value.upper()


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
    wrapped = textwrap.wrap(
        value,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not wrapped:
        return ""
    if len(wrapped) > lines:
        wrapped = wrapped[:lines]
        wrapped[-1] = _truncate(wrapped[-1], max(1, width - 1)) + "…"
    return r"\N".join(_ass_text(line) for line in wrapped)


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"
