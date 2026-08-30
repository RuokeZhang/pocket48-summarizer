from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from ..errors import AppError
from .fonts import (
    COLOR_EMOJI_NATIVE_SIZE,
    FontFace,
    require_color_emoji_font_path,
    resolve_font_face,
    split_emoji_runs,
)

TextAnchor = Literal["left", "center"]
MAX_ATLAS_SIZE = 4096
MAX_RASTER_CUES = 8192
ATLAS_PADDING = 2
MAX_EMOJI_CACHE_ENTRIES = 128


@dataclass(frozen=True, slots=True)
class RasterTextLine:
    text: str
    x: int
    y: int
    font_name: str
    font_size: int
    color: tuple[int, int, int, int]
    anchor: TextAnchor = "left"
    stroke_width: int = 0
    stroke_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    shadow_offset: int = 0
    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    scale_x: int = 100
    scale_y: int = 100


@dataclass(frozen=True, slots=True)
class RasterBox:
    fill: tuple[int, int, int, int]
    radius: int
    outline: tuple[int, int, int, int] | None = None
    outline_width: int = 0
    shadow: tuple[int, int, int, int] | None = None
    shadow_offset: int = 0
    shadow_blur: int = 0


@dataclass(frozen=True, slots=True)
class RasterAsset:
    width: int
    height: int
    lines: tuple[RasterTextLine, ...]
    box: RasterBox | None = None


@dataclass(frozen=True, slots=True)
class RasterPlacement:
    start_ms: int
    end_ms: int
    y_from: int
    y_to: int
    move_ms: int = 0


@dataclass(frozen=True, slots=True)
class RasterOverlayCue:
    asset: RasterAsset
    x: int
    placements: tuple[RasterPlacement, ...]
    layer: int
    fade_in_ms: int = 0


