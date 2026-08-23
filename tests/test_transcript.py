import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.parsing.transcript import (
    normalize_asr_result,
    transcript_to_srt,
)


def test_normalizes_asr_and_generates_srt():
    segments = normalize_asr_result(
        {
            "transcripts": [
                {
                    "sentences": [
                        {
                            "begin_time": 1000,
                            "end_time": 2500,
                            "text": "大家下午好",
                            "speaker_id": 0,
                        },
                        {
                            "begin_time": 2600,
                            "end_time": 4000,
                            "text": "今天聊一下最近的事情",
                        },
                    ]
                }
            ]
        }
    )
    assert len(segments) == 2
    srt = transcript_to_srt(segments)
    assert "00:00:01,000 --> 00:00:02,500" in srt
    assert "[说话人 0] 大家下午好" in srt


def test_rejects_empty_asr_result():
    with pytest.raises(AppError, match="可用字幕"):
        normalize_asr_result({"transcripts": []})
