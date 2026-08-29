import re

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.layouts import (
    LANDSCAPE_CANVAS_HEIGHT,
    LANDSCAPE_CANVAS_WIDTH,
    LANDSCAPE_DANMAKU_BOTTOM,
    LANDSCAPE_DANMAKU_RIGHT,
    LANDSCAPE_DANMAKU_TOP,
    LANDSCAPE_DANMAKU_WIDTH,
    LANDSCAPE_SUBTITLE_EN_COLOR,
    LANDSCAPE_SUBTITLE_LEFT,
    LANDSCAPE_SUBTITLE_WIDTH,
)
from pocket48_summarizer.media.overlays import (
    COVER_DURATION_MS,
    build_cover_overlay,
    build_clip_overlay,
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
    )

    assert document.subtitle_event_count == 1
    assert document.danmaku_event_count == 8
    assert r"\{测试\}" in document.content
    assert r"{\rSubtitleEn}English subtitle" in document.content
    assert r"\pos(" in document.content
    assert r"\move(" in document.content
    assert r"\fad(120,0)" in document.content
    assert "Style: SubtitleZh,Noto Sans CJK SC" in document.content
    # Portrait captions are unstyled: white glyphs, black outline, no box.
    assert (
        "Style: SubtitleZh,Noto Sans CJK SC,87,"
        "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,5,0,2,"
        in document.content
    )
    assert (
        "Style: SubtitleEn,Noto Sans CJK SC,"
        in document.content
    )
    assert "&H18E1E9EB" not in document.content


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


def test_danmaku_stack_rises_and_evicts_only_at_visible_limit():
    document = build_clip_overlay(
        width=1280,
        height=720,
        clip_start_ms=0,
        clip_end_ms=10_000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=index,
                timestamp_ms=1000 + index * 500,
                author=f"用户{index}",
                text=f"第 {index} 条弹幕",
            )
            for index in range(6)
        ],
    )

    assert document.danmaku_event_count == 6
    first = [
        line
        for line in document.content.splitlines()
        if "用户0" in line
    ]
    newest = [
        line
        for line in document.content.splitlines()
        if "用户5" in line
    ]
    assert len(first) == 5
    assert first[0].startswith(
        "Dialogue: 10,0:00:01.00,0:00:01.50,"
    )
    assert first[-1].startswith(
        "Dialogue: 10,0:00:03.00,0:00:03.50,"
    )
    assert newest[0].startswith(
        "Dialogue: 10,0:00:03.50,0:00:10.00,"
    )
    initial_position = re.search(r"\\pos\(\d+,(\d+)\)", first[0])
    assert initial_position is not None
    initial_y = int(initial_position.group(1))
    rise = re.search(
        r"\\move\(\d+,(\d+),\d+,(\d+),0,220\)",
        first[1],
    )
    assert rise is not None
    assert int(rise.group(1)) == initial_y
    assert int(rise.group(2)) < initial_y


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
    assert "WrapStyle: 0" in document.content
    assert (
        "Style: LandscapeSubtitleZh,LXGW WenKai,52,"
        "&H00123DE4"
        in document.content
    )
    assert (
        "Style: LandscapeDanmaku,Noto Sans CJK SC,31,"
        "&H00423A5B"
        in document.content
    )
    assert "Style: LandscapeDanmakuAuthor,Noto Sans CJK SC,27," in (
        document.content
    )
    assert (
        "Style: LandscapeDanmakuBox,Noto Sans CJK SC,1,"
        "&H0CF6F8FF"
        in document.content
    )
    assert "&H006D53D6" in document.content
    assert r"\pos(1337,926)" in document.content
    assert r"\pos(1351,938)" in document.content
    assert r"\p1}m 20 0 l 498 0" in document.content
    assert ",LandscapeSubtitleZh," in document.content


def test_landscape_ass_geometry_matches_browser_preview_percentages():
    assert LANDSCAPE_SUBTITLE_LEFT / LANDSCAPE_CANVAS_WIDTH == pytest.approx(
        0.0375
    )
    assert LANDSCAPE_SUBTITLE_WIDTH / LANDSCAPE_CANVAS_WIDTH == pytest.approx(
        0.265,
        abs=0.0002,
    )
    assert LANDSCAPE_DANMAKU_RIGHT / LANDSCAPE_CANVAS_WIDTH == pytest.approx(
        0.034,
        abs=0.0002,
    )
    assert LANDSCAPE_DANMAKU_WIDTH / LANDSCAPE_CANVAS_WIDTH == pytest.approx(
        0.27,
        abs=0.0003,
    )
    assert LANDSCAPE_DANMAKU_BOTTOM / LANDSCAPE_CANVAS_HEIGHT == pytest.approx(
        0.07,
        abs=0.0005,
    )


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

    assert f"Style: LandscapeSubtitleZh,{font_name},52," in document.content


