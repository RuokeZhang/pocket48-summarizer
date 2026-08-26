import pytest
from fastapi.testclient import TestClient

from pocket48_summarizer.app import create_app
from pocket48_summarizer.auth import AuthRepository, hash_password
from pocket48_summarizer.media.boundaries import BoundarySuggestion
from pocket48_summarizer.media.clips import ClipState
from pocket48_summarizer.models import (
    ClipRange,
    DanmakuEntry,
    DanmakuPeak,
    DanmakuPeakSummary,
    FinalSummary,
    MemberCatalogEntry,
    ReplayMetadata,
    TimelineItem,
    TranscriptSegment,
)
from pocket48_summarizer.routes import (
    CreateClipExportRequest,
    format_china_datetime,
)
from pocket48_summarizer.services import ApplicationServices


class DummyWorker:
    def __init__(self):
        self.notified = 0

    async def start(self):
        return None

    async def stop(self):
        return None

    def notify(self):
        self.notified += 1


def test_clip_export_request_uses_vibrant_calm_defaults():
    payload = CreateClipExportRequest(
        request_id="request-default-style",
        timeline_index=0,
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="zh",
    )

    assert payload.subtitle_font_scale == 100
    assert payload.subtitle_text_color == "#E43D12"
    assert payload.subtitle_background_color == "#EBE9E1"
    assert payload.output_layout == "portrait"
    assert payload.subtitle_font_family == "wenkai"
    assert payload.kept_ranges == [
        ClipRange(start_ms=1000, end_ms=5000)
    ]

    landscape = CreateClipExportRequest(
        request_id="request-landscape-style",
        timeline_index=0,
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="zh",
        subtitle_text_color="#FFFFFF",
        subtitle_background_color="#FFFFFF",
        output_layout="landscape",
    )
    assert landscape.output_layout == "landscape"

    with pytest.raises(ValueError, match="kept range"):
        CreateClipExportRequest(
            request_id="request-cover-deleted",
            timeline_index=0,
            start_ms=1000,
            end_ms=9000,
            kept_ranges=[
                ClipRange(start_ms=1000, end_ms=3000),
                ClipRange(start_ms=6000, end_ms=9000),
            ],
            subtitle_mode="off",
            cover_enabled=True,
            cover_timestamp_ms=4500,
            cover_title="已删除画面",
        )


class DummyClipper:
    def __init__(self, output_path, repository=None):
        self.state = ClipState("completed", output_path)
        self.started_with = None
        self.started_export_with = None
        self.started_exports = []
        self.repository = repository

    def start(self, **kwargs):
        self.started_with = kwargs
        return self.state

    def get(self, **kwargs):
        return self.state

    def start_export(self, **kwargs):
        self.started_export_with = kwargs
        self.started_exports.append(kwargs)
        record, _ = self.repository.begin_video_clip_export(
            clip_id=f"clip-export-{len(self.started_exports)}",
            job_id=kwargs["job_id"],
            timeline_index=kwargs["timeline_index"],
            timeline_title=kwargs["timeline_title"],
            requested_by_user_id=kwargs["requested_by_user_id"],
            request_id=kwargs["request_id"],
            start_ms=kwargs["start_ms"],
            end_ms=kwargs["end_ms"],
            kept_ranges=kwargs["kept_ranges"],
            subtitle_mode=kwargs["subtitle_mode"],
            include_danmaku=kwargs["include_danmaku"],
            subtitle_font_scale=kwargs["subtitle_font_scale"],
            subtitle_text_color=kwargs["subtitle_text_color"],
            subtitle_background_color=(
                kwargs["subtitle_background_color"]
            ),
            output_layout=kwargs["output_layout"],
            subtitle_font_family=kwargs["subtitle_font_family"],
            cover_enabled=kwargs["cover_enabled"],
            cover_timestamp_ms=kwargs["cover_timestamp_ms"],
            cover_title=kwargs["cover_title"],
            cover_style=kwargs["cover_style"],
            render_version="ass-v2",
            filename=self.state.output_path.name,
        )
        self.repository.complete_video_clip_export(
            record.id, "clips/job/clip-export-1.mp4"
        )
        return self.repository.get_video_clip_export(
            kwargs["job_id"], record.id
        )

    def retry_export(self, **kwargs):
        record = self.repository.retry_video_clip_export(
            kwargs["job_id"], kwargs["clip_id"]
        )
        self.repository.complete_video_clip_export(
            record.id, "clips/job/clip-export-1.mp4"
        )
        return self.repository.get_video_clip_export(
            kwargs["job_id"], record.id
        )

    async def suggest_boundary(self, **kwargs):
        return BoundarySuggestion(
            boundary=kwargs["boundary"],
            requested_ms=kwargs["target_ms"],
            sentence_sequence=1,
            sentence_ms=30_000,
            suggested_ms=29_850,
            source="silence",
            silence_start_ms=29_400,
            silence_end_ms=29_850,
        )

    def output_path_for(self, record):
        return self.state.output_path

    async def startup(self):
        return None

    async def signed_download_url(self, state):
        return f"https://oss.example/{state.oss_object_key}?signed=1"

    async def close(self):
        return None