@dataclass(frozen=True, slots=True)
class RenderedRasterCue:
    atlas_index: int
    crop_x: int
    crop_y: int
    width: int
    height: int
    x: int
    placements: tuple[RasterPlacement, ...]
    layer: int
    fade_in_ms: int

    @property
    def start_ms(self) -> int:
        return self.placements[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.placements[-1].end_ms


@dataclass(frozen=True, slots=True)
class RasterOverlayBundle:
    atlas_paths: tuple[Path, ...]
    cues: tuple[RenderedRasterCue, ...]

    def cleanup(self) -> None:
        for path in self.atlas_paths:
            path.unlink(missing_ok=True)


class RasterOverlayRenderer:
    def __init__(self) -> None:
        if not features.check_module("freetype2") or not features.check_feature(
            "raqm"
        ):
            raise AppError(
                "color_emoji_unavailable",
                "Pillow 缺少 FreeType 或 RAQM，无法渲染彩色 emoji",
                False,
            )
        self._emoji_path = require_color_emoji_font_path()
        self._font_faces: dict[str, FontFace] = {}
        self._fonts: dict[
            tuple[Path, int, int], ImageFont.FreeTypeFont
        ] = {}
        self._emoji_font = ImageFont.truetype(
            str(self._emoji_path),
            COLOR_EMOJI_NATIVE_SIZE,
            layout_engine=ImageFont.Layout.RAQM,
        )
        emoji_bbox = self._emoji_font.getbbox("🎉")
        self._emoji_cell_height = max(
            1, emoji_bbox[3] - emoji_bbox[1]
        )
        emoji_ascent, _ = self._emoji_font.getmetrics()
        self._emoji_ascent_ratio = (
            emoji_ascent / self._emoji_cell_height
        )
        self._emoji_cache: OrderedDict[str, Image.Image] = OrderedDict()

    def render(
        self,
        cues: tuple[RasterOverlayCue, ...],
        output_prefix: Path,
    ) -> RasterOverlayBundle | None:
        if not cues:
            return None
        if len(cues) > MAX_RASTER_CUES:
            raise AppError(
                "clip_overlay_too_complex",
                "彩色 emoji 叠加元素过多，请缩短剪辑范围",
                False,
            )
        assets: list[RasterAsset] = []
        asset_indexes: dict[RasterAsset, int] = {}
        cue_asset_indexes: list[int] = []
        for cue in cues:
            index = asset_indexes.get(cue.asset)
            if index is None:
                index = len(assets)
                assets.append(cue.asset)
                asset_indexes[cue.asset] = index
            cue_asset_indexes.append(index)
        output_prefix.parent.mkdir(parents=True, exist_ok=True)
        atlas_paths: list[Path] = []
        locations: list[tuple[int, int, int]] = []
        try:
            fingerprint = hashlib.sha256(
                repr((assets, cues)).encode("utf-8")
            ).hexdigest()[:12]
            atlas = Image.new(
                "RGBA", (MAX_ATLAS_SIZE, MAX_ATLAS_SIZE)
            )
            atlas_index = 0
            cursor_x = ATLAS_PADDING
            cursor_y = ATLAS_PADDING
            row_height = 0
            used_width = 1
            used_height = 1
            for asset in assets:
                image = self._render_asset(asset)
                if (
                    image.width + ATLAS_PADDING * 2 > MAX_ATLAS_SIZE
                    or image.height + ATLAS_PADDING * 2 > MAX_ATLAS_SIZE
                ):
                    raise AppError(
                        "clip_overlay_too_large",
                        "彩色 emoji 叠加资源超过尺寸限制",
                        False,
                    )
                if (
                    cursor_x + image.width + ATLAS_PADDING
                    > MAX_ATLAS_SIZE
                ):
                    cursor_x = ATLAS_PADDING
                    cursor_y += row_height + ATLAS_PADDING
                    row_height = 0
                if (
                    cursor_y + image.height + ATLAS_PADDING
                    > MAX_ATLAS_SIZE
                ):
                    atlas_paths.append(
                        self._save_atlas(
                            atlas,
                            output_prefix,
                            fingerprint,
                            atlas_index,
                            used_width,
                            used_height,
                        )
                    )
                    atlas_index += 1
                    atlas = Image.new(
                        "RGBA", (MAX_ATLAS_SIZE, MAX_ATLAS_SIZE)
                    )
                    cursor_x = ATLAS_PADDING
                    cursor_y = ATLAS_PADDING
                    row_height = 0
                    used_width = 1
                    used_height = 1
                atlas.alpha_composite(image, (cursor_x, cursor_y))
                locations.append((atlas_index, cursor_x, cursor_y))
                used_width = max(
                    used_width,
                    cursor_x + image.width + ATLAS_PADDING,
                )
                used_height = max(
                    used_height,
                    cursor_y + image.height + ATLAS_PADDING,
                )
                cursor_x += image.width + ATLAS_PADDING
                row_height = max(row_height, image.height)
            atlas_paths.append(
                self._save_atlas(
                    atlas,
                    output_prefix,
                    fingerprint,
                    atlas_index,
                    used_width,
                    used_height,
                )
            )
        except BaseException:
            for path in atlas_paths:
                path.unlink(missing_ok=True)
            raise
        rendered_cues = tuple(
            RenderedRasterCue(
                atlas_index=locations[asset_index][0],
                crop_x=locations[asset_index][1],
                crop_y=locations[asset_index][2],
                width=cue.asset.width,
                height=cue.asset.height,
                x=cue.x,
                placements=cue.placements,
                layer=cue.layer,
                fade_in_ms=cue.fade_in_ms,
            )
            for cue, asset_index in zip(cues, cue_asset_indexes, strict=True)
        )
        return RasterOverlayBundle(
            atlas_paths=tuple(atlas_paths),
            cues=tuple(
                sorted(
                    rendered_cues,
                    key=lambda cue: (cue.layer, cue.start_ms, cue.x),
                )
            ),
        )

    @staticmethod
    def _save_atlas(
        atlas: Image.Image,
        output_prefix: Path,
        fingerprint: str,
        index: int,
        width: int,
        height: int,
    ) -> Path:
        path = output_prefix.with_name(
            f"{output_prefix.name}.emoji-{fingerprint}-{index}.png"
        )
        try:
            atlas.crop((0, 0, width, height)).save(
                path, "PNG", compress_level=6
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def _render_asset(self, asset: RasterAsset) -> Image.Image:
        if (
            asset.width <= 0
            or asset.height <= 0
            or asset.width > MAX_ATLAS_SIZE
            or asset.height > MAX_ATLAS_SIZE
        ):
            raise AppError(
                "clip_overlay_invalid",
                "彩色 emoji 叠加尺寸无效",
                False,
            )
        image = Image.new("RGBA", (asset.width, asset.height))
        if asset.box is not None:
            image = self._draw_box(image, asset.box)
        for line in asset.lines:
            line_image = self._render_line(line)
            x = (
                line.x - line_image.width // 2
                if line.anchor == "center"
                else line.x
            )
            image.alpha_composite(line_image, (x, line.y))
        return image

    @staticmethod
    def _draw_box(image: Image.Image, box: RasterBox) -> Image.Image:
        if box.shadow and box.shadow_offset:
            shadow = Image.new("RGBA", image.size)
            draw = ImageDraw.Draw(shadow)
            draw.rounded_rectangle(
                (
                    box.shadow_offset,
                    box.shadow_offset,
                    image.width - 1,
                    image.height - 1,
                ),
                radius=box.radius,
                fill=box.shadow,
            )
            if box.shadow_blur:
                shadow = shadow.filter(
                    ImageFilter.GaussianBlur(box.shadow_blur)
                )
            image = Image.alpha_composite(image, shadow)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, image.width - 1, image.height - 1),
            radius=box.radius,
            fill=box.fill,
            outline=box.outline,
            width=box.outline_width,
        )
        return image

    def _render_line(self, line: RasterTextLine) -> Image.Image:
        font = self._font(line.font_name, line.font_size)
        ascent, descent = font.getmetrics()
        stroke = max(0, line.stroke_width)
        pieces: list[tuple[Image.Image, float, int, bool]] = []
        width = 0.0
        height = ascent + descent + stroke * 2
        for is_emoji, text in split_emoji_runs(line.text):
            if not text:
                continue
            if is_emoji:
                piece = self._scaled_emoji(text, line.font_size)
                advance = float(piece.width)
                piece_y = max(
                    0,
                    stroke
                    + ascent
                    - round(
                        piece.height * self._emoji_ascent_ratio
                    ),
                )
            else:
                advance = float(font.getlength(text))
                piece_width = max(1, math.ceil(advance) + stroke * 2)
                piece = Image.new(
                    "RGBA",
                    (piece_width, max(1, height)),
                )
                ImageDraw.Draw(piece).text(
                    (stroke, stroke + ascent),
                    text,
                    font=font,
                    fill=line.color,
                    stroke_width=stroke,
                    stroke_fill=line.stroke_color,
                    anchor="ls",
                )
                piece_y = 0
            pieces.append((piece, advance, piece_y, is_emoji))
            width += advance
        output = Image.new(
            "RGBA",
            (max(1, math.ceil(width) + stroke * 2), max(1, height)),
        )
        cursor = 0.0
        for piece, advance, piece_y, is_emoji in pieces:
            piece_x = round(cursor + stroke) if is_emoji else round(cursor)
            output.alpha_composite(piece, (piece_x, piece_y))
            cursor += advance
        ink_bbox = output.getchannel("A").getbbox()
        if ink_bbox is not None:
            output = output.crop(
                (0, ink_bbox[1], output.width, ink_bbox[3])
            )
        if line.scale_x != 100 or line.scale_y != 100:
            output = output.resize(
                (
                    max(1, round(output.width * line.scale_x / 100)),
                    max(1, round(output.height * line.scale_y / 100)),
                ),
                Image.Resampling.LANCZOS,
            )
        if line.shadow_offset > 0 and line.shadow_color[3] > 0:
            shadowed = Image.new(
                "RGBA",
                (
                    output.width + line.shadow_offset,
                    output.height + line.shadow_offset,
                ),
            )
            shadow = Image.new("RGBA", output.size, line.shadow_color)
            shadow.putalpha(output.getchannel("A"))
            shadowed.alpha_composite(
                shadow,
                (line.shadow_offset, line.shadow_offset),
            )
            shadowed.alpha_composite(output)
            output = shadowed
        return output

    def _font(self, family: str, size: int) -> ImageFont.FreeTypeFont:
        if size <= 0:
            raise AppError(
                "clip_overlay_invalid",
                "彩色文字字号无效",
                False,
            )
        face = self._font_faces.get(family)
        if face is None:
            face = resolve_font_face(family)
            self._font_faces[family] = face
        key = (face.path, face.index, size)
        font = self._fonts.get(key)
        if font is None:
            font = ImageFont.truetype(
                str(face.path),
                size,
                index=face.index,
                layout_engine=ImageFont.Layout.RAQM,
            )
            self._fonts[key] = font
        return font

    def _scaled_emoji(self, text: str, target_size: int) -> Image.Image:
        cached = self._emoji_cache.get(text)
        if cached is None:
            bbox = self._emoji_font.getbbox(text)
            advance = max(
                1, math.ceil(self._emoji_font.getlength(text))
            )
            cell_height = max(
                self._emoji_cell_height,
                bbox[3] - bbox[1],
            )
            native = Image.new("RGBA", (advance, cell_height))
            draw = ImageDraw.Draw(native)
            draw.text(
                (0, -bbox[1]),
                text,
                font=self._emoji_font,
                embedded_color=True,
            )
            if native.getchannel("A").getbbox() is None:
                raise AppError(
                    "color_emoji_unsupported",
                    f"服务器彩色 emoji 字体不支持：{text}",
                    False,
                )
            cached = native
            self._emoji_cache[text] = cached
            if len(self._emoji_cache) > MAX_EMOJI_CACHE_ENTRIES:
                self._emoji_cache.popitem(last=False)
        else:
            self._emoji_cache.move_to_end(text)
        scale = target_size / max(1, cached.height)
        return cached.resize(
            (
                max(1, round(cached.width * scale)),
                max(1, target_size),
            ),
            Image.Resampling.LANCZOS,
        )