def test_landscape_overlay_wraps_long_subtitle_inside_left_panel():
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
                text="我最近已经在开始反思自己了，我要走这种不那种。",
            )
        ],
        translations={},
        danmaku=[],
        output_layout="landscape",
    )

    assert (
        "Style: LandscapeSubtitleZh,LXGW WenKai,52,"
        "&H00123DE4,&H00123DE4,&H00000000,&H00000000,"
        "-1,0,0,0,94,94,0,0,1,0,0,7,0,0,0,1"
        in document.content
    )
    assert r"\pos(72,491)}我最近已经在开始反思自己" in document.content
    assert r"\pos(72,548)}了，我要走这种不那种。" in document.content


def test_landscape_danmaku_card_moves_by_new_bubble_height():
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=5000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=1,
                timestamp_ms=500,
                author="第一条",
                text="较早弹幕",
            ),
            DanmakuEntry(
                sequence=2,
                timestamp_ms=1000,
                author="第二条",
                text="新弹幕",
            ),
        ],
        output_layout="landscape",
    )

    assert r"\move(1337,926,1337,835,0,220)" in document.content
    assert r"\move(1351,938,1351,847,0,220)" in document.content


def test_landscape_english_wrap_uses_browser_equivalent_width():
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=3000,
        subtitle_mode="bilingual",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=3000,
                text="我已经开始反思自己了。",
            )
        ],
        translations={1: "I have started reflecting on myself."},
        danmaku=[],
        output_layout="landscape",
    )

    assert LANDSCAPE_SUBTITLE_EN_COLOR == "#D6536D"
    assert (
        "Style: LandscapeSubtitleEn,LXGW WenKai,40,"
        "&H1F6D53D6,&H1F6D53D6,&H00000000,&H00000000,"
        "-1,0,0,0,95,90,0,0,1,0,0,7,0,0,0,1"
        in document.content
    )
    assert (
        r"\pos(72,558)}I have started reflecting on myself."
        in document.content
    )


def test_landscape_bilingual_lines_match_browser_spacing_and_wrap():
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=4000,
        subtitle_mode="bilingual",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=4000,
                text="人家讨厌我，可是讨厌是他的事情，我无法主观控制啊。",
            )
        ],
        translations={
            1: (
                "They hate me, but hating is their thing, "
                "I can't subjectively control it."
            )
        },
        danmaku=[],
        subtitle_font_scale=110,
        output_layout="landscape",
    )

    assert r"\pos(72,376)}人家讨厌我，可是讨厌是他" in document.content
    assert r"\pos(72,439)}的事情，我无法主观控制" in document.content
    assert r"\pos(72,502)}啊。" in document.content
    assert (
        r"\pos(72,572)}They hate me, but hating is their"
        in document.content
    )
    assert (
        r"\pos(72,622)}thing, I can't subjectively control"
        in document.content
    )
    assert r"\pos(72,671)}it." in document.content


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("scrim", r"\pos(692,130)\p1"),
        ("display", r"\pos(960,454)"),
        ("badge", r"\pos(692,670)\p1"),
    ],
)
def test_cover_overlay_keeps_title_inside_video_safe_area(style, expected):
    document = build_cover_overlay(
        width=1920,
        height=1080,
        title="这是一个自定义封面标题，最多显示两行",
        style=style,
        output_layout="landscape",
    )

    assert document.style == style
    assert document.title == "这是一个自定义封面标题，最多显示两行"
    assert expected in document.content
    assert "Style: CoverTitle,Noto Sans CJK SC" in document.content
    assert document.content.count(r"\N") <= 1
    assert "0:00:01.50" in document.content


def test_cover_overlay_rejects_empty_title():
    with pytest.raises(AppError, match="封面标题"):
        build_cover_overlay(
            width=1080,
            height=1920,
            title="   ",
        )


def test_landscape_danmaku_stack_fills_the_column_beyond_five_cards():
    danmaku = [
        DanmakuEntry(
            sequence=index,
            timestamp_ms=1000 + index * 500,
            author=f"观众{index}",
            text=f"第 {index} 条",
        )
        for index in range(12)
    ]
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=20_000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=danmaku,
        output_layout="landscape",
    )

    boxes = [
        line
        for line in document.content.splitlines()
        if line.startswith("Dialogue: 9,")
    ]
    # Count the cards alive at the moment the newest entry appears.
    newest_start = "0:00:06.50"
    concurrent = [line for line in boxes if newest_start in line]
    assert len(concurrent) > 5

    tops = [
        int(match.group(1))
        for line in boxes
        for match in [re.search(r"\\pos\(\d+,(-?\d+)\)", line)]
        if match is not None
    ] + [
        int(match.group(2))
        for line in boxes
        for match in [
            re.search(r"\\move\(\d+,-?\d+,\d+,(-?)(\d+),0,\d+\)", line)
        ]
        if match is not None
    ]
    assert tops
    assert min(tops) >= LANDSCAPE_DANMAKU_TOP


def test_portrait_danmaku_stack_stays_capped_at_five():
    document = build_clip_overlay(
        width=1280,
        height=720,
        clip_start_ms=0,
        clip_end_ms=20_000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=index,
                timestamp_ms=1000 + index * 500,
                author=f"用户{index}",
                text=f"第 {index} 条",
            )
            for index in range(12)
        ],
    )

    oldest = [
        line
        for line in document.content.splitlines()
        if "用户0" in line
    ]
    assert len(oldest) == 5