class DummyMemberCatalog:
    def __init__(self):
        self.force = None

    async def sync_if_due(self, *, force=False):
        self.force = force
        return None


def test_formats_replay_time_in_china_timezone():
    assert (
        format_china_datetime("2026-08-22T10:57:06+00:00")
        == "2026-08-22 18:57"
    )


def test_create_and_view_job(settings, repository):
    worker = DummyWorker()
    app = create_app(
        settings,
        ApplicationServices(repository=repository, worker=worker),
    )
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["release"] == "development"
        response = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=1297967327104274432"
                )
            },
        )
        assert response.status_code == 201
        job_id = response.json()["id"]
        assert worker.notified == 1
        assert client.get(f"/api/jobs/{job_id}/status").status_code == 200
        page = client.get(f"/jobs/{job_id}")
        assert page.status_code == 200
        assert "1297967327104274432" in page.text


def test_web_without_embedded_worker_can_queue_job(settings, repository):
    app = create_app(
        settings,
        ApplicationServices(repository=repository),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=1297967327104274433"
                )
            },
        )

    assert response.status_code == 201
    assert repository.get_job(response.json()["id"]).status.value == "queued"


def test_rejects_ssrf_input(settings, repository):
    app = create_app(
        settings,
        ApplicationServices(repository=repository, worker=DummyWorker()),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn.evil.test/"
                    "2019appshare/memberLiveShare/index.html?id=123456"
                )
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_share_url"


def test_job_page_shows_peak_summary_and_clickable_author(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=223344",
        "223344",
    )
    repository.replace_danmaku(
        job.id,
        [
            DanmakuEntry(
                sequence=1,
                timestamp_ms=1000,
                author="fan-42",
                text="好开心",
            )
        ],
    )
    repository.replace_danmaku_peaks(
        job.id,
        [
            DanmakuPeak(
                rank=1,
                start_ms=0,
                end_ms=30_000,
                message_count=1,
                score=4,
            )
        ],
    )
    summary = FinalSummary(
        overview="测试",
        timeline=[],
        topics=[],
        highlights=[],
        danmaku_peak_summaries=[
            DanmakuPeakSummary(
                start_ms=0,
                end_ms=30_000,
                summary="主播讲述近况，弹幕样本显示观众很开心。",
            )
        ],
    )
    repository.save_summary(job.id, summary.model_dump_json(), "# 测试")
    app = create_app(
        settings,
        ApplicationServices(repository=repository, worker=DummyWorker()),
    )

    with TestClient(app) as client:
        page = client.get(f"/jobs/{job.id}")

    assert page.status_code == 200
    assert "主播讲述近况，弹幕样本显示观众很开心。" in page.text
    assert 'class="danmaku-author"' in page.text
    assert 'data-danmaku-author="fan-42"' in page.text


def test_rejects_untrusted_host_header(settings, repository):
    app = create_app(
        settings,
        ApplicationServices(repository=repository, worker=DummyWorker()),
    )
    with TestClient(app) as client:
        response = client.get("/", headers={"Host": "evil.example"})
        assert response.status_code == 400


