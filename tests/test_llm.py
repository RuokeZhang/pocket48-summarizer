import json

import httpx
import pytest

from pocket48_summarizer.clients.llm import OpenAICompatibleClient
from pocket48_summarizer.errors import ExternalServiceError
from pocket48_summarizer.models import FinalSummary


@pytest.mark.asyncio
async def test_sends_strict_json_schema_and_output_limit(settings):
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "overview": "总结",
                                    "timeline": [],
                                    "topics": [],
                                    "highlights": [],
                                    "danmaku_peak_summaries": [],
                                    "verification_needed": [],
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    configured = settings.model_copy(
        update={
            "llm_response_format": "json_schema",
            "llm_max_output_tokens": 16_384,
        }
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = OpenAICompatibleClient(configured, client=http_client)

    payload = await client.chat_json(
        system_prompt="Return JSON.",
        user_prompt="Summarize.",
        response_model=FinalSummary,
    )

    assert payload["overview"] == "总结"
    assert captured["max_tokens"] == 16_384
    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "FinalSummary"
    assert set(response_format["json_schema"]["schema"]["required"]) == {
        "overview",
        "timeline",
        "topics",
        "highlights",
        "danmaku_peak_summaries",
        "verification_needed",
    }
    assert (
        response_format["json_schema"]["schema"]["additionalProperties"]
        is False
    )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_reports_truncated_model_output(settings):
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"overview":"partial"'},
                        }
                    ]
                },
            )
        )
    )
    client = OpenAICompatibleClient(settings, client=http_client)

    with pytest.raises(ExternalServiceError) as error:
        await client.chat_json(
            system_prompt="Return JSON.",
            user_prompt="Summarize.",
            response_model=FinalSummary,
        )

    assert error.value.code == "llm_output_truncated"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_retries_truncated_output_with_recovery_limit(settings):
    requested_limits = []

    def handler(request):
        body = json.loads(request.content)
        requested_limits.append(body["max_tokens"])
        if len(requested_limits) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"overview":"partial"'},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "overview": "完整总结",
                                    "timeline": [],
                                    "topics": [],
                                    "highlights": [],
                                    "danmaku_peak_summaries": [],
                                    "verification_needed": [],
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    configured = settings.model_copy(
        update={
            "external_retry_attempts": 2,
            "llm_max_output_tokens": 16_384,
            "llm_truncation_retry_max_tokens": 65_536,
        }
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = OpenAICompatibleClient(configured, client=http_client)

    payload = await client.chat_json(
        system_prompt="Return JSON.",
        user_prompt="Summarize.",
        response_model=FinalSummary,
    )

    assert payload["overview"] == "完整总结"
    assert requested_limits == [16_384, 65_536]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_truncation_retry_gets_proportionally_longer_timeout(settings):
    timeouts = []

    def handler(request):
        body = json.loads(request.content)
        timeouts.append(
            (body["max_tokens"], request.extensions["timeout"]["read"])
        )
        if len(timeouts) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"overview":"partial"'},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "overview": "完整总结",
                                    "timeline": [],
                                    "topics": [],
                                    "highlights": [],
                                    "danmaku_peak_summaries": [],
                                    "verification_needed": [],
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    configured = settings.model_copy(
        update={
            "external_retry_attempts": 2,
            "llm_max_output_tokens": 16_384,
            "llm_truncation_retry_max_tokens": 65_536,
            "llm_timeout_seconds": 120.0,
        }
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(configured, client=http_client)

    await client.chat_json(
        system_prompt="Return JSON.",
        user_prompt="Summarize.",
        response_model=FinalSummary,
    )

    assert timeouts[0] == (16_384, 120.0)
    assert timeouts[1] == (65_536, 480.0)
    await http_client.aclose()


def test_scaled_timeout_is_capped_and_never_shrinks():
    assert OpenAICompatibleClient._scaled_timeout(600.0, 32_768, 65_536) == 900.0
    assert OpenAICompatibleClient._scaled_timeout(120.0, 65_536, 32_768) == 120.0
    assert OpenAICompatibleClient._scaled_timeout(120.0, 0, 65_536) == 120.0


@pytest.mark.asyncio
async def test_timeout_failure_reports_the_underlying_reason(settings):
    def handler(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    configured = settings.model_copy(
        update={
            "external_retry_attempts": 1,
            "llm_timeout_seconds": 120.0,
        }
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(configured, client=http_client)

    with pytest.raises(ExternalServiceError) as error:
        await client.chat_json(
            system_prompt="Return JSON.",
            user_prompt="Summarize.",
            response_model=FinalSummary,
        )

    assert error.value.code == "llm_request_failed"
    assert "请求超时" in error.value.message
    assert "120 秒" in error.value.message
    await http_client.aclose()


@pytest.mark.asyncio
async def test_connection_failure_names_the_transport_error(settings):
    def handler(request):
        raise httpx.ConnectError("nodename nor servname provided")

    configured = settings.model_copy(
        update={"external_retry_attempts": 1}
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(configured, client=http_client)

    with pytest.raises(ExternalServiceError) as error:
        await client.chat_json(
            system_prompt="Return JSON.",
            user_prompt="Summarize.",
            response_model=FinalSummary,
        )

    assert "ConnectError" in error.value.message
    assert "nodename" in error.value.message
    assert "请求超时" not in error.value.message
    await http_client.aclose()
