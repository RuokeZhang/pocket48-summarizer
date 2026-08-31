from __future__ import annotations

import pytest

from pocket48_summarizer.errors import ExternalServiceError

from scripts.scan_room_voice import (
    chunks,
    parse_positive_int,
    require_success_content,
)


def test_chunks_preserve_order():
    assert list(chunks([1, 2, 3, 4, 5], 2)) == [
        [1, 2],
        [3, 4],
        [5],
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("123", 123), (123, 123), ("0", None), ("bad", None), (None, None)],
)
def test_parse_positive_int(value, expected):
    assert parse_positive_int(value) == expected


def test_requires_successful_bounded_envelope():
    assert require_success_content(
        {"status": 200, "success": True, "content": {"items": []}},
        "测试",
    ) == {"items": []}
    with pytest.raises(ExternalServiceError):
        require_success_content(
            {"status": 500, "success": False, "content": None},
            "测试",
        )