def test_timeline_clip_can_be_created_and_downloaded(
    settings, repository, tmp_path
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=556677",
        "556677",
    )
    repository.set_media_details(
        job.id,
        "https://idol-vod.48.cn/path/replay.m3u8",
        600_000,
    )
    summary = FinalSummary(
        overview="测试",
        timeline=[
            TimelineItem(
                start_ms=61_250,
                end_ms=125_750,
                title="测试话题",
                detail="测试详情",
                evidence_segment_ids=[1],
            )
        ],
        topics=[],
        highlights=[],
    )
    repository.save_summary(job.id, summary.model_dump_json(), "# 测试")
    output_path = tmp_path / "timeline-01-61-125.mp4"
    output_path.write_bytes(b"fake mp4")
    clipper = DummyClipper(output_path)
    app = create_app(
        settings,
        ApplicationServices(repository=repository, clipper=clipper),
    )

    with TestClient(app) as client:
        page = client.get(f"/jobs/{job.id}")
        settings.clip_maintenance_path.touch()
        blocked = client.post(f"/api/jobs/{job.id}/clips/0")
        settings.clip_maintenance_path.unlink()
        response = client.post(f"/api/jobs/{job.id}/clips/0")
        download = client.get(f"/jobs/{job.id}/clips/0/download")
        clipper.state.oss_object_key = "clips/job/timeline.mp4"
        output_path.unlink()
        redirect = client.get(
            f"/jobs/{job.id}/clips/0/download",
            follow_redirects=False,
        )

    assert 'class="clip-button"' in page.text
    assert 'id="clip-editor"' in page.text
    assert 'data-duration-ms="600000"' in page.text
    assert 'data-clip-start-ms="61250"' in page.text
    assert 'data-clip-end-ms="125750"' in page.text
    assert 'id="clip-timeline-viewport"' in page.text
    assert 'id="clip-transcript-cues"' in page.text
    assert 'id="clip-start-handle"' in page.text
    assert 'id="clip-end-handle"' in page.text
    assert 'id="clip-zoom-out"' in page.text
    assert 'id="clip-zoom-in"' in page.text
    assert 'id="clip-start-range"' not in page.text
    assert 'id="clip-end-range"' not in page.text
    assert 'id="clip-start-input"' not in page.text
    assert 'id="clip-end-input"' not in page.text
    assert 'id="clip-subtitle-mode"' in page.text
    assert 'id="clip-danmaku-enabled"' in page.text
    assert 'id="clip-preview-player"' in page.text
    assert 'id="clip-lyric-preview"' in page.text
    assert 'id="clip-lyric-previous-2"' in page.text
    assert 'id="clip-lyric-next-2"' in page.text
    assert 'id="clip-hover-marker"' in page.text
    assert 'id="clip-marked-marker"' in page.text
    assert 'id="clip-segment-track"' in page.text
    assert 'id="clip-split-at-marker"' in page.text
    assert 'id="clip-toggle-segment"' in page.text
    assert 'id="clip-segment-list"' in page.text
    assert 'data-i18n="clipOutputTrack"' in page.text
    assert 'id="clip-preview-cut-notice"' in page.text
    assert 'id="clip-marker-time"' not in page.text
    assert 'id="clip-subtitle-font-scale"' in page.text
    assert 'value="100"' in page.text
    assert 'id="clip-subtitle-font-scale-value">100%</output>' in page.text
    assert 'id="clip-cover-panel"' in page.text
    assert 'id="clip-cover-use-frame"' in page.text
    assert 'id="clip-cover-title-input"' in page.text
    assert 'name="clip-cover-style"' in page.text
    assert 'id="clip-subtitle-font-family"' in page.text
    assert 'id="clip-subtitle-text-color"' in page.text
    assert 'id="clip-subtitle-background-color"' in page.text
    assert 'id="clip-theme-vibrant-calm"' in page.text
    assert 'id="clip-output-layout"' in page.text
    assert 'value="landscape"' in page.text
    assert 'id="clip-landscape-style-note"' in page.text
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "clipper_maintenance"
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["download_url"].endswith("/clips/0/download")
    assert clipper.started_with["start_ms"] == 61_250
    assert clipper.started_with["end_ms"] == 125_750
    assert download.status_code == 200
    assert download.content == b"fake mp4"
    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("https://oss.example/")


