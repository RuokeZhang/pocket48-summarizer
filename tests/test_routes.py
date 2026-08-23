from fastapi.testclient import TestClient

from pocket48_summarizer.app import create_app
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


def test_rejects_untrusted_host_header(settings, repository):
    app = create_app(
        settings,
        ApplicationServices(repository=repository, worker=DummyWorker()),
    )
    with TestClient(app) as client:
        response = client.get("/", headers={"Host": "evil.example"})
        assert response.status_code == 400
