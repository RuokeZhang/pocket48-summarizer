import base64
import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from pocket48_summarizer.annual_report import (
    AnnualReportRepository,
    AnnualReportWorker,
    ReportMember,
    shrink_avatar,
)
from pocket48_summarizer.annual_report_stats import (
    REPORT_VERSION,
    Replay,
    RoomMessage,
    build_alias_windows,
    build_report,
    match_danmaku,
    parse_lrc_lines,
)
from pocket48_summarizer.app import create_app
from pocket48_summarizer.auth import AuthRepository, hash_password
from pocket48_summarizer.clients.msg48 import ArchiveMessage, ArchiveNickname, Msg48Client
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.models import ReplayMetadata
from pocket48_summarizer.parsing.lrc import parse_lrc_timestamp
from pocket48_summarizer.services import ApplicationServices

CHINA = ZoneInfo("Asia/Shanghai")
MEMBER = ReportMember("407126", "杨晔", "SNH48", "TEAM X", True, "YangYe", "社长 二毛 日华",
                      "https://www.snh48.com/images/member/zp_10247.jpg")


def png_bytes(width=100, height=60):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (width, height), (200, 80, 120)).save(output, format="PNG")
    return output.getvalue()


def ms(year, month, day, hour=20, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=CHINA).timestamp() * 1000)


def room(timestamp_ms, text, nickname="成早Ruoke", owner="杨晔", msg_type="TEXT"):
    return RoomMessage(timestamp_ms, owner, nickname, msg_type, text)


def test_old_nickname_only_matches_while_it_was_in_use():
    room_messages = [
        room(ms(2025, 1, 5), "早"),
        room(ms(2025, 3, 1), "晚安", nickname="花果山大当家Ruoke"),
        room(ms(2026, 2, 16), "新名字", nickname="Monkey King"),
        room(ms(2026, 6, 1), "回来了", nickname="花果山大当家RUOKE"),
    ]
    windows = build_alias_windows([], room_messages, ["成早117"])
    replay = Replay("1", ms(2026, 2, 16, 19), "直播", [
        (60_000, "Monkey King", "在的"),
        (120_000, "花果山大当家ruoke", "大小写不同也算"),
    ])
    later = Replay("2", ms(2026, 5, 1, 19), "直播", [
        (60_000, "Monkey King", "这是别人"),
        (90_000, "成早117", "手填的昵称一直有效"),
        (120_000, "成早Ruoke", "旧昵称过期了"),
    ])
    matched = match_danmaku([replay, later], windows)
    assert [line[2] for line in matched["1"]] == ["在的", "大小写不同也算"]
    assert [line[2] for line in matched["2"]] == ["手填的昵称一直有效"]


def test_gift_and_system_lines_are_not_counted():
    windows = build_alias_windows([("小明", ms(2025, 1, 1))], [], [])
    replay = Replay("1", ms(2025, 5, 1), "直播", [
        (1_000, "小明", "送给SNH48-杨晔 1个心锁"),
        (2_000, "小明", "进入直播间"),
        (3_000, "小明", "今天好看"),
    ])
    assert [line[2] for line in match_danmaku([replay], windows)["1"]] == ["今天好看"]


def test_parse_lrc_lines_keeps_raw_send_offset():
    lines = parse_lrc_lines("[00:01:02.500]小明\t你好\n[02:03]小红\t晚上好\n无效行", parse_lrc_timestamp)
    assert lines == [(62_500, "小明", "你好"), (123_000, "小红", "晚上好")]


