import re

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.fonts import split_emoji_runs
from pocket48_summarizer.media.layouts import (
    LANDSCAPE_CANVAS_HEIGHT,
    LANDSCAPE_CANVAS_WIDTH,
    LANDSCAPE_DANMAKU_BOTTOM,
    LANDSCAPE_DANMAKU_RIGHT,
    LANDSCAPE_DANMAKU_TOP,
    LANDSCAPE_DANMAKU_WIDTH,
    LANDSCAPE_SUBTITLE_LEFT,
    LANDSCAPE_SUBTITLE_WIDTH,
    LANDSCAPE_THEMES,
    LANDSCAPE_WATERMARK_LEFT_TOP,
    LANDSCAPE_WATERMARK_SIZE,
    LANDSCAPE_WATERMARK_TOP,
)
from pocket48_summarizer.media.overlays import (
    COVER_DURATION_MS,
    DEFAULT_SUBTITLE_FONT_SCALE,
    LIBASS_CJK_ADVANCE_RATIO,
    _portrait_subtitle_metrics,
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


def test_overlay_allows_an_empty_subtitle_range_for_service_validation():
    document = build_clip_overlay(
        width=1280,
        height=720,
        clip_start_ms=0,
        clip_end_ms=5000,
        subtitle_mode="zh",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[],
        allow_empty_subtitles=True,
    )

    assert document.subtitle_event_count == 0
    assert document.raster_cues == ()


def test_portrait_danmaku_stack_rises_as_newer_cards_arrive():
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
    # Every card stays on screen because six of them fit the column; the
    # stack is bounded by height now, not by a hard-coded count.
    assert len(first) == 6
    assert first[0].startswith(
        "Dialogue: 10,0:00:01.00,0:00:01.50,"
    )
    assert first[-1].startswith(
        "Dialogue: 10,0:00:03.50,0:00:10.00,"
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


@pytest.mark.parametrize("theme_key", LANDSCAPE_THEMES)
def test_landscape_overlay_uses_every_registered_theme(theme_key):
    theme = LANDSCAPE_THEMES[theme_key]
    document = build_clip_overlay(
        width=1920,
        height=1080,
        clip_start_ms=0,
        clip_end_ms=3000,
        subtitle_mode="zh",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=3000,
                text="配色测试",
            )
        ],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=1,
                timestamp_ms=1000,
                author="测试成员",
                text="测试弹幕",
            )
        ],
        output_layout="landscape",
        landscape_theme=theme_key,
    )

    def ass_color(value: str) -> str:
        red, green, blue = (
            value[1:3],
            value[3:5],
            value[5:7],
        )
        return f"&H00{blue}{green}{red}"

    assert ass_color(theme.subtitle_zh) in document.content
    assert ass_color(theme.danmaku_author) in document.content
    assert ass_color(theme.danmaku_text) in document.content


def test_landscape_overlay_rejects_an_unknown_theme():
    with pytest.raises(AppError) as raised:
        build_clip_overlay(
            width=1920,
            height=1080,
            clip_start_ms=0,
            clip_end_ms=3000,
            subtitle_mode="off",
            include_danmaku=False,
            font_name="Noto Sans CJK SC",
            transcript=[],
            translations={},
            danmaku=[],
            output_layout="landscape",
            landscape_theme="unknown",
        )

    assert raised.value.code == "clip_theme_invalid"


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

    assert LANDSCAPE_THEMES["cream"].subtitle_en == "#D6536D"
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


def test_cover_title_emoji_is_rendered_as_one_rgba_asset():
    document = build_cover_overlay(
        width=1920,
        height=1080,
        title="开心🎉时刻",
        style="scrim",
        output_layout="landscape",
    )

    assert "开心🎉时刻" not in document.content
    assert r"\p1" in document.content
    assert len(document.raster_cues) == 1
    assert [line.text for line in document.raster_cues[0].asset.lines] == [
        "开心🎉时刻"
    ]
    title_asset = document.raster_cues[0].asset
    assert title_asset.height > title_asset.lines[0].font_size


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


def test_portrait_danmaku_stack_is_bounded_by_the_column_height():
    """Portrait used to evict at five cards while the preview filled the column.

    The export now measures the same bottom-anchored stack the browser draws,
    so it keeps cards until they would overflow the frame.
    """

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
    assert len(oldest) == 12
    positions = [
        int(match.group(1))
        for line in oldest
        if (match := re.search(r"\\(?:pos|move)\(\d+,\d+,\d+,(\d+)", line))
        or (match := re.search(r"\\pos\(\d+,(\d+)\)", line))
    ]
    assert positions == sorted(positions, reverse=True)
    assert min(positions) >= 0


def _dialogue_text(line: str) -> str:
    # Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    return line.split(",", 9)[9]


def _portrait_metrics():
    return _portrait_subtitle_metrics(
        width=1080,
        height=1920,
        subtitle_font_scale=DEFAULT_SUBTITLE_FONT_SCALE,
        reserve_danmaku=True,
    )


