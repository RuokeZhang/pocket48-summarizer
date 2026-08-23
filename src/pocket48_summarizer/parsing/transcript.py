from __future__ import annotations

from typing import Any

from ..errors import AppError
from ..models import TranscriptSegment
from ..security import strip_control_chars


def normalize_asr_result(payload: dict[str, Any]) -> list[TranscriptSegment]:
    transcripts = payload.get("transcripts")
    if transcripts is None and isinstance(payload.get("output"), dict):
        transcripts = payload["output"].get("transcripts")
    if not isinstance(transcripts, list):
        raise AppError(
            "invalid_asr_result",
            "语音识别结果缺少 transcripts",
            False,
        )
    raw_sentences: list[dict[str, Any]] = []
    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        sentences = transcript.get("sentences", [])
        if isinstance(sentences, list):
            raw_sentences.extend(
                sentence for sentence in sentences if isinstance(sentence, dict)
            )
    if not raw_sentences:
        raise AppError(
            "empty_transcript",
            "语音识别没有返回可用字幕",
            False,
        )
    normalized: list[tuple[int, int, str | None, str]] = []
    for sentence in raw_sentences:
        text = strip_control_chars(str(sentence.get("text", "")))
        if not text:
            continue
        start = _integer_value(
            sentence,
            "begin_time",
            "start_time",
            "begin_time_in_milliseconds",
        )
        end = _integer_value(
            sentence,
            "end_time",
            "end_time_in_milliseconds",
        )
        if start is None:
            continue
        if end is None or end <= start:
            end = start + 1
        speaker = sentence.get("speaker_id")
        normalized.append(
            (max(0, start), max(1, end), None if speaker is None else str(speaker), text)
        )
    normalized.sort(key=lambda item: (item[0], item[1]))
    segments: list[TranscriptSegment] = []
    previous_start = 0
    for index, (start, end, speaker, text) in enumerate(normalized, start=1):
        start = max(previous_start, start)
        end = max(start + 1, end)
        segments.append(
            TranscriptSegment(
                sequence=index,
                start_ms=start,
                end_ms=end,
                speaker_id=speaker,
                text=text,
            )
        )
        previous_start = start
    if not segments:
        raise AppError(
            "empty_transcript",
            "语音识别没有返回可用字幕",
            False,
        )
    return segments


def transcript_to_srt(segments: list[TranscriptSegment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.replace("\r", " ").replace("\n", " ").strip()
        prefix = f"[说话人 {segment.speaker_id}] " if segment.speaker_id else ""
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment.start_ms)} --> "
                    f"{format_srt_time(segment.end_ms)}",
                    prefix + text,
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def format_srt_time(milliseconds: int) -> str:
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _integer_value(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
