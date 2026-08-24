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
