"""Font coverage probes for the libass overlay renderer.

libass resolves a glyph the styled font lacks by asking fontconfig for a
fallback, and simply draws nothing when no installed font covers the
codepoint. Emoji therefore vanish from an exported clip without any error,
which is how they went missing in production for weeks. These probes let the
export report the gap instead of shipping a video with holes in it.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from typing import Literal

# Supplementary-plane pictographs. Deliberately narrow: the BMP symbol blocks
# are largely covered by the CJK fonts we install, so including them would
# raise the warning on clips that render fine.
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff]")

# 🎉 PARTY POPPER, a plain single-codepoint emoji that every emoji font ships.
EMOJI_PROBE_CODEPOINT = 0x1F389

EmojiFontStatus = Literal["available", "missing", "unknown"]


def contains_emoji(value: str) -> bool:
    return bool(_EMOJI_RE.search(value))


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
    try:
        result = subprocess.run(
            ["fc-list", f":charset={EMOJI_PROBE_CODEPOINT:x}", "family"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (False, None)
    if result.returncode != 0:
        return (False, None)
    for line in result.stdout.splitlines():
        family = line.split(",", 1)[0].strip()
        if family:
            return (True, family)
    return (True, None)
