import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media import raster_overlays
from pocket48_summarizer.media.fonts import (
    emoji_font_status,
    has_chromatic_pixels,
)
from pocket48_summarizer.media.raster_overlays import (
    RasterAsset,
    RasterOverlayCue,
    RasterOverlayRenderer,
    RasterPlacement,
    RasterTextLine,
)


def test_renderer_fails_explicitly_without_raqm(monkeypatch):
    monkeypatch.setattr(
        raster_overlays.features,
        "check_module",
        lambda name: name == "freetype2",
    )
    monkeypatch.setattr(
        raster_overlays.features,
        "check_feature",
        lambda name: False,
    )

    with pytest.raises(AppError) as raised:
        RasterOverlayRenderer()

    assert raised.value.code == "color_emoji_unavailable"


def test_color_probe_rejects_monochrome_fallback_glyphs():
    monochrome = Image.new("RGBA", (2, 1), (255, 255, 255, 255))
    color = Image.new("RGBA", (2, 1), (255, 80, 20, 255))

    assert not has_chromatic_pixels(monochrome)
    assert has_chromatic_pixels(color)


def test_renderer_streams_assets_across_bounded_atlases(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(raster_overlays, "MAX_ATLAS_SIZE", 16)
    renderer = object.__new__(RasterOverlayRenderer)
    monkeypatch.setattr(
        renderer,
        "_render_asset",
        lambda asset: Image.new(
            "RGBA", (asset.width, asset.height), (255, 0, 0, 255)
        ),
    )
    placement = RasterPlacement(
        start_ms=0,
        end_ms=1000,
        y_from=0,
        y_to=0,
    )
    cues = tuple(
        RasterOverlayCue(
            asset=RasterAsset(
                width=10,
                height=10,
                lines=(
                    RasterTextLine(
                        text=str(index),
                        x=0,
                        y=0,
                        font_name="test",
                        font_size=10,
                        color=(255, 255, 255, 255),
                    ),
                ),
            ),
            x=0,
            placements=(placement,),
            layer=10,
        )
        for index in range(2)
    )

    bundle = renderer.render(cues, tmp_path / "overlay")

    assert bundle is not None
    assert len(bundle.atlas_paths) == 2
    assert [cue.atlas_index for cue in bundle.cues] == [0, 1]
    bundle.cleanup()


@pytest.mark.skipif(
    emoji_font_status() != "available",
    reason="Noto Color Emoji is not installed on this machine",
)
def test_renderer_rasterizes_mixed_text_into_rgba_atlas(tmp_path: Path):
    cue = RasterOverlayCue(
        asset=RasterAsset(
            width=420,
            height=100,
            lines=(
                RasterTextLine(
                    text="前🎉后",
                    x=10,
                    y=5,
                    font_name="Noto Sans CJK SC",
                    font_size=64,
                    color=(255, 255, 255, 255),
                ),
            ),
        ),
        x=20,
        placements=(
            RasterPlacement(
                start_ms=500,
                end_ms=2500,
                y_from=100,
                y_to=100,
            ),
        ),
        layer=20,
    )

    bundle = RasterOverlayRenderer().render((cue,), tmp_path / "overlay")

    assert bundle is not None
    assert len(bundle.atlas_paths) == 1
    with Image.open(bundle.atlas_paths[0]).convert("RGBA") as atlas:
        rendered = bundle.cues[0]
        crop = atlas.crop(
            (
                rendered.crop_x,
                rendered.crop_y,
                rendered.crop_x + rendered.width,
                rendered.crop_y + rendered.height,
            )
        )
        assert crop.getchannel("A").getbbox() is not None
        assert len(crop.getcolors(maxcolors=1_000_000) or []) > 20
    bundle.cleanup()


@pytest.mark.skipif(
    emoji_font_status() != "available",
    reason="Noto Color Emoji is not installed on this machine",
)
def test_render_line_preserves_the_full_text_stroke():
    renderer = RasterOverlayRenderer()
    line = RasterTextLine(
        text="测试",
        x=0,
        y=0,
        font_name="Noto Sans CJK SC",
        font_size=64,
        color=(255, 255, 255, 255),
        stroke_width=8,
        stroke_color=(0, 0, 0, 255),
    )
    font = renderer._font(line.font_name, line.font_size)
    ascent, descent = font.getmetrics()
    width = math.ceil(font.getlength(line.text)) + 16
    expected = Image.new("RGBA", (width, ascent + descent + 16))
    ImageDraw.Draw(expected).text(
        (8, 8 + ascent),
        line.text,
        font=font,
        fill=line.color,
        stroke_width=8,
        stroke_fill=line.stroke_color,
        anchor="ls",
    )
    ink_bbox = expected.getchannel("A").getbbox()
    assert ink_bbox is not None
    expected = expected.crop((0, ink_bbox[1], expected.width, ink_bbox[3]))

    actual = renderer._render_line(line)

    assert actual.size == expected.size
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.skipif(
    emoji_font_status() != "available",
    reason="Noto Color Emoji is not installed on this machine",
)
def test_render_line_keeps_emoji_centered_inside_stroke_padding():
    renderer = RasterOverlayRenderer()
    line = RasterTextLine(
        text="🎉",
        x=0,
        y=0,
        font_name="Noto Sans CJK SC",
        font_size=64,
        color=(255, 255, 255, 255),
        stroke_width=6,
        stroke_color=(0, 0, 0, 255),
    )

    rendered = renderer._render_line(line)
    ink_bbox = rendered.getchannel("A").getbbox()

    assert ink_bbox is not None
    assert abs(ink_bbox[0] - (rendered.width - ink_bbox[2])) <= 1
