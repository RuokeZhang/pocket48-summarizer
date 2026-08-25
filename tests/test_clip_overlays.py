import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.overlays import build_clip_overlay
from pocket48_summarizer.models import DanmakuEntry, TranscriptSegment


def test_overlay_renders_bilingual_subtitles_and_bounded_danmaku():
    transcript = [
        TranscriptSegment(
            sequence=1,
            start_ms=1000,
            end_ms=3000,
            text=r"中文 {测试}\\",
        )
    ]
    danmaku = [
        DanmakuEntry(
            sequence=index,
            timestamp_ms=1000 + index * 500,
            author=f"用户{index}",
            text="这是一条很长的弹幕内容，用来测试右侧气泡换行。",
        )
        for index in range(8)
    ]

    document = build_clip_overlay(
        width=720,
        height=1280,
        clip_start_ms=1000,
        clip_end_ms=9000,
        subtitle_mode="bilingual",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=transcript,
        translations={1: "English subtitle"},
        danmaku=danmaku,
    )

    assert document.subtitle_event_count == 1
    assert document.danmaku_event_count <= 5
    assert r"\{测试\}" in document.content
    assert r"{\rSubtitleEn}English subtitle" in document.content
    assert r"\pos(" in document.content
    assert "Style: SubtitleZh,Noto Sans CJK SC" in document.content


def test_overlay_requires_complete_english_translation():
    with pytest.raises(AppError, match="英文字幕尚未完整生成"):
        build_clip_overlay(
            width=1280,
            height=720,
            clip_start_ms=0,
            clip_end_ms=5000,
            subtitle_mode="en",
            include_danmaku=False,
            font_name="Noto Sans CJK SC",
            transcript=[
                TranscriptSegment(
                    sequence=1,
                    start_ms=0,
                    end_ms=3000,
                    text="中文",
                )
            ],
            translations={},
            danmaku=[],
        )


def test_overlay_warns_when_danmaku_range_is_empty():
    document = build_clip_overlay(
        width=1280,
        height=720,
        clip_start_ms=0,
        clip_end_ms=5000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[],
    )

    assert document.subtitle_event_count == 0
    assert document.danmaku_event_count == 0
    assert document.warning_message == "所选范围没有可渲染的弹幕"
