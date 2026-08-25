import json

import httpx
import pytest

from pocket48_summarizer.clients.dashscope import DashScopeClient
from pocket48_summarizer.errors import ExternalServiceError


@pytest.mark.asyncio
async def test_task_polling_uses_get_without_async_header(settings):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "FAILED",
                    "message": "test failure",
                }
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = DashScopeClient(settings, client=http_client)

    with pytest.raises(ExternalServiceError):
        await client.wait_for_result("task-1")

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "X-DashScope-Async" not in requests[0].headers
    await http_client.aclose()


@pytest.mark.asyncio
async def test_vocabulary_lifecycle_and_asr_submission(settings):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        action = payload.get("input", {}).get("action")
        if action == "create_vocabulary":
            return httpx.Response(
                200,
                json={"output": {"vocabulary_id": "vocab-p48-test"}},
            )
        if action == "query_vocabulary":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "status": "OK",
                        "target_model": "paraformer-v2",
                        "vocabulary": [],
                    }
                },
            )
        if action == "delete_vocabulary":
            return httpx.Response(200, json={"output": {}})
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "task-with-vocabulary",
                    "task_status": "PENDING",
                }
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = DashScopeClient(settings, client=http_client)

    vocabulary_id = await client.create_vocabulary(
        prefix="p48vocab",
        target_model="paraformer-v2",
        vocabulary=[{"text": "曹可甜", "weight": 4, "lang": "zh"}],
    )
    queried = await client.query_vocabulary(vocabulary_id)
    task_id, _ = await client.submit(
        "https://oss.example/audio.mp3",
        vocabulary_id=vocabulary_id,
    )
    await client.delete_vocabulary(vocabulary_id)

    assert queried["status"] == "OK"
    assert task_id == "task-with-vocabulary"
    customization_requests = [
        request
        for request in requests
        if request.url.path.endswith("/customization")
    ]
    assert len(customization_requests) == 3
    assert all(
        "X-DashScope-Async" not in request.headers
        for request in customization_requests
    )
    transcription = next(
        request
        for request in requests
        if request.url.path.endswith("/transcription")
    )
    transcription_payload = json.loads(transcription.content)
    assert (
        transcription_payload["parameters"]["vocabulary_id"]
        == "vocab-p48-test"
    )
    assert transcription.headers["X-DashScope-Async"] == "enable"
    await http_client.aclose()
