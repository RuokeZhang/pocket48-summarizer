from __future__ import annotations

from dataclasses import dataclass

from ..models import TranscriptSegment


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    index: int
    start_ms: int
    end_ms: int
    segment_ids: tuple[int, ...]
    text: str


def build_transcript_chunks(
    segments: list[TranscriptSegment],
    *,
    max_chars: int,
    max_duration_ms: int,
    overlap_segments: int,
) -> list[TranscriptChunk]:
    if not segments:
        return []
    if max_chars <= 0 or max_duration_ms <= 0:
        raise ValueError("chunk limits must be positive")
    chunks: list[TranscriptChunk] = []
    current: list[TranscriptSegment] = []
    current_length = 0

    def emit() -> None:
        nonlocal current, current_length
        if not current:
            return
        lines = [_format_segment(segment) for segment in current]
        chunks.append(
            TranscriptChunk(
                index=len(chunks),
                start_ms=current[0].start_ms,
                end_ms=current[-1].end_ms,
                segment_ids=tuple(segment.sequence for segment in current),
                text="\n".join(lines),
            )
        )
        overlap = current[-overlap_segments:] if overlap_segments else []
        current = list(overlap)
        current_length = sum(len(_format_segment(item)) + 1 for item in current)

    for segment in segments:
        line_length = len(_format_segment(segment)) + 1
        if current and (
            current_length + line_length > max_chars
            or segment.end_ms - current[0].start_ms > max_duration_ms
        ):
            emit()
            while current and (
                current_length + line_length > max_chars
                or segment.end_ms - current[0].start_ms > max_duration_ms
            ):
                removed = current.pop(0)
                current_length -= len(_format_segment(removed)) + 1
        current.append(segment)
        current_length += line_length
    if current:
        if (
            chunks
            and tuple(segment.sequence for segment in current)
            == chunks[-1].segment_ids
        ):
            return chunks
        emit()
    return chunks


def format_clock(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_segment(segment: TranscriptSegment) -> str:
    speaker = f" speaker={segment.speaker_id}" if segment.speaker_id else ""
    return (
        f'<segment id="{segment.sequence}" start="{format_clock(segment.start_ms)}"'
        f' end="{format_clock(segment.end_ms)}"{speaker}>'
        f"{segment.text}</segment>"
    )
