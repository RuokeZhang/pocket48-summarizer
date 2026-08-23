from __future__ import annotations

from ..models import FinalSummary
from .chunking import format_clock


def render_summary_markdown(
    summary: FinalSummary,
    *,
    title: str,
    member_name: str,
    live_id: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 主播：{member_name}",
        f"- 直播 ID：`{live_id}`",
        "",
        "## 摘要",
        "",
        summary.overview,
        "",
        "## 时间线",
        "",
    ]
    for item in summary.timeline:
        lines.append(
            f"- **{format_clock(item.start_ms)}–{format_clock(item.end_ms)} "
            f"{item.title}**：{item.detail} "
            f"（字幕证据：{', '.join(map(str, item.evidence_segment_ids))}）"
        )
    lines.extend(["", "## 主要话题", ""])
    for topic in summary.topics:
        evidence = (
            f"（字幕证据：{', '.join(map(str, topic.evidence_segment_ids))}）"
            if topic.evidence_segment_ids
            else ""
        )
        lines.append(f"- **{topic.name}**：{topic.detail}{evidence}")
    lines.extend(["", "## 高光", ""])
    for item in summary.highlights:
        danmaku = (
            f"；弹幕参考：{item.danmaku_evidence}"
            if item.danmaku_evidence
            else ""
        )
        lines.append(
            f"- **{format_clock(item.start_ms)}–{format_clock(item.end_ms)} "
            f"{item.title}**：{item.detail} "
            f"（字幕证据：{', '.join(map(str, item.evidence_segment_ids))}"
            f"{danmaku}）"
        )
    lines.extend(["", "## 待核实项", ""])
    if summary.verification_needed:
        lines.extend(f"- {item}" for item in summary.verification_needed)
    else:
        lines.append("- 无")
    return "\n".join(lines).strip() + "\n"
