from fastapi.testclient import TestClient

from pocket48_summarizer.app import create_app
from pocket48_summarizer.auth import AuthRepository, hash_password
from pocket48_summarizer.media.clips import ClipState
from pocket48_summarizer.models import (
    DanmakuEntry,
    DanmakuPeak,
    DanmakuPeakSummary,
    FinalSummary,
    TimelineItem,
)
from pocket48_summarizer.routes import format_china_datetime
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


class DummyClipper:
    def __init__(self, output_path):
        self.state = ClipState("completed", output_path)
        self.started_with = None

    def start(self, **kwargs):
        self.started_with = kwargs
        return self.state

    def get(self, **kwargs):
        return self.state

    async def startup(self):
        return None

    async def signed_download_url(self, state):
        return f"https://oss.example/{state.oss_object_key}?signed=1"

    async def close(self):
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
        response = client.post(f"/api/jobs/{job.id}/clips/0")
        download = client.get(f"/jobs/{job.id}/clips/0/download")
        clipper.state.oss_object_key = "clips/job/timeline.mp4"
        output_path.unlink()
        redirect = client.get(
            f"/jobs/{job.id}/clips/0/download",
            follow_redirects=False,
        )

    assert 'class="clip-button"' in page.text
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["download_url"].endswith("/clips/0/download")
    assert clipper.started_with["start_ms"] == 61_250
    assert clipper.started_with["end_ms"] == 125_750
    assert download.status_code == 200
    assert download.content == b"fake mp4"
    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("https://oss.example/")


def auth_app(
    settings,
    repository,
    *,
    daily_limit=3,
    clipper=None,
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
    assert "直播时间 · 2026-08-22 18:57" in index.text
    assert page.status_code == 200
    assert summary.status_code == 200
    assert raw_asr.status_code == 303
    assert raw_asr.headers["location"] == "/login"


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
        download = anonymous.get(f"/jobs/{job_id}/clips/0/download")

    assert response.status_code == 200
    assert clipper.started_with["job_id"] == job_id
    assert 'class="clip-button"' not in page.text
    assert 'id="replay-player"' in page.text
    assert "hls-1.7.1.min.js" in page.text
    assert (
        'data-hls-src="https://idol-vod.48.cn/path/public-replay.m3u8"'
        in page.text
    )
    assert 'data-seek-ms="30000"' in page.text
    assert download.status_code == 200
    assert download.content == b"public clip"
