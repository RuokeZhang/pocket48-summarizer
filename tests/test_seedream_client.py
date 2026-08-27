import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from pocket48_summarizer.clients.seedream import SeedreamClient
from pocket48_summarizer.errors import (
    ConfigurationError,
    ExternalServiceError,
)


def seedream_settings(settings, **updates):
    return settings.model_copy(
        update={
            "ark_api_key": SecretStr("test-ark-key"),
            "ark_seedream_model": "doubao-seedream-test",
            "ai_cover_download_max_bytes": 1024,
            **updates,
        }
    )


@pytest.mark.asyncio
async def test_seedream_generates_reference_image_with_exact_size(settings):
    requests: list[httpx.Request] = []
    image = b"\xff\xd8\xff" + b"generated-jpeg"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "ark-request-1"},
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(image).decode(),
                        "size": "1440x2560",
                    }
                ]
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = SeedreamClient(
        seedream_settings(settings),
        client=http_client,
    )

    generated = await client.generate(
        reference_image_url="https://oss.example/source.png?signature=test",
        prompt="variety cover without text",
        width=1440,
        height=2560,
        seed=42,
    )

    assert generated.content == image
    assert generated.content_type == "image/jpeg"
    assert generated.provider_request_id == "ark-request-1"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v3/images/generations"
    assert request.headers["Authorization"] == "Bearer test-ark-key"
    payload = json.loads(request.content)
    assert payload == {
        "model": "doubao-seedream-test",
        "prompt": "variety cover without text",
        "image": "https://oss.example/source.png?signature=test",
        "size": "1440x2560",
        "response_format": "b64_json",
        "watermark": False,
    }
    await http_client.aclose()


@pytest.mark.asyncio
async def test_seedream_surfaces_moderation_rejection(settings):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "OutputImageSensitiveContentDetected",
                    "message": "sensitive output",
                }
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = SeedreamClient(
        seedream_settings(settings),
        client=http_client,
    )

    with pytest.raises(
        ExternalServiceError,
        match="内容安全审核",
    ) as error:
        await client.generate(
            reference_image_url="https://oss.example/source.png",
            prompt="test",
            width=1440,
            height=2560,
            seed=None,
        )

    assert error.value.code == "ai_cover_moderation_rejected"
    assert not error.value.retryable
    await http_client.aclose()


@pytest.mark.asyncio
async def test_seedream_rejects_oversized_base64_before_decode(settings):
    encoded = base64.b64encode(b"\xff\xd8\xff" + b"x" * 2048).decode()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = SeedreamClient(
        seedream_settings(settings, ai_cover_download_max_bytes=64),
        client=http_client,
    )

    with pytest.raises(ExternalServiceError) as error:
        await client.generate(
            reference_image_url="https://oss.example/source.png",
            prompt="test",
            width=1440,
            height=2560,
            seed=None,
        )

    assert error.value.code in {
        "seedream_response_too_large",
        "seedream_image_too_large",
    }
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            httpx.Response(200, content=b"not-json"),
            "seedream_invalid_response",
        ),
        (
            httpx.Response(
                200,
                json={"data": [{"url": "https://untrusted.example/out.png"}]},
            ),
            "seedream_invalid_response",
        ),
        (
            httpx.Response(
                302,
                headers={"location": "https://other.example/result"},
            ),
            "seedream_unexpected_redirect",
        ),
    ],
)
async def test_seedream_rejects_unexpected_responses(
    settings, response, expected_code
):
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: response)
    )
    client = SeedreamClient(
        seedream_settings(settings),
        client=http_client,
    )

    with pytest.raises(ExternalServiceError) as error:
        await client.generate(
            reference_image_url="https://oss.example/source.png",
            prompt="test",
            width=1440,
            height=2560,
            seed=None,
        )

    assert error.value.code == expected_code
    await http_client.aclose()


@pytest.mark.asyncio
async def test_seedream_maps_timeout_to_retryable_failure(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = SeedreamClient(
        seedream_settings(settings),
        client=http_client,
    )

    with pytest.raises(ExternalServiceError) as error:
        await client.generate(
            reference_image_url="https://oss.example/source.png",
            prompt="test",
            width=1440,
            height=2560,
            seed=None,
        )

    assert error.value.code == "seedream_request_failed"
    assert error.value.retryable
    await http_client.aclose()


@pytest.mark.asyncio
async def test_seedream_rejects_non_https_reference_before_request(settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = SeedreamClient(
        seedream_settings(settings),
        client=http_client,
    )

    with pytest.raises(ExternalServiceError) as error:
        await client.generate(
            reference_image_url="http://oss.example/source.png",
            prompt="test",
            width=1440,
            height=2560,
            seed=None,
        )

    assert error.value.code == "seedream_reference_invalid"
    assert requests == []
    await http_client.aclose()


def test_seedream_requires_environment_configuration(settings):
    with pytest.raises(ConfigurationError, match="ARK_API_KEY"):
        SeedreamClient(settings)