def test_configurable_clip_export_routes_preserve_versions(
    settings, repository, tmp_path
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=556678",
        "556678",
    )
    repository.set_media_details(
        job.id,
        "https://idol-vod.48.cn/path/replay.m3u8",
        1_200_000,
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=30_000,
                end_ms=60_000,
                text="测试字幕",
            )
        ],
    )
    repository.save_summary(
        job.id,
        FinalSummary(
            overview="测试",
            timeline=[
                TimelineItem(
                    start_ms=30_000,
                    end_ms=60_000,
                    title="可调剪辑",
                    detail="测试详情",
                    evidence_segment_ids=[1],
                )
            ],
            topics=[],
            highlights=[],
        ).model_dump_json(),
        "# 测试",
    )
    repository.claim_next_job("worker", 120)
    repository.mark_completed(job.id)
    output_path = tmp_path / "clip-export-1.mp4"
    output_path.write_bytes(b"configurable clip")
    clipper = DummyClipper(output_path, repository)
    app = auth_app(settings, repository, clipper=clipper)

    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        suggestion = alice.post(
            f"/api/jobs/{job.id}/clip-boundaries/suggest",
            json={
                "timeline_index": 0,
                "boundary": "start",
                "target_ms": 30_400,
            },
            headers=csrf_headers(alice),
        )
        invalid_cover = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-cover-invalid",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 60_000,
                "subtitle_mode": "off",
                "cover_enabled": True,
                "cover_timestamp_ms": 70_000,
                "cover_title": "超出范围",
            },
            headers=csrf_headers(alice),
        )
        created = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-clip-1",
                "timeline_index": 0,
                "start_ms": 29_850,
                "end_ms": 60_500,
                "subtitle_mode": "zh",
                "include_danmaku": False,
                "subtitle_font_scale": 125,
                "subtitle_text_color": "#E43D12",
                "subtitle_background_color": "#EBE9E1",
                "output_layout": "landscape",
                "subtitle_font_family": "serif",
                "cover_enabled": True,
                "cover_timestamp_ms": 45_000,
                "cover_title": "灯光亮起时",
                "cover_style": "badge",
            },
            headers=csrf_headers(alice),
        )
        repeated = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-clip-1",
                "timeline_index": 0,
                "start_ms": 29_850,
                "end_ms": 60_500,
                "subtitle_mode": "zh",
                "include_danmaku": False,
                "subtitle_font_scale": 125,
                "subtitle_text_color": "#E43D12",
                "subtitle_background_color": "#EBE9E1",
                "output_layout": "landscape",
                "subtitle_font_family": "serif",
                "cover_enabled": True,
                "cover_timestamp_ms": 45_000,
                "cover_title": "灯光亮起时",
                "cover_style": "badge",
            },
            headers=csrf_headers(alice),
        )
        low_contrast = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-low-contrast",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 60_000,
                "subtitle_mode": "zh",
                "include_danmaku": False,
                "subtitle_text_color": "#FFFFFF",
                "subtitle_background_color": "#FFFFFF",
            },
            headers=csrf_headers(alice),
        )
        english_blocked = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-clip-en",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 60_000,
                "subtitle_mode": "en",
                "include_danmaku": False,
            },
            headers=csrf_headers(alice),
        )
        cut_created = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-cut-clip",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 60_000,
                "kept_ranges": [
                    {"start_ms": 30_000, "end_ms": 40_000},
                    {"start_ms": 50_000, "end_ms": 60_000},
                ],
                "subtitle_mode": "zh",
            },
            headers=csrf_headers(alice),
        )
        wide_cut_created = alice.post(
            f"/api/jobs/{job.id}/clip-exports",
            json={
                "request_id": "request-wide-cut-clip",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 660_000,
                "kept_ranges": [
                    {"start_ms": 30_000, "end_ms": 40_000},
                    {"start_ms": 650_000, "end_ms": 660_000},
                ],
                "subtitle_mode": "off",
            },
            headers=csrf_headers(alice),
        )

    newer, _ = repository.begin_video_clip_export(
        clip_id="clip-export-failed",
        job_id=job.id,
        timeline_index=0,
        timeline_title="可调剪辑",
        requested_by_user_id=None,
        request_id="request-clip-failed",
        start_ms=31_000,
        end_ms=59_000,
        subtitle_mode="off",
        include_danmaku=False,
        render_version="ass-v1",
        filename="clip-export-failed.mp4",
    )
    repository.fail_video_clip_export(newer.id, "测试失败")

    with TestClient(app) as anonymous:
        listed = anonymous.get(f"/api/jobs/{job.id}/clip-exports")
        status = anonymous.get(
            f"/api/jobs/{job.id}/clip-exports/clip-export-1"
        )
        download = anonymous.get(
            f"/jobs/{job.id}/clip-exports/clip-export-1/download",
            follow_redirects=False,
        )
        legacy_download = anonymous.get(
            f"/jobs/{job.id}/clips/0/download",
            follow_redirects=False,
        )

    assert suggestion.status_code == 200
    assert suggestion.json()["source"] == "silence"
    assert suggestion.json()["suggested_ms"] == 29_850
    assert created.status_code == 200
    assert repeated.json()["id"] == created.json()["id"]
    assert created.json()["subtitle_mode"] == "zh"
    assert created.json()["subtitle_font_scale"] == 125
    assert created.json()["subtitle_text_color"] == "#E43D12"
    assert created.json()["subtitle_background_color"] == "#EBE9E1"
    assert created.json()["output_layout"] == "landscape"
    assert created.json()["subtitle_font_family"] == "serif"
    assert created.json()["cover_enabled"] is True
    assert created.json()["cover_timestamp_ms"] == 45_000
    assert created.json()["cover_title"] == "灯光亮起时"
    assert created.json()["cover_style"] == "badge"
    assert created.json()["cover_duration_ms"] == 1500
    assert created.json()["duration_ms"] == 32_150
    first_started = clipper.started_exports[0]
    assert first_started["start_ms"] == 29_850
    assert first_started["subtitle_font_scale"] == 125
    assert first_started["output_layout"] == "landscape"
    assert first_started["subtitle_font_family"] == "serif"
    assert first_started["cover_timestamp_ms"] == 45_000
    assert first_started["cover_title"] == "灯光亮起时"
    cut_started = next(
        item
        for item in clipper.started_exports
        if item["request_id"] == "request-cut-clip"
    )
    assert cut_started["kept_ranges"] == [
        ClipRange(start_ms=30_000, end_ms=40_000),
        ClipRange(start_ms=50_000, end_ms=60_000),
    ]
    assert low_contrast.status_code == 422
    assert english_blocked.status_code == 409
    assert invalid_cover.status_code == 422
    assert cut_created.status_code == 200
    assert cut_created.json()["kept_ranges"] == [
        {"start_ms": 30_000, "end_ms": 40_000},
        {"start_ms": 50_000, "end_ms": 60_000},
    ]
    assert cut_created.json()["duration_ms"] == 20_000
    assert wide_cut_created.status_code == 200
    assert wide_cut_created.json()["duration_ms"] == 20_000
    assert (
        english_blocked.json()["error"]["code"]
        == "clip_english_subtitles_not_ready"
    )
    assert listed.status_code == 200
    assert len(listed.json()["clips"]) == 4
    assert status.json()["id"] == "clip-export-1"
    assert download.status_code == 303
    assert download.headers["location"].startswith("https://oss.example/")
    assert legacy_download.status_code == 303
    assert legacy_download.headers["location"].startswith(
        "https://oss.example/"
    )


