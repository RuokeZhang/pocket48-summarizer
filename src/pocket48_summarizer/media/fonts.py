"""Emoji font selection for the libass overlay renderer.

libass cannot rasterise a colour font, and it resolves a glyph the styled font
lacks by asking fontconfig for a fallback. On a machine that has any colour
emoji font installed, fontconfig answers with that font, libass gets no
outline, and it silently draws nothing -- so installing a monochrome emoji
font is not enough to make emoji appear. The overlay therefore names the
monochrome family explicitly in the ASS text instead of trusting fallback,
and this module owns which characters need that treatment.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal

# The monochrome outline emoji font installed by the deployment scripts.
# libass is asked for it by name, so fontconfig's fallback preference -- which
# favours a colour font whenever one is installed -- cannot hijack the glyph.
EMOJI_FONT_FAMILY = "Symbola"

# Unicode gives every emoji-capable codepoint a default presentation, and only
# the ones defaulting to *emoji* presentation break: those are the ones
# fontconfig answers with a colour font. Characters defaulting to text
# presentation (☀ ★ ☺ ♥ ✔ ➡ ...) resolve to the installed CJK font and already
# render, so routing them here would restyle glyphs that are not broken.
_EMOJI_PRESENTATION_SUPPLEMENTARY = "\U0001f000-\U0001faff"
_EMOJI_PRESENTATION_BMP = (
    "\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2614\u2615"
    "\u2648-\u2653\u267f\u2693\u26a1\u26aa\u26ab\u26bd\u26be"
    "\u26c4\u26c5\u26ce\u26d4\u26ea\u26f2\u26f3\u26f5\u26fa"
    "\u26fd\u2705\u270a\u270b\u2728\u274c\u274e\u2753-\u2755"
    "\u2757\u2795-\u2797\u27b0\u27bf\u2b1b\u2b1c\u2b50\u2b55"
)
# A text-presentation character followed by U+FE0F is an explicit request for
# the colour glyph, so it fails the same way and belongs to the same run.
_EMOJI_TEXT_DEFAULT = "\u0023\u002a\u0030-\u0039\u00a9\u00ae\u203c-\u3299"
# Joiners, variation selectors, keycaps and skin tones only ever extend a run.
_EMOJI_MODIFIER = "\u200d\ufe0e\ufe0f\u20e3\U0001f3fb-\U0001f3ff"

_EMOJI_ATOM = (
    f"(?:[{_EMOJI_PRESENTATION_SUPPLEMENTARY}{_EMOJI_PRESENTATION_BMP}]"
    f"|[{_EMOJI_TEXT_DEFAULT}]\ufe0f)"
)
# Whole grapheme clusters are matched together so a family or skin-tone
# sequence is not split across two font changes.
_EMOJI_RE = re.compile(
    f"{_EMOJI_ATOM}(?:{_EMOJI_ATOM}|[{_EMOJI_MODIFIER}])*"
)

# 🎉 PARTY POPPER, a plain single-codepoint emoji that every emoji font ships.
EMOJI_PROBE_CODEPOINT = 0x1F389

EmojiFontStatus = Literal["available", "missing", "unknown"]


def contains_emoji(value: str) -> bool:
    return bool(_EMOJI_RE.search(value))


def split_emoji_runs(value: str) -> Iterator[tuple[bool, str]]:
    """Yield ``(is_emoji, text)`` chunks covering ``value`` in order."""

    cursor = 0
    for match in _EMOJI_RE.finditer(value):
        if match.start() > cursor:
            yield (False, value[cursor : match.start()])
        yield (True, match.group())
        cursor = match.end()
    if cursor < len(value):
        yield (False, value[cursor:])


@lru_cache(maxsize=1)
def emoji_font_family() -> str | None:
    """Return the first installed family covering emoji, if it can be probed.

    ``None`` covers both "no such font" and "cannot ask", so callers that need
    to tell those apart should use :func:`emoji_font_status`.
    """

    return _probe_emoji_font()[1]


@lru_cache(maxsize=1)
def emoji_font_status() -> EmojiFontStatus:
    probed, family = _probe_emoji_font()
    if not probed:
        return "unknown"
    return "available" if family else "missing"


def reset_font_probe_cache() -> None:
    _probe_emoji_font.cache_clear()
    emoji_font_family.cache_clear()
    emoji_font_status.cache_clear()


@lru_cache(maxsize=1)
def _probe_emoji_font() -> tuple[bool, str | None]:
    """Check that the family we name in the ASS text is actually installed.

    The query is constrained to :data:`EMOJI_FONT_FAMILY` rather than asking
    which font happens to cover the codepoint, because any answer other than
    the family we request tells us nothing about what libass will draw.
    """

    try:
        result = subprocess.run(
            [
                "fc-list",
                f":family={EMOJI_FONT_FAMILY}"
                f":charset={EMOJI_PROBE_CODEPOINT:x}",
                "family",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (False, None)
    if result.returncode != 0:
        return (False, None)
    if any(line.strip() for line in result.stdout.splitlines()):
        return (True, EMOJI_FONT_FAMILY)
    return (True, None)
