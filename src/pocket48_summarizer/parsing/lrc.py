from __future__ import annotations

import math
import re
import statistics
from collections import Counter

from ..models import DanmakuEntry, DanmakuPeak
from ..security import strip_control_chars

LRC_LINE_RE = re.compile(r"^\[([0-9:.]+)\](.*)$")
DANMAKU_SYNC_ADVANCE_MS = 3_000


def parse_lrc(text: str) -> list[DanmakuEntry]:
    parsed: list[tuple[int, int, str, str]] = []
    for original_index, raw_line in enumerate(text.splitlines()):
        match = LRC_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        source_timestamp_ms = parse_lrc_timestamp(match.group(1))
        if source_timestamp_ms is None:
            continue
        timestamp_ms = max(
            0,
            source_timestamp_ms - DANMAKU_SYNC_ADVANCE_MS,
        )
        body = match.group(2)
        author, separator, comment = body.partition("\t")
        if not separator:
            author, comment = "", body
        author = strip_control_chars(author)
        comment = strip_control_chars(comment)
        if not comment:
            continue
        parsed.append((timestamp_ms, original_index, author, comment))
    parsed.sort(key=lambda item: (item[0], item[1]))
    return [
        DanmakuEntry(
            sequence=index + 1,
            timestamp_ms=item[0],
            author=item[2],
            text=item[3],
        )
        for index, item in enumerate(parsed)
    ]


def parse_lrc_timestamp(value: str) -> int | None:
    parts = value.split(":")
    try:
        if len(parts) == 2:
            hours = 0
            minutes = int(parts[0])
            seconds = float(parts[1])
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
        else:
            return None
    except ValueError:
        return None
    if hours < 0 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
        return None
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def detect_danmaku_peaks(
    entries: list[DanmakuEntry],
    *,
    bucket_ms: int = 30_000,
    merge_distance_ms: int = 90_000,
    max_peak_duration_ms: int = 300_000,
    max_peaks: int = 10,
) -> list[DanmakuPeak]:
    if not entries:
        return []
    if bucket_ms <= 0 or max_peak_duration_ms <= 0:
        raise ValueError("bucket_ms and max_peak_duration_ms must be positive")
    counts = Counter(entry.timestamp_ms // bucket_ms for entry in entries)
    max_bucket = max(counts)
    series = [counts.get(index, 0) for index in range(max_bucket + 1)]
    smoothed = [
        sum(series[max(0, index - 1) : min(len(series), index + 2)]) / 3
        for index in range(len(series))
    ]
    median = statistics.median(smoothed)
    deviations = [abs(value - median) for value in smoothed]
    mad = statistics.median(deviations)
    sorted_values = sorted(smoothed)
    percentile_index = max(
        0, min(len(sorted_values) - 1, math.ceil(len(sorted_values) * 0.9) - 1)
    )
    threshold = max(3.0, median + 3 * mad, sorted_values[percentile_index])
    candidates: list[int] = []
    for index, value in enumerate(smoothed):
        previous = smoothed[index - 1] if index > 0 else -1
        following = smoothed[index + 1] if index + 1 < len(smoothed) else -1
        if value >= threshold and value >= previous and value >= following:
            candidates.append(index)
    merged: list[tuple[int, int]] = []
    for bucket in candidates:
        start = bucket * bucket_ms
        end = start + bucket_ms
        if merged and start - merged[-1][1] <= merge_distance_ms:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    bounded: list[tuple[int, int]] = []
    for start, end in merged:
        while end - start > max_peak_duration_ms:
            bounded.append((start, start + max_peak_duration_ms))
            start += max_peak_duration_ms
        if start < end:
            bounded.append((start, end))
    scored: list[tuple[float, int, int, list[DanmakuEntry]]] = []
    for start, end in bounded:
        window_entries = [
            entry for entry in entries if start <= entry.timestamp_ms < end
        ]
        if not window_entries:
            continue
        bucket_scores = smoothed[
            start // bucket_ms : max(start // bucket_ms + 1, end // bucket_ms)
        ]
        score = max(bucket_scores, default=0.0)
        scored.append((score, start, end, window_entries))
    scored.sort(key=lambda item: (-item[0], item[1]))
    peaks: list[DanmakuPeak] = []
    for rank, (score, start, end, window_entries) in enumerate(
        scored[:max_peaks], start=1
    ):
        sample_count = min(8, len(window_entries))
        sample_indexes = (
            [0]
            if sample_count == 1
            else [
                round(index * (len(window_entries) - 1) / (sample_count - 1))
                for index in range(sample_count)
            ]
        )
        samples = [
            {
                "timestamp_ms": entry.timestamp_ms,
                "author": entry.author,
                "text": entry.text,
            }
            for entry in (window_entries[index] for index in sample_indexes)
        ]
        peaks.append(
            DanmakuPeak(
                rank=rank,
                start_ms=start,
                end_ms=end,
                message_count=len(window_entries),
                score=round(score, 3),
                samples=samples,
            )
        )
    return peaks