def _portrait_document(text: str, *, width: int = 1080, height: int = 1920):
    return build_clip_overlay(
        width=width,
        height=height,
        clip_start_ms=0,
        clip_end_ms=6000,
        subtitle_mode="zh",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1, start_ms=0, end_ms=6000, text=text
            )
        ],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=1,
                timestamp_ms=500,
                author="粉丝",
                text="好耶",
            )
        ],
        output_layout="portrait",
    )


def test_portrait_subtitles_are_wrapped_before_libass_sees_them():
    """libass only breaks lines at spaces, so it never wraps Chinese.

    A long Chinese caption used to reach libass as one Dialogue, render as a
    single over-wide line, and spill off both edges once ``\\an2`` centred it.
    """

    document = _portrait_document("今天我们来聊一聊最近发生的一些很有意思的事情")

    lines = [
        line
        for line in document.content.splitlines()
        if line.startswith("Dialogue:") and "SubtitleZh" in line
    ]
    assert len(lines) == 1
    text = _dialogue_text(lines[0])
    assert "\\N" in text, "the caption must carry explicit breaks"
    metrics = _portrait_metrics()
    for segment in text.split("\\N"):
        assert len(segment) <= metrics.zh_line_width


def test_portrait_subtitles_stay_inside_the_frame():
    document = _portrait_document("今天我们来聊一聊最近发生的一些很有意思的事情")

    metrics = _portrait_metrics()
    widest = max(
        len(segment)
        for line in document.content.splitlines()
        if line.startswith("Dialogue:") and "SubtitleZh" in line
        for segment in _dialogue_text(line).split("\\N")
    )
    drawn = widest * metrics.zh_size * LIBASS_CJK_ADVANCE_RATIO
    assert metrics.margin_l + drawn <= 1080


def test_portrait_danmaku_uses_the_same_card_renderer_as_landscape():
    """Portrait was left on the old fixed-slot grey rows when landscape moved
    to rounded cards, so the export stopped matching its own preview."""

    document = _portrait_document("你好")

    assert "Style: PortraitDanmakuBox" in document.content
    boxes = [
        line
        for line in document.content.splitlines()
        if line.startswith("Dialogue:") and "PortraitDanmakuBox" in line
    ]
    assert boxes, "portrait danmaku must draw a card background"
    assert "\\p1" in boxes[0], "the card is a vector rounded rectangle"
    position = re.search(r"\\pos\(\d+,(\d+)\)", boxes[0])
    assert position is not None
    # Bottom-anchored like the preview column, not floating in the upper half.
    assert int(position.group(1)) > 1920 * 0.5


def _dialogue_lines(content: str, style: str) -> list[str]:
    return [
        line
        for line in content.splitlines()
        if line.startswith("Dialogue:")
        and line.split(",", 9)[3].strip() == style
    ]


def test_emoji_subtitle_becomes_one_mixed_rgba_asset():
    document = build_clip_overlay(
        width=1080,
        height=1920,
        clip_start_ms=0,
        clip_end_ms=6000,
        subtitle_mode="zh",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1, start_ms=0, end_ms=6000, text="开心🎉真的"
            )
        ],
        translations={},
        danmaku=[],
        output_layout="portrait",
    )

    assert _dialogue_lines(document.content, "SubtitleZh") == []
    assert "🎉" not in document.content
    assert len(document.raster_cues) == 1
    assert document.raster_cues[0].layer == 20
    assert [line.text for line in document.raster_cues[0].asset.lines] == [
        "开心🎉真的"
    ]


def test_bilingual_emoji_asset_keeps_both_surrounding_text_styles():
    document = build_clip_overlay(
        width=1080,
        height=1920,
        clip_start_ms=0,
        clip_end_ms=6000,
        subtitle_mode="bilingual",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(sequence=1, start_ms=0, end_ms=6000, text="开心")
        ],
        translations={1: "so happy 🎉 today"},
        danmaku=[],
        output_layout="portrait",
    )

    assert len(document.raster_cues) == 1
    lines = document.raster_cues[0].asset.lines
    assert [line.text for line in lines] == ["开心", "so happy 🎉 today"]
    assert lines[0].font_size > lines[1].font_size
    assert "🎉" not in document.content


def test_only_emoji_presentation_characters_form_emoji_runs():
    assert list(split_emoji_runs("星★亮⭐心♥")) == [
        (False, "星★亮"),
        (True, "⭐"),
        (False, "心♥"),
    ]


