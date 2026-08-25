import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.overlays import (
    build_clip_overlay,
    subtitle_contrast_ratio,
)
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
        subtitle_font_scale=125,
        subtitle_text_color="#E43D12",
        subtitle_background_color="#EBE9E1",
    )

    assert document.subtitle_event_count == 1
    assert document.danmaku_event_count <= 5
    assert r"\{测试\}" in document.content
    assert r"{\rSubtitleEn}English subtitle" in document.content
    assert r"\pos(" in document.content
    assert "Style: SubtitleZh,Noto Sans CJK SC" in document.content
    assert (
        "Style: SubtitleZh,Noto Sans CJK SC,54,"
        "&H00123DE4,&H00123DE4,&H18E1E9EB,&H38E1E9EB"
        in document.content
    )
    assert subtitle_contrast_ratio("#E43D12", "#EBE9E1") == pytest.approx(
        3.4724,
        rel=1e-4,
    )


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


def test_overlay_rejects_out_of_range_subtitle_scale():
    with pytest.raises(AppError, match="字号比例无效"):
        build_clip_overlay(
            width=1280,
            height=720,
            clip_start_ms=0,
            clip_end_ms=5000,
            subtitle_mode="zh",
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
            subtitle_font_scale=200,
        )


def test_landscape_overlay_places_content_in_side_panels():
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=1000,
        clip_end_ms=9000,
        subtitle_mode="zh",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1,
                start_ms=1000,
                end_ms=4000,
                text="左侧红色字幕",
            )
        ],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=1,
                timestamp_ms=1500,
                author="观众",
                text="右侧玫瑰粉弹幕",
            )
        ],
        output_layout="landscape",
    )

    assert "PlayResX: 1920" in document.content
    assert "PlayResY: 1080" in document.content
    assert (
        "Style: LandscapeSubtitleZh,LXGW WenKai,35,"
        "&H00123DE4"
        in document.content
    )
    assert (
        "Style: LandscapeDanmaku,Noto Sans CJK SC,22,"
        "&H00423A5B"
        in document.content
    )
    assert "&H006D53D6" in document.content
    assert r"\pos(1862,70)" in document.content
    assert ",LandscapeSubtitleZh," in document.content


@pytest.mark.parametrize(
    ("font_family", "font_name"),
    [
        ("wenkai", "LXGW WenKai"),
        ("serif", "Noto Serif CJK SC"),
        ("sans", "Noto Sans CJK SC"),
    ],
)
def test_landscape_overlay_maps_selectable_fonts(font_family, font_name):
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=3000,
        subtitle_mode="zh",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=3000,
                text="字体测试",
            )
        ],
        translations={},
        danmaku=[],
        output_layout="landscape",
        subtitle_font_family=font_family,
    )

    assert f"Style: LandscapeSubtitleZh,{font_name},35," in document.content