def build_sample_report(scope="2025-2026"):
    room_messages = [
        room(ms(2025, 1, 1, 9), "第一句话哈哈哈"),
        room(ms(2025, 1, 2), "辛苦啦姐"),
        room(ms(2025, 1, 3), "好看"),
        room(ms(2025, 6, 1), "去串门", owner="禹佳蔚"),
        room(ms(2025, 6, 2), "再串一次", owner="禹佳蔚"),
        room(ms(2025, 6, 3), "第三次", owner="禹佳蔚"),
        room(ms(2025, 6, 4), "只来一次", owner="闫娜"),
        room(ms(2026, 3, 1), json.dumps({"replyInfo": {"text": "回复她", "replyName": "SNH48-杨晔", "replyText": "x"}}), msg_type="REPLY"),
        room(ms(2026, 3, 2), "{}", msg_type="GIFT_TEXT"),
        room(ms(2026, 4, 1, 23), "最近一句", nickname="花果山大当家Ruoke"),
    ]
    replays = [
        Replay("a", ms(2025, 1, 2, 20), "第一场", [
            (90_000, "成早Ruoke", "我来啦大家好"), (200_000, "成早Ruoke", "今天的妆好好看呀"),
            (300_000, "成早Ruoke", "哈哈哈哈哈"), (400_000, "路人", "无关")]),
        Replay("b", ms(2025, 1, 20, 20), "没去的一场", [(60_000, "路人", "无关")]),
        Replay("c", ms(2026, 4, 1, 20), "第二场", [(30_000, "花果山大当家Ruoke", "晚上好呀大家")]),
    ]
    return build_report(
        member_name="杨晔", scope=scope, data_end=date(2026, 4, 30),
        archive_nicknames=[("成早Ruoke", ms(2024, 10, 18)), ("花果山大当家Ruoke", ms(2025, 7, 27))],
        room_messages=room_messages, replays=replays, extra_nicknames=[],
    )


def test_build_report_counts_spoken_messages_and_attendance():
    report = build_sample_report()
    assert report["totals"] == {
        "room": 5, "danmaku": 4, "lives": 3, "attended": 2,
        "active_days": 5, "total_days": 485,
    }
    january = report["monthly"][0]
    assert january == {"month": "2025-01", "room": 3, "danmaku": 3, "lives": 2, "attended": 1}
    assert report["monthly"][-1]["month"] == "2026-04"
    assert report["delay"]["2025"]["median_minutes"] == 1.5
    assert report["delay"]["2026"]["buckets"][0] == {"label": "2分钟内", "count": 1}
    assert report["top_lives"][0]["title"] == "第一场"
    assert report["top_lives"][0]["quotes"] == ["我来啦大家好", "今天的妆好好看呀"]
    assert "other_rooms" not in report
    assert "bookends" not in report
    assert report["display_name"] == "花果山大当家Ruoke"
    assert report["ha"] == {"count": 8, "longest": 5}
    assert report["phrases"]["2025"]["好看"] == 2


def test_build_report_can_cover_only_2026():
    report = build_sample_report(scope="2026")
    assert report["years"] == [2026]
    assert report["totals"]["room"] == 2
    assert report["totals"]["lives"] == 1
    assert report["monthly"][0]["month"] == "2026-01"
    assert list(report["phrases"]) == ["2026"]


@pytest.fixture
def reports(settings, repository):
    store = AnnualReportRepository(repository.database, settings)
    store.replace_member_index([MEMBER])
    return store


def test_repository_reuses_jobs_and_limits_requests(settings, reports):
    first = reports.enqueue(uid="367554", member=MEMBER, scope="2026", extra_nicknames=[], requester="1.1.1.1")
    again = reports.enqueue(uid="367554", member=MEMBER, scope="2026", extra_nicknames=[], requester="2.2.2.2")
    assert again.id == first.id
    assert first.queue_position == 1
    for uid in ("1", "2"):
        reports.enqueue(uid=uid, member=MEMBER, scope="2026", extra_nicknames=[], requester="1.1.1.1")
    with pytest.raises(AppError) as error:
        reports.enqueue(uid="3", member=MEMBER, scope="2026", extra_nicknames=[], requester="1.1.1.1")
    assert error.value.code == "report_rate_limited"
    settings.annual_report_queue_limit = 3
    with pytest.raises(AppError) as error:
        reports.enqueue(uid="4", member=MEMBER, scope="2026", extra_nicknames=[], requester="3.3.3.3")
    assert error.value.code == "report_queue_full"


def test_finished_jobs_forget_the_uid(reports):
    job = reports.enqueue(uid="367554", member=MEMBER, scope="2026", extra_nicknames=[], requester="x")
    claimed = reports.claim_next("worker", 60)
    assert claimed.id == job.id and claimed.uid == "367554"
    reports.complete(job.id, {"ok": True})
    finished = reports.get(job.id)
    assert finished.uid == ""
    assert finished.result == {"ok": True}


