from fastapi.testclient import TestClient

from pocket48_summarizer.app import create_app
from pocket48_summarizer.media.clips import ClipState
from pocket48_summarizer.models import (
    DanmakuEntry,
    DanmakuPeak,
    DanmakuPeakSummary,
    FinalSummary,
    TimelineItem,
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


class DummyClipper:
    def __init__(self, output_path):
        self.state = ClipState("completed", output_path)
        self.started_with = None

    def start(self, **kwargs):
        self.started_with = kwargs
        return self.state

    def get(self, **kwargs):
        return self.state

    async def close(self):
        return None


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

    assert 'class="clip-button"' in page.text
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["download_url"].endswith("/clips/0/download")
    assert clipper.started_with["start_ms"] == 61_250
    assert clipper.started_with["end_ms"] == 125_750
    assert download.status_code == 200
    assert download.content == b"fake mp4"
