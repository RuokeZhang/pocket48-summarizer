"""Shared China-time formatting.

The clip watermark and the web pages must agree on how a replay's start time
reads, so the parsing lives here rather than being reimplemented next to each
caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def china_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(CHINA_TIMEZONE)


def format_china_datetime(value: str | None, *, fallback: str = "") -> str:
    parsed = china_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else fallback