class FakeMsg48:
    async def user_nicknames(self, uid):
        return [ArchiveNickname(nickname="小明", firstUsed=ms(2025, 1, 1))]

    async def user_messages(self, uid):
        return [ArchiveMessage(user_id=int(uid), nickname="小明", timestamp=ms(2026, 1, 2), msg_type="TEXT",
                               content="你好呀", avatar="/avatar/2026/0101/me.jpg",
                               room_info={"ownerName": "杨晔"})], False

    async def fetch_avatar(self, url):
        return png_bytes()

    async def gift_price_table(self):
        return {"prices": {"荧光棒": 5}, "scores": {}}

    async def member_index(self):
        return []

    async def close(self):
        return None


class FakePocket48:
    def __init__(self):
        self.danmaku_fetches = 0

    async def list_member_replays(self, member_id, since_ms):
        class Entry:
            def __init__(self, live_id, started):
                self.live_id, self.started_at_ms = live_id, started
        return [Entry("10", ms(2026, 1, 2, 20)), Entry("11", ms(2026, 1, 3, 20))]

    async def resolve_replay_danmaku(self, live_id):
        if live_id == "11":
            raise AppError("replay_not_ready", "not ready", True)
        return ReplayMetadata(live_id=live_id, member_id="407126", member_name="杨晔", title="开播啦",
                              media_url="https://example.invalid/a.m3u8",
                              danmaku_url="https://source.48.cn/a.lrc")

    async def fetch_danmaku(self, url):
        self.danmaku_fetches += 1
        return "[00:00:30]小明\t来了\n[00:01:00]路人\t你好"


async def test_worker_pipeline_generates_and_caches_danmaku(settings, reports):
    pocket48 = FakePocket48()
    worker = AnnualReportWorker(settings, reports, pocket48, FakeMsg48())
    job = reports.enqueue(uid="367554", member=MEMBER, scope="2026", extra_nicknames=[], requester="x")
    await worker._process(reports.claim_next(worker.worker_id, 60))
    result = reports.get(job.id).result
    assert result["totals"]["room"] == 1
    assert result["totals"]["danmaku"] == 1
    assert result["totals"]["attended"] == 1
    assert result["top_lives"][0]["title"] == "开播啦"
    assert result["fan_avatar"].startswith("data:image/jpeg;base64,")
    assert result["member_avatar"].startswith("data:image/jpeg;base64,")
    second = reports.enqueue(uid="1", member=MEMBER, scope="2026", extra_nicknames=[], requester="y")
    await worker._process(reports.claim_next(worker.worker_id, 60))
    assert reports.get(second.id).status == "completed"
    assert pocket48.danmaku_fetches == 1


def test_report_page_and_api(settings, repository, reports):
    app = create_app(settings, ApplicationServices(repository=repository))
    with TestClient(app) as client:
        page = client.get("/report")
        assert page.status_code == 200
        assert "杨晔 · SNH48 TEAM X" in page.text
        assert 'data-search="杨晔 yangye 社长 二毛 日华 snh48 team x"' in page.text
        assert 'id="report-member-search"' in page.text
        assert 'id="report-sample"' in page.text
        assert 'id="report-history"' in page.text
        sample = client.get("/static/report-sample.json").json()
        assert sample["version"] == REPORT_VERSION and sample["totals"]["danmaku"] > 0
        assert 'href="/report"' in client.get("/").text
        unknown = client.post("/api/reports", json={"uid": "367554", "member_pocket_id": "1", "scope": "2026"})
        assert unknown.status_code == 400
        assert client.post("/api/reports", json={"uid": "abc", "member_pocket_id": "407126"}).status_code == 422
        created = client.post("/api/reports", json={"uid": "367554", "member_pocket_id": "407126", "scope": "2026",
                                                    "extra_nicknames": [" 成早117 ", ""]})
        assert created.status_code == 202
        body = created.json()
        assert body["status"] == "queued" and body["result"] is None
        status = client.get(f"/api/reports/{body['id']}")
        assert status.json()["queue_position"] == 1
        assert client.get("/api/reports/not-a-job").status_code == 404


class FailingIndexMsg48(FakeMsg48):
    def __init__(self):
        self.index_calls = 0

    async def member_index(self):
        self.index_calls += 1
        raise AppError("archive_request_failed", "blocked", True)