def auth_app(
    settings,
    repository,
    *,
    daily_limit=3,
    clipper=None,
    member_catalog=None,
):
    auth_settings = settings.model_copy(
        update={
            "auth_required": True,
            "session_cookie_secure": False,
            "daily_job_limit": daily_limit,
        }
    )
    auth_repository = AuthRepository(repository.database)
    auth_repository.create_user(
        "alice",
        "alice",
        hash_password("alice has a secure password"),
        is_admin=True,
    )
    auth_repository.create_user(
        "bob",
        "bob",
        hash_password("bob also has secure password"),
        is_admin=False,
    )
    app = create_app(
        auth_settings,
        ApplicationServices(
            repository=repository,
            worker=DummyWorker(),
            clipper=clipper,
            member_catalog=member_catalog,
        ),
    )
    return app


def login(client: TestClient, username: str, password: str):
    return client.post(
        "/login",
        content=f"username={username}&password={password}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("p48_csrf")}


def test_glossary_admin_requires_admin_and_manages_entries(
    settings, repository
):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id="10337",
                canonical_name="曹可甜",
                pinyin="Cao KeTian",
                group_id="10",
                group_name="SNH",
                team_id="101",
                team_name="SII",
                status="99",
                active=True,
            )
        ],
        source_url=settings.member_catalog_url,
        source_hash="e" * 64,
    )
    repository.activate_vocabulary("vocab-admin-test", "f" * 64)
    member_catalog = DummyMemberCatalog()
    app = auth_app(
        settings,
        repository,
        member_catalog=member_catalog,
    )

    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/admin/glossary", follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    with TestClient(app) as bob:
        login(bob, "bob", "bob also has secure password")
        response = bob.get("/admin/glossary")
        assert response.status_code == 403

    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        page = alice.get("/admin/glossary")
        assert page.status_code == 200
        assert "成员与术语词库" in page.text
        assert "曹可甜" in page.text
        assert "vocab-admin-test" in page.text

        csrf = alice.cookies.get("p48_csrf")
        created_term = alice.post(
            "/admin/glossary/terms",
            data={
                "_csrf": csrf,
                "canonical_text": "春晚",
                "term_type": "event",
                "description_zh": "SNH48 GROUP 年度活动",
                "description_en": "Annual group event",
            },
            follow_redirects=False,
        )
        assert created_term.status_code == 303
        term = repository.list_glossary_terms()[0]

        created_alias = alice.post(
            "/admin/glossary/aliases",
            data={
                "_csrf": csrf,
                "target_kind": "member",
                "target_id": "10337",
                "alias": "甜甜",
            },
            follow_redirects=False,
        )
        assert created_alias.status_code == 303
        alias = repository.list_glossary_aliases()[0]
        assert alias.target_text == "曹可甜"

        deactivated = alice.post(
            f"/admin/glossary/terms/{term.id}/active",
            data={"_csrf": csrf, "active": "0"},
            follow_redirects=False,
        )
        assert deactivated.status_code == 303
        assert repository.list_glossary_terms()[0].active is False

        sync = alice.post(
            "/admin/glossary/sync",
            data={"_csrf": csrf},
            follow_redirects=False,
        )
        assert sync.status_code == 303
        assert member_catalog.force is True


def test_authentication_and_csrf(settings, repository):
    app = auth_app(settings, repository)
    with TestClient(app) as client:
        anonymous = client.get("/", follow_redirects=False)
        assert anonymous.status_code == 200
        assert 'id="create-job-form"' not in anonymous.text
        assert "最近公开结果" in anonymous.text

        protected = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800000"
                )
            },
        )
        assert protected.status_code == 401

        response = login(client, "alice", "alice has a secure password")
        assert response.status_code == 303
        assert client.get("/").status_code == 200

        without_csrf = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800001"
                )
            },
        )
        assert without_csrf.status_code == 403

        created = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800001"
                )
            },
            headers=csrf_headers(client),
        )
        assert created.status_code == 201


