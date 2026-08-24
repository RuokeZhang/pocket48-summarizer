from pocket48_summarizer.parsing.lrc import (
    detect_danmaku_peaks,
    parse_lrc,
    parse_lrc_timestamp,
)


def test_parse_lrc_sorts_and_preserves_unicode():
    entries = parse_lrc(
        "\n".join(
            [
                "[00:01:03.879]娜娜\t下午好",
                "[00:00:31.345]小咪\t这么早👀",
                "invalid",
                "[00:00:31.500]\t只有评论",
            ]
        )
    )
    assert [entry.sequence for entry in entries] == [1, 2, 3]
    assert entries[0].timestamp_ms == 31_345
    assert entries[0].text == "这么早👀"
    assert entries[2].author == "娜娜"


def test_lrc_timestamp_variants():
    assert parse_lrc_timestamp("01:02.500") == 62_500
    assert parse_lrc_timestamp("02:01:02.500") == 7_262_500
    assert parse_lrc_timestamp("00:99.000") is None


def test_detects_bounded_danmaku_peak():
    text = "\n".join(
        [f"[00:00:{index % 30:02d}.000]a\tquiet" for index in range(3)]
        + [f"[00:05:{index % 30:02d}.000]b\tpeak-{index}" for index in range(40)]
    )
    peaks = detect_danmaku_peaks(parse_lrc(text))
    assert peaks
    assert peaks[0].message_count >= 40
    assert len(peaks[0].samples) <= 8


def test_splits_long_continuous_peak_without_losing_late_window():
    lines = []
    for bucket in range(24):
        total_seconds = bucket * 30
        minutes, seconds = divmod(total_seconds, 60)
        for index in range(12):
            lines.append(
                f"[00:{minutes:02d}:{seconds:02d}.{index:03d}]fan"
                f"\tactive-{bucket}-{index}"
            )

    peaks = detect_danmaku_peaks(parse_lrc("\n".join(lines)))

    assert len(peaks) >= 3
    assert all(0 < peak.end_ms - peak.start_ms <= 300_000 for peak in peaks)
    assert any(
        peak.start_ms <= 660_000 < peak.end_ms
        for peak in peaks
    )
