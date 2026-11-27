"""Deterministic statistics for the public annual report.

Everything here is pure: callers pass already-fetched archive messages, the
selected member's replays and their danmaku lines. No model is involved.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

CHINA = ZoneInfo("Asia/Shanghai")
DAY_MS = 86_400_000
ALIAS_GRACE_MS = 2 * DAY_MS
SCOPES = {"2025-2026": (2025, 2026), "2026": (2026,)}
CATCHPHRASE_MIN_SUPPORT = 3
# Generic words everyone types ("不是", "好看") sit near 1x; a real catchphrase
# the fan leans on sits several times higher, so the threshold splits them while
# still letting quieter fans surface something.
CATCHPHRASE_MIN_LIFT = 5.0
CATCHPHRASE_TOP_N = 6
CATCHPHRASE_BG_SAMPLE = 80000
CATCHPHRASE_FILLER = set("哈啊嗯哦呜嘿哇嘤咦诶欸唉呀哟喔嗷么了的是我你他她着呢吧啦哒呀喂")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")
# Pocket48 chat stickers arrive inline as "[拥抱]" / "【鼓掌】". They are room-only
# icons, so the word inside is not a catchphrase; strip the whole code first.
_EMOJI_CODE_RE = re.compile(r"[\[【][^\[\]【】]{1,8}[\]】]")


def _catchphrase_grams(text: str) -> set[str]:
    grams: set[str] = set()
    text = _EMOJI_CODE_RE.sub("", text)
    for run in _CJK_RUN_RE.findall(text):
        for size in (2, 3, 4):
            for index in range(len(run) - size + 1):
                grams.add(run[index : index + size])
    return grams


# Connective characters worth stripping from the *front* of a phrase ("的小祖宗"
# -> "小祖宗"). The tail is left alone: endings like 吧/呢/了 are often the point
# of the catchphrase ("不是吧", "拉倒吧").
CATCHPHRASE_LEADING_TRIM = set("的了着")


def _trim_filler(phrase: str) -> str:
    start = 0
    while len(phrase) - start > 2 and phrase[start] in CATCHPHRASE_LEADING_TRIM:
        start += 1
    return phrase[start:]


def extract_catchphrases(
    fan_texts_by_year: dict[int, list[str]],
    background_texts: list[str],
    member_name: str,
) -> dict:
    """Each fan's own catchphrases: short phrases they use far more than the
    average fan watching the same member, so the list reflects the person
    instead of the generic words everyone types."""
    fan_doc_freq: Counter = Counter()
    per_year: dict[str, Counter] = {}
    fan_total = 0
    for year, texts in fan_texts_by_year.items():
        year_counter: Counter = Counter()
        for text in texts:
            fan_total += 1
            for gram in _catchphrase_grams(text):
                fan_doc_freq[gram] += 1
                year_counter[gram] += 1
        per_year[str(year)] = year_counter

    year_totals = {str(year): len(texts) for year, texts in fan_texts_by_year.items()}
    # The member's own name is not a catchphrase; drop any phrase that overlaps
    # it (either way), so "蒋舒婷" and "舒婷蒋舒婷" are both skipped.
    member_bigrams = {member_name[index : index + 2] for index in range(len(member_name) - 1)}
    candidate_grams = {
        gram
        for gram, count in fan_doc_freq.items()
        if count >= CATCHPHRASE_MIN_SUPPORT
        and not all(char in CATCHPHRASE_FILLER for char in gram)
        and gram not in member_name
        and not any(bigram in gram for bigram in member_bigrams)
    }
    if fan_total == 0 or not candidate_grams:
        return {"items": [], "year_totals": year_totals}

    # Which of the fan's messages each candidate appears in. When someone repeats
    # one sentence, its fragments ("上次还有", "页没写") share the same messages,
    # so this lets us keep just one of them instead of a row of slivers.
    gram_messages: dict[str, set[int]] = {gram: set() for gram in candidate_grams}
    message_index = 0
    for texts in fan_texts_by_year.values():
        for text in texts:
            for gram in _catchphrase_grams(text):
                if gram in candidate_grams:
                    gram_messages[gram].add(message_index)
            message_index += 1

    # Background frequency only for the fan's candidate phrases, so a popular
    # member's millions of danmaku never build a giant counter.
    background_doc_freq: Counter = Counter()
    background_total = 0
    for text in background_texts:
        background_total += 1
        for gram in _catchphrase_grams(text):
            if gram in candidate_grams:
                background_doc_freq[gram] += 1
    if background_total == 0:
        return {"items": [], "year_totals": year_totals}

    scored = []
    for gram in candidate_grams:
        count = fan_doc_freq[gram]
        fan_rate = count / fan_total
        # Floor the background at one sighting so an unseen phrase cannot score
        # infinitely. A rate floor (not a +k count) keeps the lift stable no
        # matter how big the sampled background is.
        background_rate = max(background_doc_freq[gram], 1) / background_total
        lift = fan_rate / background_rate
        if lift < CATCHPHRASE_MIN_LIFT:
            continue
        scored.append((gram, count, lift))
    # Highest lift first; a longer phrase breaks ties so "打个商量" beats "个商量".
    scored.sort(key=lambda entry: (-entry[2], -len(entry[0]), -entry[1], entry[0]))

    def bigrams(value: str) -> set[str]:
        return {value[index : index + 2] for index in range(len(value) - 1)}

    items: list[dict] = []
    chosen_bigrams: list[set[str]] = []
    chosen_messages: list[set[int]] = []
    for gram, count, lift in scored:
        gram_bigrams = bigrams(gram)
        # Skip variants of a phrase already shown (祖宗 after 小祖宗) so each
        # family appears once, represented by its strongest member.
        if any(gram_bigrams & seen for seen in chosen_bigrams):
            continue
        # Skip a fragment that rides along in the same messages as a phrase
        # already shown (both came from one repeated sentence).
        messages = gram_messages[gram]
        if any(len(messages & seen) >= 0.6 * len(messages) for seen in chosen_messages):
            continue
        phrase = _trim_filler(gram)
        items.append({
            "phrase": phrase,
            "total": count,
            "lift": round(lift, 1),
            "per_year": {year: counter[gram] for year, counter in per_year.items()},
        })
        chosen_bigrams.append(gram_bigrams)
        chosen_messages.append(messages)
        if len(items) >= CATCHPHRASE_TOP_N:
            break
    return {"items": items, "year_totals": year_totals}
DELAY_BUCKETS = [
    (0, 2, "2分钟内"),
    (2, 5, "2—5分钟"),
    (5, 10, "5—10分钟"),
    (10, 30, "10—30分钟"),
    (30, math.inf, "30分钟以后"),
]
DANMAKU_EVENT_RE = re.compile(
    r"^(?:送出了?|赠送了?|送给\S+\s*\d+个|进入了?直播间|加入了?直播间|点亮了|"
    r"为主播点了赞|给主播点了赞|\[礼物\])"
)
SPOKEN_ROOM_TYPES = {"TEXT", "REPLY"}
# Bump when the statistics change meaning, so cached results are not reused.
REPORT_VERSION = 9
LIVE_GIFT_RE = re.compile(r"^送给\S+\s*(\d+)个(.+)$")


@dataclass(slots=True)
class RoomMessage:
    timestamp_ms: int
    room_owner: str
    nickname: str
    msg_type: str
    content: str


@dataclass(slots=True)
class Replay:
    live_id: str
    started_at_ms: int
    title: str
    lines: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass(slots=True)
class AliasWindow:
    key: str
    start_ms: float
    end_ms: float


def normalize_nickname(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def china_time(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, CHINA)


def room_message_text(message: RoomMessage) -> str | None:
    if message.msg_type == "TEXT":
        return message.content.strip() or None
    if message.msg_type == "REPLY":
        try:
            payload = json.loads(message.content)
        except ValueError:
            return None
        reply = payload.get("replyInfo") if isinstance(payload, dict) else None
        text = reply.get("text") if isinstance(reply, dict) else None
        return text.strip() if isinstance(text, str) and text.strip() else None
    return None


def build_alias_windows(
    archive_nicknames: list[tuple[str, int]],
    room_messages: list[RoomMessage],
    extra_nicknames: list[str],
) -> list[AliasWindow]:
    """Each historical nickname only counts while the user actually used it.

    A nickname is valid from shortly before it first appeared until the next
    nickname took over (or its last sighting, if it came back). The most
    recently used nickname stays valid. Nicknames the user typed in by hand
    are trusted for the whole period.
    """
    first_seen: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for nickname, first_used_ms in archive_nicknames:
        key = normalize_nickname(nickname)
        if len(key) < 2:
            continue
        first_seen[key] = min(first_seen.get(key, first_used_ms), first_used_ms)
        last_seen[key] = max(last_seen.get(key, first_used_ms), first_used_ms)
    for message in room_messages:
        key = normalize_nickname(message.nickname)
        if len(key) < 2:
            continue
        first_seen[key] = min(first_seen.get(key, message.timestamp_ms), message.timestamp_ms)
        last_seen[key] = max(last_seen.get(key, message.timestamp_ms), message.timestamp_ms)
    ordered = sorted(first_seen, key=lambda key: (first_seen[key], key))
    latest_key = max(last_seen, key=lambda key: (last_seen[key], key)) if last_seen else None
    windows = []
    for index, key in enumerate(ordered):
        start = first_seen[key] - ALIAS_GRACE_MS
        if key == latest_key:
            end = math.inf
        else:
            following = first_seen[ordered[index + 1]] if index + 1 < len(ordered) else last_seen[key]
            end = max(following, last_seen[key]) + ALIAS_GRACE_MS
        windows.append(AliasWindow(key, start, end))
    known = {window.key for window in windows}
    for nickname in extra_nicknames:
        key = normalize_nickname(nickname)
        if len(key) >= 2 and key not in known:
            windows.append(AliasWindow(key, -math.inf, math.inf))
            known.add(key)
    return windows


def _own_lines(replays: list[Replay], windows: list[AliasWindow]):
    """Yield (replay, sent_ms, author, text) for lines the fan wrote."""
    by_key: dict[str, list[AliasWindow]] = defaultdict(list)
    for window in windows:
        by_key[window.key].append(window)
    for replay in replays:
        for offset_ms, author, text in replay.lines:
            candidates = by_key.get(normalize_nickname(author))
            if not candidates:
                continue
            sent_ms = replay.started_at_ms + offset_ms
            if any(window.start_ms <= sent_ms <= window.end_ms for window in candidates):
                yield replay, sent_ms, author, text


def match_danmaku(
    replays: list[Replay], windows: list[AliasWindow]
) -> dict[str, list[tuple[int, str, str]]]:
    matched: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for replay, sent_ms, author, text in _own_lines(replays, windows):
        if not DANMAKU_EVENT_RE.match(text.strip()):
            matched[replay.live_id].append((sent_ms, author, text))
    return dict(matched)


def gift_name(value: str) -> str:
    name = value.strip()
    # Team glow sticks ("荧光棒-X", "荧光棒-SII") are one gift for the poster.
    return "荧光棒" if name.startswith("荧光棒") else name


def match_live_gifts(
    replays: list[Replay], windows: list[AliasWindow]
) -> list[tuple[int, str, int]]:
    gifts = []
    for _, sent_ms, _, text in _own_lines(replays, windows):
        match = LIVE_GIFT_RE.match(text.strip())
        if match:
            gifts.append((sent_ms, gift_name(match.group(2)), int(match.group(1))))
    return gifts


def room_gift(message: RoomMessage) -> tuple[str, int] | None:
    if message.msg_type != "GIFT_TEXT":
        return None
    try:
        info = json.loads(message.content).get("giftInfo") or {}
        name, count = gift_name(str(info.get("giftName") or "")), int(info.get("giftNum") or 1)
    except (ValueError, AttributeError, TypeError):
        return None
    return (name, max(1, count)) if name else None


def _quote_candidates(lines: list[tuple[int, str, str]], count: int) -> list[str]:
    seen = set()
    candidates = []
    for sent_ms, _, text in lines:
        cleaned = text.strip()
        if not 4 <= len(cleaned) <= 30 or cleaned in seen:
            continue
        if len(set(cleaned)) <= 2 or re.fullmatch(r"[\d\W_]+", cleaned):
            continue
        seen.add(cleaned)
        candidates.append((len(cleaned), sent_ms, cleaned))
    chosen = sorted(candidates, key=lambda item: (-item[0], item[1]))[:count]
    return [text for _, _, text in sorted(chosen, key=lambda item: item[1])]


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def build_report(
    *,
    member_name: str,
    scope: str,
    data_end: date,
    archive_nicknames: list[tuple[str, int]],
    room_messages: list[RoomMessage],
    replays: list[Replay],
    extra_nicknames: list[str],
    gift_table: dict | None = None,
) -> dict:
    """gift_table: {"prices": {gift name: 鸡腿}, "scores": {gift name: 分 per piece}}."""
    years = SCOPES[scope]
    scope_start = date(years[0], 1, 1)
    scope_end = min(date(years[-1], 12, 31), data_end)

    def in_scope(timestamp_ms: int) -> bool:
        return china_time(timestamp_ms).year in years and china_time(timestamp_ms).date() <= scope_end

    windows = build_alias_windows(archive_nicknames, room_messages, extra_nicknames)
    scoped_replays = [replay for replay in replays if in_scope(replay.started_at_ms)]
    matched = match_danmaku(scoped_replays, windows)

    # The poster is about one fan and one member: only her room and her
    # lives count. Messages in other members' rooms are ignored.
    spoken: list[dict] = []
    for message in room_messages:
        text = room_message_text(message)
        if text is None or not in_scope(message.timestamp_ms) or message.room_owner != member_name:
            continue
        spoken.append({"ms": message.timestamp_ms, "kind": "room", "room": message.room_owner,
                       "nickname": message.nickname, "text": text})
    for replay in scoped_replays:
        for sent_ms, author, text in matched.get(replay.live_id, []):
            if in_scope(sent_ms):
                spoken.append({"ms": sent_ms, "kind": "danmaku", "room": member_name,
                               "nickname": author, "text": text, "live": replay.live_id})
    spoken.sort(key=lambda item: item["ms"])

    months = []
    cursor = scope_start
    while cursor <= scope_end:
        months.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    monthly = {month: {"month": month, "room": 0, "danmaku": 0, "lives": 0, "attended": 0}
               for month in months}
    daily: Counter = Counter()
    hours = [0] * 24
    for item in spoken:
        moment = china_time(item["ms"])
        monthly[moment.strftime("%Y-%m")][item["kind"]] += 1
        daily[moment.date().isoformat()] += 1
        hours[moment.hour] += 1

    replay_by_id = {replay.live_id: replay for replay in scoped_replays}
    delays: dict[int, list[float]] = defaultdict(list)
    for replay in scoped_replays:
        month = china_time(replay.started_at_ms).strftime("%Y-%m")
        if month not in monthly:
            continue
        monthly[month]["lives"] += 1
        lines = matched.get(replay.live_id)
        if lines:
            monthly[month]["attended"] += 1
            delays[china_time(replay.started_at_ms).year].append(
                max(0.0, (min(line[0] for line in lines) - replay.started_at_ms) / 60_000)
            )

    delay_summary = {}
    for year in years:
        values = delays.get(year, [])
        if not values:
            continue
        delay_summary[str(year)] = {
            "median_minutes": round(statistics.median(values), 1),
            "within_10": sum(value < 10 for value in values),
            "buckets": [
                {"label": label, "count": sum(low <= value < high for value in values)}
                for low, high, label in DELAY_BUCKETS
            ],
        }

    top_lives = []
    for live_id, lines in sorted(matched.items(), key=lambda item: (-len(item[1]), item[0]))[:3]:
        replay = replay_by_id[live_id]
        nicknames = list(dict.fromkeys(line[1] for line in lines))
        top_lives.append({
            "title": replay.title or "未命名直播",
            "started_at": _iso(china_time(replay.started_at_ms)),
            "count": len(lines),
            "nicknames": nicknames,
            "quotes": _quote_candidates(lines, 3),
        })

    active_days = sorted(date.fromisoformat(day) for day in daily)
    longest = {"days": 0, "start": None, "end": None}
    run_start = previous = None
    for day in active_days:
        if previous is None or (day - previous).days != 1:
            run_start = day
        run_length = (day - run_start).days + 1
        if run_length > longest["days"]:
            longest = {"days": run_length, "start": run_start.isoformat(), "end": day.isoformat()}
        previous = day

    # Catchphrases draw on everything the fan wrote to this member — both live
    # danmaku and room messages — so quieter fans who mostly post in the room
    # still get a list. The distinctiveness background stays the live danmaku of
    # everyone in her lives (we have no all-fans room corpus), which is why the
    # fan's room-only habits such as "早安" can score as their own.
    fan_texts_by_year: dict[int, list[str]] = {year: [] for year in years}
    for item in spoken:
        fan_texts_by_year[china_time(item["ms"]).year].append(item["text"])
    # Sample the background across all lives (never materialize every line) so a
    # member with millions of danmaku stays cheap to report on.
    total_lines = sum(len(replay.lines) for replay in scoped_replays)
    stride = max(1, total_lines // CATCHPHRASE_BG_SAMPLE)
    background_texts = []
    position = 0
    for replay in scoped_replays:
        for _, _, text in replay.lines:
            if position % stride == 0 and not DANMAKU_EVENT_RE.match(text.strip()):
                background_texts.append(text)
            position += 1
    phrases = extract_catchphrases(fan_texts_by_year, background_texts, member_name)
    all_texts = [item["text"] for item in spoken]
    ha_runs = [len(run) for text in all_texts for run in re.findall(r"哈+", text)]

    nickname_stats: dict[str, dict] = {}
    for item in spoken:
        entry = nickname_stats.setdefault(item["nickname"], {"name": item["nickname"], "count": 0,
                                                             "first": item["ms"], "last": item["ms"]})
        entry["count"] += 1
        entry["last"] = item["ms"]
    nickname_list = [
        {"name": entry["name"], "count": entry["count"],
         "first": china_time(entry["first"]).date().isoformat(),
         "last": china_time(entry["last"]).date().isoformat()}
        for entry in sorted(nickname_stats.values(), key=lambda entry: entry["first"])
        if entry["name"]
    ]

    gift_counts: Counter = Counter()
    gifts = {"room_times": 0, "room_items": 0, "live_times": 0, "live_items": 0}
    gift_events = []
    for message in room_messages:
        gift = room_gift(message)
        if gift and message.room_owner == member_name and in_scope(message.timestamp_ms):
            gifts["room_times"] += 1
            gifts["room_items"] += gift[1]
            gift_events.append((message.timestamp_ms, gift[0], gift[1]))
    for sent_ms, name, count in match_live_gifts(scoped_replays, windows):
        if in_scope(sent_ms):
            gifts["live_times"] += 1
            gifts["live_items"] += count
            gift_events.append((sent_ms, name, count))
    prices = (gift_table or {}).get("prices") or {}
    scores = (gift_table or {}).get("scores") or {}
    gift_months = {month: {"month": month, "items": 0, "chicken": 0} for month in months}
    unpriced_items = 0
    score_counts: Counter = Counter()
    score_by_day: Counter = Counter()
    for sent_ms, name, count in gift_events:
        gift_counts[name] += count
        month = gift_months.get(china_time(sent_ms).strftime("%Y-%m"))
        if month is None:
            continue
        month["items"] += count
        if name in prices:
            month["chicken"] += prices[name] * count
        elif name in scores:
            score_counts[name] += count
            score_by_day[china_time(sent_ms).date().isoformat()] += scores[name] * count
        else:
            unpriced_items += count
    # Score gifts get their own card, so the list ranks the 鸡腿 gifts by what they cost.
    regular = [
        {"name": name, "count": count, "chicken": prices[name] * count if name in prices else None}
        for name, count in gift_counts.items() if name not in scores
    ]
    gifts["top"] = sorted(regular, key=lambda gift: (-(gift["chicken"] or 0), -gift["count"], gift["name"]))[:6]
    gifts["monthly"] = list(gift_months.values())
    gifts["chicken_total"] = sum(month["chicken"] for month in gift_months.values()) if prices else None
    gifts["score_items"] = sorted(
        ({"name": name, "count": count, "score": round(scores[name] * count, 1)} for name, count in score_counts.items()),
        key=lambda item: -item["score"],
    )
    gifts["score_total"] = round(sum(item["score"] for item in gifts["score_items"]), 1)
    peak_day = max(score_by_day, key=lambda day: (score_by_day[day], day), default=None)
    gifts["score_peak_day"] = (
        {"date": peak_day, "score": round(score_by_day[peak_day], 1)} if peak_day else None
    )
    gifts["unpriced_items"] = unpriced_items if prices else None

    attended_counts = [len(lines) for lines in matched.values()]
    total_lives = len(scoped_replays)
    display_name = nickname_list[-1]["name"] if nickname_list else (
        sorted(archive_nicknames, key=lambda item: item[1])[-1][0] if archive_nicknames else "")
    return {
        "version": REPORT_VERSION,
        "member_name": member_name,
        "scope": scope,
        "years": list(years),
        "data_end": scope_end.isoformat(),
        "display_name": display_name,
        "totals": {
            "room": sum(1 for item in spoken if item["kind"] == "room"),
            "danmaku": sum(1 for item in spoken if item["kind"] == "danmaku"),
            "lives": total_lives,
            "attended": len(matched),
            "active_days": len(active_days),
            "total_days": (scope_end - scope_start).days + 1,
        },
        "monthly": list(monthly.values()),
        "delay": delay_summary,
        "lines_per_live": {
            "mean": round(statistics.mean(attended_counts), 1) if attended_counts else 0,
            "max": max(attended_counts, default=0),
        },
        "top_lives": top_lives,
        "daily": dict(sorted(daily.items())),
        "longest_streak": longest,
        "hours": hours,
        "phrases": phrases,
        "ha": {"count": sum(text.count("哈") for text in all_texts), "longest": max(ha_runs, default=0)},
        "nicknames": nickname_list,
        "gifts": gifts,
    }


def parse_lrc_lines(text: str, parse_timestamp) -> list[tuple[int, str, str]]:
    """Keep the raw source offset; summaries need real send times, not the
    3-second display advance the player applies."""
    lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip().lstrip("﻿")
        if not stripped.startswith("["):
            continue
        closing = stripped.find("]")
        if closing < 0:
            continue
        offset_ms = parse_timestamp(stripped[1:closing])
        if offset_ms is None:
            continue
        author, separator, comment = stripped[closing + 1:].partition("\t")
        if not separator or not comment.strip():
            continue
        lines.append((offset_ms, author.strip(), comment.strip()))
    return lines


def scope_start_ms(scope: str) -> int:
    first_year = SCOPES[scope][0]
    return int(datetime(first_year, 1, 1, tzinfo=CHINA).timestamp() * 1000)


def china_today() -> date:
    return datetime.now(CHINA).date()