def test_completed_result_is_public_but_raw_asr_requires_login(
    settings, repository
):
    app = auth_app(settings, repository)
    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        created = alice.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800010"
                )
            },
            headers=csrf_headers(alice),
        )
        job_id = created.json()["id"]
    claimed = repository.claim_next_job("test-worker", 120)
    assert claimed and claimed.id == job_id
    repository.save_asr_raw(job_id, '{"transcripts":[]}')
    public_summary = FinalSummary(
        overview="公开结果",
        timeline=[],
        topics=[],
        highlights=[],
    )
    repository.save_summary(
        job_id, public_summary.model_dump_json(), "# 公开结果"
    )
    with repository.database.connect() as connection:
        connection.execute(
            """
            UPDATE jobs SET replay_started_at = ?
            WHERE id = ?
            """,
            ("2026-08-22T10:57:06+00:00", job_id),
        )
    repository.mark_completed(job_id)

    with TestClient(app) as anonymous:
        index = anonymous.get("/")
        page = anonymous.get(f"/jobs/{job_id}")
        summary = anonymous.get(f"/jobs/{job_id}/summary.md")
        raw_asr = anonymous.get(
            f"/jobs/{job_id}/asr.json", follow_redirects=False
        )

    assert "800010" in index.text
    assert 'data-i18n="liveTime">直播时间</span>' in index.text
    assert "2026-08-22 18:57" in index.text
    assert page.status_code == 200
    assert summary.status_code == 200
    assert raw_asr.status_code == 303
    assert raw_asr.headers["location"] == "/login"


def test_homepage_member_filter_respects_job_visibility(
    settings, repository
):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id=member_id,
                canonical_name=member_name,
                group_id="10",
                group_name="SNH48",
                status="99",
                active=True,
            )
            for member_id, member_name in (
                ("1001", "成员甲"),
                ("1002", "成员乙"),
                ("1003", "成员丙"),
                ("1004", "成员丁"),
            )
        ],
        source_url=settings.member_catalog_url,
        source_hash="f" * 64,
    )
    app = auth_app(settings, repository)
    auth_repository = AuthRepository(repository.database)
    alice = auth_repository.get_user_by_username("alice")
    bob = auth_repository.get_user_by_username("bob")
    assert alice and bob

    def add_job(
        live_id: str,
        member_id: str,
        member_name: str,
        title: str,
        *,
        user_id: str = "local",
        completed: bool,
    ):
        job, _ = repository.create_or_get_job(
            (
                "https://h5.48.cn/2019appshare/memberLiveShare/"
                f"index.html?id={live_id}"
            ),
            live_id,
            user_id,
        )
        if completed:
            claimed = repository.claim_next_job("filter-worker", 120)
            assert claimed and claimed.id == job.id
        repository.save_replay_metadata(
            job.id,
            ReplayMetadata(
                live_id=live_id,
                member_id=member_id,
                member_name=f"SNH48-{member_name}",
                title=title,
                media_url="https://idol-vod.48.cn/replay.m3u8",
            ),
        )
        if completed:
            repository.mark_completed(job.id)
        return job

    public_a = add_job(
        "810001", "1001", "成员甲", "甲的公开直播", completed=True
    )
    public_b = add_job(
        "810002", "1002", "成员乙", "乙的公开直播", completed=True
    )
    private_c = add_job(
        "810003",
        "1003",
        "成员丙",
        "丙的私有任务",
        user_id=alice.id,
        completed=False,
    )
    private_d = add_job(
        "810004",
        "1004",
        "成员丁",
        "丁的私有任务",
        user_id=bob.id,
        completed=False,
    )

    with TestClient(app) as anonymous:
        home = anonymous.get("/")
        filtered = anonymous.get("/?member=1001")
        hidden_filter = anonymous.get("/?member=1003")

    assert 'id="member-filter"' in home.text
    assert "成员甲 · SNH48 (1)" in home.text
    assert "成员乙 · SNH48 (1)" in home.text
    assert "成员丙" not in home.text
    assert "成员丁" not in home.text
    assert public_a.live_id in filtered.text
    assert public_b.live_id not in filtered.text
    assert 'value="1001"' in filtered.text
    assert "selected" in filtered.text
    assert public_a.live_id in hidden_filter.text
    assert public_b.live_id in hidden_filter.text
    assert private_c.live_id not in hidden_filter.text

    with TestClient(app) as alice_client:
        login(alice_client, "alice", "alice has a secure password")
        alice_home = alice_client.get("/")
        alice_filtered = alice_client.get("/?member=1003")

    assert "成员丙 · SNH48 (1)" in alice_home.text
    assert "成员丁" not in alice_home.text
    assert private_c.live_id in alice_filtered.text
    assert public_a.live_id not in alice_filtered.text
    assert public_b.live_id not in alice_filtered.text
    assert private_d.live_id not in alice_filtered.text


def test_user_cannot_read_another_users_job(settings, repository):
    app = auth_app(settings, repository)
    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        created = alice.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800002"
                )
            },
            headers=csrf_headers(alice),
        )
        job_id = created.json()["id"]

    with TestClient(app) as bob:
        login(bob, "bob", "bob also has secure password")
        assert bob.get(f"/api/jobs/{job_id}/status").status_code == 404
        assert bob.get(f"/jobs/{job_id}/summary.md").status_code == 404


def test_daily_quota_is_enforced(settings, repository):
    app = auth_app(settings, repository, daily_limit=1)
    with TestClient(app) as client:
        login(client, "alice", "alice has a secure password")
        first = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800003"
                )
            },
            headers=csrf_headers(client),
        )
        second = client.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800004"
                )
            },
            headers=csrf_headers(client),
        )
        assert first.status_code == 201
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "daily_quota_exceeded"