async def test_member_list_falls_back_to_snapshot_and_retries_soon(settings, repository, monkeypatch):
    reports = AnnualReportRepository(repository.database, settings)
    msg48 = FailingIndexMsg48()
    worker = AnnualReportWorker(settings, reports, FakePocket48(), msg48)
    await worker.start()
    await worker.stop()
    assert reports.get_member("407126").name == "杨晔"
    assert len(reports.list_members()) > 500

    await worker._refresh_member_index_if_due()
    await worker._refresh_member_index_if_due()
    assert msg48.index_calls == 1
    import pocket48_summarizer.annual_report as annual_report
    monkeypatch.setattr(annual_report, "MEMBER_INDEX_RETRY", annual_report.timedelta(0))
    await worker._refresh_member_index_if_due()
    assert msg48.index_calls == 2


def test_admins_are_not_rate_limited(settings, repository, reports):
    configured = settings.model_copy(update={"auth_required": True, "session_cookie_secure": False})
    AuthRepository(repository.database).create_user(
        "boss", "boss", hash_password("boss has a secure password"), is_admin=True
    )
    app = create_app(configured, ApplicationServices(repository=repository))
    body = {"member_pocket_id": "407126", "scope": "2026"}
    with TestClient(app) as visitor:
        codes = [visitor.post("/api/reports", json={**body, "uid": str(uid)}).status_code for uid in range(1, 5)]
        assert codes == [202, 202, 202, 429]
    with TestClient(app) as admin:
        admin.post("/login", data={"username": "boss", "password": "boss has a secure password"},
                   follow_redirects=False)
        assert admin.post("/api/reports", json={**body, "uid": "10"}).status_code == 403
        token = admin.cookies.get(configured.csrf_cookie_name)
        codes = [
            admin.post("/api/reports", json={**body, "uid": str(uid)}, headers={"X-CSRF-Token": token}).status_code
            for uid in range(20, 25)
        ]
        assert codes == [202] * 5


async def test_member_list_without_search_fields_is_refreshed_from_snapshot(settings, repository):
    reports = AnnualReportRepository(repository.database, settings)
    reports.replace_member_index([ReportMember("407126", "杨晔", "SNH48", "TEAM X", True)])
    worker = AnnualReportWorker(settings, reports, FakePocket48(), FailingIndexMsg48())
    await worker.start()
    await worker.stop()
    yang_ye = reports.get_member("407126")
    assert yang_ye.pinyin == "YangYe"
    assert "二毛" in yang_ye.aliases


