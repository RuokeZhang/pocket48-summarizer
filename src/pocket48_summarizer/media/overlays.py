from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Literal

from ..errors import AppError
from ..models import DanmakuEntry, TranscriptSegment
from ..security import strip_control_chars

SubtitleMode = Literal["off", "zh", "en", "bilingual"]
DANMAKU_LIFETIME_MS = 5000
DANMAKU_MIN_GAP_MS = 450
DANMAKU_SLOT_COUNT = 5


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
) -> ClipOverlayDocument:
    if width <= 0 or height <= 0 or clip_end_ms <= clip_start_ms:
        raise AppError(
            "clip_overlay_invalid",
            "字幕或弹幕渲染参数无效",
            False,
        )
    subtitle_events = _subtitle_events(
        clip_start_ms=clip_start_ms,
        clip_end_ms=clip_end_ms,
        subtitle_mode=subtitle_mode,
        transcript=transcript,
        translations=translations,
    )
    danmaku_events = (
        _danmaku_events(
            width=width,
            height=height,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            danmaku=danmaku,
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
    right_margin = max(14, round(width * 0.025))
    top_margin = max(14, round(height * 0.035))
    slot_step = max(48, round(height * 0.115))
    x = width - right_margin
    author_size = max(13, round(height * 0.016))
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
        body = _wrapped_ass_text(_plain_text(entry.text), width=18, lines=3)
        if not body:
            continue
        y = top_margin + slot * slot_step
        text = (
            f"{{\\an9\\pos({x},{y})\\fs{author_size}}}"
            f"{author or '匿名'}\\N{{\\rDanmaku}}{body}"
        )
        events.append(
            _dialogue(
                layer=10,
                start_ms=relative_ms,
                end_ms=event_end_ms,
                style="Danmaku",
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
) -> str:
    zh_size = max(20, round(height * 0.034))
    en_size = max(16, round(height * 0.025))
    danmaku_size = max(15, round(height * 0.020))
    margin_v = max(12, round(height * 0.025))
    margin_l = max(12, round(width * 0.04))
    margin_r = (
        max(12, round(width * 0.44))
        if reserve_danmaku
        else margin_l
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
Style: SubtitleZh,{font_name},{zh_size},&H00FFFFFF,&H00FFFFFF,&H50000000,&H76000000,-1,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: SubtitleEn,{font_name},{en_size},&H00E8E8EE,&H00E8E8EE,&H50000000,&H76000000,0,0,0,0,100,100,0,0,3,1,1,2,{margin_l},{margin_r},{margin_v},1
Style: Danmaku,{font_name},{danmaku_size},&H00FFFFFF,&H00FFFFFF,&H40000000,&HA8000000,0,0,0,0,100,100,0,0,3,1,1,9,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


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