def test_configured_user_has_unlimited_job_quota(settings, repository):
    unlimited_settings = settings.model_copy(
        update={"unlimited_job_usernames": "alice"}
    )
    app = auth_app(unlimited_settings, repository, daily_limit=1)
    with TestClient(app) as client:
        login(client, "alice", "alice has a secure password")
        page = client.get("/")
        responses = [
            client.post(
                "/api/jobs",
                json={
                    "url": (
                        "https://h5.48.cn/2019appshare/memberLiveShare/"
                        f"index.html?id={live_id}"
                    )
                },
                headers=csrf_headers(client),
            )
            for live_id in ("800005", "800006")
        ]

    assert "当前额度：无限任务" in page.text
    assert [response.status_code for response in responses] == [201, 201]


def test_any_invited_user_can_clip_a_public_result(
    settings, repository, tmp_path
):
    output_path = tmp_path / "public-timeline.mp4"
    output_path.write_bytes(b"public clip")
    clipper = DummyClipper(output_path)
    app = auth_app(settings, repository, clipper=clipper)

    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        created = alice.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800020"
                )
            },
            headers=csrf_headers(alice),
        )
        job_id = created.json()["id"]

    repository.set_media_details(
        job_id,
        "https://idol-vod.48.cn/path/public-replay.m3u8",
        600_000,
    )
    summary = FinalSummary(
        overview="公开测试",
        timeline=[
            TimelineItem(
                start_ms=30_000,
                end_ms=60_000,
                title="公开时间线",
                detail="任何受邀账号都可剪辑",
                evidence_segment_ids=[1],
            )
        ],
        topics=[],
        highlights=[],
    )
    repository.save_summary(job_id, summary.model_dump_json(), "# 公开测试")
    claimed = repository.claim_next_job("test-worker", 120)
    assert claimed and claimed.id == job_id
    repository.mark_completed(job_id)

    with TestClient(app) as bob:
        login(bob, "bob", "bob also has secure password")
        response = bob.post(
            f"/api/jobs/{job_id}/clips/0",
            headers=csrf_headers(bob),
        )

    with TestClient(app) as anonymous:
        page = anonymous.get(f"/jobs/{job_id}")
        denied_create = anonymous.post(
            f"/api/jobs/{job_id}/clip-exports",
            json={
                "request_id": "guest-cannot-submit",
                "timeline_index": 0,
                "start_ms": 30_000,
                "end_ms": 60_000,
                "subtitle_mode": "zh",
            },
        )
        clip_status = anonymous.get(f"/api/jobs/{job_id}/clips/0")
        download = anonymous.get(f"/jobs/{job_id}/clips/0/download")

    assert response.status_code == 200
    assert clipper.started_with["job_id"] == job_id
    assert 'class="clip-button"' in page.text
    assert 'id="clip-editor"' in page.text
    assert 'data-can-submit="false"' in page.text
    assert "游客可以体验全部剪辑设置" in page.text
    assert 'data-i18n="loginToCreateClip"' in page.text
    assert 'id="replay-player"' in page.text
    assert "hls-1.7.1.min.js" in page.text
    assert (
        'data-hls-src="https://idol-vod.48.cn/path/public-replay.m3u8"'
        in page.text
    )
    assert 'data-seek-ms="30000"' in page.text
    assert denied_create.status_code == 401
    assert clip_status.status_code == 200
    assert clip_status.json()["status"] == "completed"
    assert clip_status.json()["download_url"].endswith("/clips/0/download")
    assert download.status_code == 200
    assert download.content == b"public clip"