def test_avatars_are_cropped_to_sharp_squares():
    from PIL import Image
    data_url = shrink_avatar(png_bytes(600, 400))
    assert data_url.startswith("data:image/jpeg;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert image.size == (240, 240)


async def test_avatar_download_only_allows_official_hosts(settings):
    client = Msg48Client(settings)
    with pytest.raises(AppError) as error:
        await client.fetch_avatar("https://evil.example/avatar.png")
    assert error.value.code == "avatar_host_not_allowed"
    await client.close()


def test_public_navigation_hides_glossary(settings, repository):
    app = create_app(settings.model_copy(update={"auth_required": True}), ApplicationServices(repository=repository))
    with TestClient(app) as client:
        home = client.get("/")
    assert 'href="/" data-i18n="summaryNav"' in home.text
    assert 'href="/glossary"' not in home.text


def test_other_rooms_do_not_count_towards_the_chosen_member():
    room_messages = [
        room(ms(2025, 2, 1), "在杨晔房间说话"),
        room(ms(2025, 2, 2), "也是杨晔房间"),
        room(ms(2025, 3, 1), "刘力菲房间的一句", owner="刘力菲"),
    ]
    report = build_report(
        member_name="刘力菲", scope="2025-2026", data_end=date(2026, 11, 26),
        archive_nicknames=[], room_messages=room_messages, replays=[], extra_nicknames=[],
    )
    assert report["totals"]["room"] == 1
    assert report["totals"]["active_days"] == 1
    assert sum(month["room"] for month in report["monthly"] if month["month"].startswith("2026")) == 0


def test_gifts_count_live_and_room_gifts_for_the_chosen_member_only():
    gift = lambda name, count: json.dumps({"giftInfo": {"giftName": name, "giftNum": count}})
    room_messages = [
        room(ms(2025, 3, 1), "晚上好"),
        room(ms(2025, 3, 2), gift("高级马卡龙", 2), msg_type="GIFT_TEXT"),
        room(ms(2025, 3, 3), gift("玫瑰花", 5), msg_type="GIFT_TEXT", owner="刘力菲"),
    ]
    replays = [Replay("a", ms(2025, 3, 5), "直播", [
        (1_000, "成早Ruoke", "送给SNH48-杨晔 99个荧光棒-X"),
        (2_000, "成早Ruoke", "送给SNH48-杨晔 1个荧光棒-SII"),
        (3_000, "路人", "送给SNH48-杨晔 520个心锁"),
        (4_000, "成早Ruoke", "今天好看"),
    ])]
    report = build_report(
        member_name="杨晔", scope="2025-2026", data_end=date(2026, 11, 26),
        archive_nicknames=[], room_messages=room_messages, replays=replays, extra_nicknames=[],
    )
    gifts = report["gifts"]
    assert {key: gifts[key] for key in ("room_times", "room_items", "live_times", "live_items", "top")} == {
        "room_times": 1, "room_items": 2, "live_times": 2, "live_items": 100,
        "top": [{"name": "荧光棒", "count": 100, "chicken": None}, {"name": "高级马卡龙", "count": 2, "chicken": None}],
    }
    march = next(month for month in gifts["monthly"] if month["month"] == "2025-03")
    assert march["items"] == 102
    assert report["totals"]["danmaku"] == 1


def test_share_link_shows_only_the_poster(settings, repository, reports):
    app = create_app(settings, ApplicationServices(repository=repository))
    with TestClient(app) as client:
        page = client.get("/report?job=" + "a" * 32)
    assert 'data-share="true"' in page.text
    assert 'id="report-poster-wrap"' in page.text
    assert 'id="report-download"' not in page.text
    assert 'id="report-form"' not in page.text
    assert 'id="report-sample"' not in page.text
    assert 'id="report-history"' not in page.text


def test_gift_chicken_legs_use_the_price_table():
    gift = json.dumps({"giftInfo": {"giftName": "高级马卡龙", "giftNum": 2}})
    report = build_report(
        member_name="杨晔", scope="2025-2026", data_end=date(2026, 11, 26), archive_nicknames=[],
        room_messages=[room(ms(2025, 4, 1), gift, msg_type="GIFT_TEXT"),
                       room(ms(2025, 4, 2), json.dumps({"giftInfo": {"giftName": "神秘礼物", "giftNum": 3}}), msg_type="GIFT_TEXT"),
                       room(ms(2025, 4, 3), json.dumps({"giftInfo": {"giftName": "命定水晶", "giftNum": 2}}), msg_type="GIFT_TEXT")],
        replays=[Replay("a", ms(2025, 5, 1), "直播", [(1_000, "成早Ruoke", "送给SNH48-杨晔 99个荧光棒-X")])],
        extra_nicknames=["成早Ruoke"],
        gift_table={"prices": {"荧光棒": 5, "高级马卡龙": 1048}, "scores": {"命定水晶": 9.0}},
    )
    gifts = report["gifts"]
    assert gifts["chicken_total"] == 2 * 1048 + 99 * 5
    assert gifts["score_total"] == 18
    assert gifts["score_items"] == [{"name": "命定水晶", "count": 2, "score": 18.0}]
    assert gifts["score_peak_day"] == {"date": "2025-04-03", "score": 18.0}
    assert [gift["name"] for gift in gifts["top"]] == ["高级马卡龙", "荧光棒", "神秘礼物"]
    assert gifts["unpriced_items"] == 3
    assert next(m for m in gifts["monthly"] if m["month"] == "2025-04")["chicken"] == 2096


def test_gift_price_table_drops_ambiguous_names_and_keeps_score_gifts_apart():
    from pocket48_summarizer.clients.msg48 import build_gift_price_table
    table = build_gift_price_table({
        "prices": [{"name": "荧光棒-X", "cost": 5}, {"name": "荧光棒-SII", "cost": 5},
                   {"name": "圣诞树", "cost": 48}, {"name": "圣诞树", "cost": 1200},
                   {"name": "高级马卡龙", "cost": 1048}],
        "rules": [{"name": "命定水晶", "scorePerUnit": "9"}, {"name": "娃娃机", "historicalCost": 1200}],
    })
    assert table == {"prices": {"荧光棒": 5, "高级马卡龙": 1048}, "scores": {"命定水晶": 9.0}}