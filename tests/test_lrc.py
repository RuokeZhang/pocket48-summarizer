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