def test_playback_track_is_public_and_user_can_request_translation(
    settings, repository
):
    app = auth_app(settings, repository)
    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        created = alice.post(
            "/api/jobs",
            json={
                "url": (
                    "https://h5.48.cn/2019appshare/memberLiveShare/"
                    "index.html?id=800030"
                )
            },
            headers=csrf_headers(alice),
        )
        job_id = created.json()["id"]

    claimed = repository.claim_next_job("main-worker", 120)
    assert claimed and claimed.id == job_id
    repository.set_media_details(
        job_id,
        "https://idol-vod.48.cn/path/synchronized.m3u8",
        120_000,
    )
    repository.replace_transcript(
        job_id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=5597,
                end_ms=8000,
                text="同步字幕",
            )
        ],
    )
    repository.replace_danmaku(
        job_id,
        [
            DanmakuEntry(
                sequence=1,
                timestamp_ms=13491,
                author="fan",
                text="同步弹幕",
            )
        ],
    )
    repository.mark_completed(job_id)

    with TestClient(app) as anonymous:
        page = anonymous.get(f"/jobs/{job_id}")
        track = anonymous.get(f"/api/jobs/{job_id}/playback-track")
        styles = anonymous.get("/static/styles.css")
        javascript = anonymous.get("/static/app.js")
        denied = anonymous.post(
            f"/api/jobs/{job_id}/translations/en",
            follow_redirects=False,
        )

    assert page.status_code == 200
    assert 'id="subtitle-mode"' in page.text
    assert 'id="live-danmaku-panel"' in page.text
    assert 'class="live-danmaku-panel mobile-danmaku-overlay"' in page.text
    assert 'id="playback-layout"' in page.text
    assert 'id="language-toggle"' in page.text
    assert 'id="mobile-history-nav"' in page.text
    assert 'id="history-back"' in page.text
    assert 'id="history-forward"' in page.text
    assert "i18n.js?v=20260826-17" in page.text
    assert "styles.css?v=20260826-23" in page.text
    assert "app.js?v=20260826-18" in page.text
    assert 'aria-keyshortcuts="Space"' in page.text
    assert 'id="danmaku-opacity"' not in page.text
    assert styles.status_code == 200
    assert "(pointer: coarse)" in styles.text
    assert "(prefers-reduced-motion: reduce)" in styles.text
    assert ".clip-timeline-viewport" in styles.text
    assert ".clip-boundary-handle" in styles.text
    assert ".clip-segment-block" in styles.text
    assert ".clip-cut-panel" in styles.text
    assert ".clip-lyric-preview" in styles.text
    assert ".clip-style-panel" in styles.text
    assert ".clip-output-layout" in styles.text
    assert ".clip-editor.is-landscape-layout .clip-editor-content" in (
        styles.text
    )
    assert ".clip-preview-stage.is-landscape-layout" in styles.text
    assert "kept_ranges: keptRanges" in javascript.text
    assert "splitClipAtMarkedTime" in javascript.text
    assert 'event.code !== "Space"' in javascript.text
    assert "seekClipPreview(clipEditorState.markerMs)" in javascript.text
    assert "container-type: size" in styles.text
    assert "padding: 1.11cqh .73cqw" in styles.text
    assert "font-size: 2.04cqh" in styles.text
    assert "left: 3.75%" in styles.text
    assert "width: 26.5%" in styles.text
    assert "right: 3.4%" in styles.text
    assert "width: 27%" in styles.text
    assert ".mobile-danmaku-overlay" in styles.text
    assert ".mobile-history-nav" in styles.text
    assert "safe-area-inset-bottom" in styles.text
    assert "--mobile-overlay-content-bottom: 10px" in styles.text
    assert "--mobile-danmaku-stream-padding: 6px" in styles.text
    assert "font-size: clamp(11px, 3vw, 13px)" in styles.text
    assert "font-size: clamp(9px, 2.5vw, 11px)" in styles.text
    assert "position: absolute" in styles.text
    assert javascript.status_code == 200
    assert "mobileDanmakuMedia" in javascript.text
    assert "mobileDensityProfiles" in javascript.text
    assert "setPointerCapture" in javascript.text
    assert "CLIP_SNAP_RELEASE_PX" in javascript.text
    assert "CLIP_DANMAKU_MAX_VISIBLE" in javascript.text
    assert "renderClipDanmakuPreview" in javascript.text
    assert "milliseconds - 5000" not in javascript.text
    assert "2.13 * scale" in javascript.text
    assert "clipLyricHoverFrame" in javascript.text
    assert "pinClipTimelineMarker" in javascript.text
    assert "renderClipTimelineHoverMarker" in javascript.text
    assert "renderClipTimelineMarkedMarker" in javascript.text
    assert "CLIP_DEFAULT_FONT_SCALE = 100" in javascript.text
    assert "CLIP_SUBTITLE_BASE_SCALE = 1.6" in javascript.text
    assert "captureClipCoverFrame" in javascript.text
    assert "coverReturnMs" in javascript.text
    assert "cover_timestamp_ms" in javascript.text
    assert (
        "renderClipLyricPreview(clipEditorState.coverTimestampMs)"
        not in javascript.text
    )
    assert "clipMarkerTime" not in javascript.text
    assert "nearestClipMarker" in javascript.text
    assert 'locked?.kind === "sentence"' in javascript.text
    assert "clipLyricPreviousTwo" in javascript.text
    assert "subtitle_font_scale" in javascript.text
    assert "output_layout" in javascript.text
    assert "subtitle_font_family" in javascript.text
    assert "clipStartRange" not in javascript.text
    assert "window.history.back()" in javascript.text
    assert "window.history.forward()" in javascript.text
    assert track.status_code == 200
    assert track.json()["subtitles"][0] == {
        "sequence": 1,
        "start_ms": 5597,
        "end_ms": 8000,
        "zh": "同步字幕",
        "en": None,
    }
    assert track.json()["danmaku"][0]["timestamp_ms"] == 13491
    assert denied.status_code == 401

    with TestClient(app) as bob:
        login(bob, "bob", "bob also has secure password")
        requested = bob.post(
            f"/api/jobs/{job_id}/translations/en",
            headers=csrf_headers(bob),
        )

    assert requested.status_code == 202
    assert requested.json()["status"] == "queued"