def test_danmaku_emoji_asset_keeps_clusters_and_card_motion():
    document = build_clip_overlay(
        width=1080,
        height=1920,
        clip_start_ms=0,
        clip_end_ms=6000,
        subtitle_mode="off",
        include_danmaku=True,
        font_name="Noto Sans CJK SC",
        transcript=[],
        translations={},
        danmaku=[
            DanmakuEntry(
                sequence=1,
                timestamp_ms=500,
                author="粉丝",
                text="家人👨\u200d👩\u200d👧和❤\ufe0f",
            ),
            DanmakuEntry(
                sequence=2,
                timestamp_ms=1000,
                author="另一位",
                text="新弹幕",
            ),
        ],
        output_layout="portrait",
    )

    cue = document.raster_cues[0]
    assert [line.text for line in cue.asset.lines] == [
        "粉丝",
        "家人👨\u200d👩\u200d👧和❤\ufe0f",
    ]
    assert len(cue.placements) == 2
    assert cue.placements[1].move_ms == 220
    assert cue.placements[1].y_to < cue.placements[1].y_from
    assert "👨\u200d👩\u200d👧" not in document.content
    assert len(document.raster_cues) == 2
    assert "PortraitDanmakuBox" not in "\n".join(
        line
        for line in document.content.splitlines()
        if line.startswith("Dialogue:")
    )


def test_grapheme_clusters_are_not_split_across_rgba_runs():
    assert list(
        split_emoji_runs("家人👨\u200d👩\u200d👧和❤\ufe0f")
    ) == [
        (False, "家人"),
        (True, "👨\u200d👩\u200d👧"),
        (False, "和"),
        (True, "❤\ufe0f"),
    ]


def test_adjacent_emoji_are_separate_graphemes():
    assert list(split_emoji_runs("前🎉😂后")) == [
        (False, "前"),
        (True, "🎉"),
        (True, "😂"),
        (False, "后"),
    ]


def test_wrapping_never_splits_a_zwj_emoji_cluster():
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
                text="一二三四五六七八九十👨\u200d👩\u200d👧",
            )
        ],
        translations={},
        danmaku=[],
        output_layout="landscape",
    )

    cue_text = "".join(
        line.text
        for cue in document.raster_cues
        for line in cue.asset.lines
    )
    assert "👨\u200d👩\u200d👧" in cue_text


def test_documents_without_emoji_are_left_byte_identical():
    kwargs = dict(
        width=1080,
        height=1920,
        clip_start_ms=0,
        clip_end_ms=6000,
        subtitle_mode="zh",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(sequence=1, start_ms=0, end_ms=6000, text="开心")
        ],
        translations={},
        danmaku=[],
        output_layout="portrait",
    )

    document = build_clip_overlay(**kwargs)
    assert "\\fn" not in document.content
    assert document.raster_cues == ()


def _watermark_overlay(
    *, output_layout: str, live_started_at: str | None
) -> str:
    return build_clip_overlay(
        width=1920 if output_layout == "landscape" else 720,
        height=1080 if output_layout == "landscape" else 1280,
        clip_start_ms=0,
        clip_end_ms=4000,
        subtitle_mode="zh",
        include_danmaku=False,
        font_name="Noto Sans CJK SC",
        transcript=[
            TranscriptSegment(
                sequence=1, start_ms=0, end_ms=3000, text="今天真开心"
            )
        ],
        translations={},
        danmaku=[],
        output_layout=output_layout,
        live_started_at=live_started_at,
    ).content


def test_landscape_watermark_credits_the_tool_and_dates_the_replay():
    content = _watermark_overlay(
        output_layout="landscape", live_started_at="2026-08-29T12:15:00Z"
    )

    lines = _dialogue_lines(content, "LandscapeWatermark")
    assert len(lines) == 2
    assert "AI剪切片工具 p48.ruokezhang.com" in lines[0]
    assert "2026-08-29 20:15" in lines[1]
    assert (
        rf"\an7\pos({LANDSCAPE_SUBTITLE_LEFT},{LANDSCAPE_WATERMARK_LEFT_TOP})"
        in lines[0]
    )
    assert rf"\an9\pos(1855,{LANDSCAPE_WATERMARK_TOP})" in lines[1]
    # The danmaku column is bounded at LANDSCAPE_DANMAKU_TOP and fills upward,
    # so the right mark is only safe while it clears that ceiling. The left
    # column has no such ceiling, which is why it may hang lower.
    assert (
        LANDSCAPE_WATERMARK_TOP + LANDSCAPE_WATERMARK_SIZE
        < LANDSCAPE_DANMAKU_TOP
    )
    assert LANDSCAPE_WATERMARK_LEFT_TOP > LANDSCAPE_WATERMARK_TOP


def test_landscape_watermark_omits_a_time_the_replay_never_recorded():
    content = _watermark_overlay(
        output_layout="landscape", live_started_at=None
    )

    lines = _dialogue_lines(content, "LandscapeWatermark")
    assert len(lines) == 1
    assert "AI剪切片工具 p48.ruokezhang.com" in lines[0]


def test_portrait_exports_carry_no_watermark():
    content = _watermark_overlay(
        output_layout="portrait", live_started_at="2026-08-29T12:15:00Z"
    )

    assert _dialogue_lines(content, "LandscapeWatermark") == []
    assert "p48.ruokezhang.com" not in content
