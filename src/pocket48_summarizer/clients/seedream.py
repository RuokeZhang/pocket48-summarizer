from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..errors import ConfigurationError, ExternalServiceError
from ..media.cover_providers import GeneratedCoverImage

SENSITIVE_OUTPUT_CODES = {
    "OutputImageSensitiveContentDetected",
    "InputImageSensitiveContentDetected",
    "SensitiveContentDetected",
}


class SeedreamClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        if not settings.ark_api_key:
            raise ConfigurationError("ARK_API_KEY 未配置")
        if not settings.ark_seedream_model:
            raise ConfigurationError("ARK_SEEDREAM_MODEL 未配置")
        self.client = client or httpx.AsyncClient(
            timeout=settings.ai_cover_request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    @property
    def endpoint(self) -> str:
        return (
            self.settings.ark_base_url.rstrip("/")
            + "/images/generations"
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                "Bearer " + self.settings.ark_api_key.get_secret_value()
            ),
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        *,
        reference_image_url: str,
        prompt: str,
        width: int,
        height: int,
        seed: int | None,
    ) -> GeneratedCoverImage:
        del seed
        self._validate_reference_url(reference_image_url)
        payload = await self._post_json(
            {
                "model": self.settings.ark_seedream_model,
                "prompt": prompt,
                "image": reference_image_url,
                "size": f"{width}x{height}",
                "response_format": "b64_json",
                "watermark": False,
            }
        )
        error = payload.get("error")
        if isinstance(error, dict):
            self._raise_provider_error(error)
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ExternalServiceError(
                "seedream_invalid_response",
                "Seedream 没有返回生成图片",
                True,
            )
        selected: dict[str, Any] | None = None
        for item in data:
            if not isinstance(item, dict):
                continue
            item_error = item.get("error")
            if isinstance(item_error, dict):
                self._raise_provider_error(item_error)
            if isinstance(item.get("b64_json"), str):
                selected = item
                break
        if selected is None:
            raise ExternalServiceError(
                "seedream_invalid_response",
                "Seedream 没有返回 Base64 图片数据",
                True,
            )
        encoded = selected["b64_json"]
        if len(encoded) > (
            self.settings.ai_cover_download_max_bytes * 4 // 3 + 16
        ):
            raise ExternalServiceError(
                "seedream_image_too_large",
                "Seedream 返回的图片超过大小限制",
                False,
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ExternalServiceError(
                "seedream_invalid_response",
                "Seedream 返回了无效的图片数据",
                True,
            ) from exc
        if (
            not content
            or len(content) > self.settings.ai_cover_download_max_bytes
        ):
            raise ExternalServiceError(
                "seedream_image_too_large",
                "Seedream 返回的图片为空或超过大小限制",
                False,
            )
        content_type = self._detect_content_type(content)
        return GeneratedCoverImage(
            content=content,
            content_type=content_type,
            provider_request_id=self._request_id(payload),
        )

    async def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        response_limit = (
            self.settings.ai_cover_download_max_bytes * 4 // 3
            + 2 * 1024 * 1024
        )
        last_error: ExternalServiceError | None = None
        for attempt in range(1, self.settings.external_retry_attempts + 1):
            try:
                async with self.client.stream(
                    "POST",
                    self.endpoint,
                    headers=self.headers,
                    json=body,
                ) as response:
                    if response.is_redirect:
                        raise ExternalServiceError(
                            "seedream_unexpected_redirect",
                            "Seedream 返回了未允许的重定向",
                            False,
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > response_limit:
                            raise ExternalServiceError(
                                "seedream_response_too_large",
                                "Seedream 响应超过大小限制",
                                False,
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if response.status_code != 200:
                        error = self._http_error(
                            response.status_code, content
                        )
                        if (
                            error.retryable
                            and attempt
                            < self.settings.external_retry_attempts
                        ):
                            last_error = error
                            await asyncio.sleep(2 ** (attempt - 1))
                            continue
                        raise error
                    try:
                        payload = json.loads(content)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ExternalServiceError(
                            "seedream_invalid_response",
                            "Seedream 返回了无效 JSON",
                            True,
                        ) from exc
                    if not isinstance(payload, dict):
                        raise ExternalServiceError(
                            "seedream_invalid_response",
                            "Seedream 返回了未知数据格式",
                            True,
                        )
                    request_id = (
                        response.headers.get("x-request-id")
                        or response.headers.get("x-tt-logid")
                    )
                    if request_id:
                        payload["_request_id"] = request_id
                    return payload
            except httpx.RequestError as exc:
                last_error = ExternalServiceError(
                    "seedream_request_failed",
                    "连接 Seedream 失败",
                    True,
                )
                if attempt >= self.settings.external_retry_attempts:
                    raise last_error from exc
                await asyncio.sleep(2 ** (attempt - 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_reference_url(value: str) -> None:
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ExternalServiceError(
                "seedream_reference_invalid",
                "Seedream 参考图片地址无效",
                False,
            ) from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path
            or parsed.fragment
        ):
            raise ExternalServiceError(
                "seedream_reference_invalid",
                "Seedream 参考图片地址无效",
                False,
            )

    @staticmethod
    def _detect_content_type(content: bytes) -> str:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if (
            len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        ):
            return "image/webp"
        raise ExternalServiceError(
            "seedream_invalid_image",
            "Seedream 返回了不支持的图片格式",
            False,
        )

    @staticmethod
    def _request_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("_request_id")
        return str(value)[:256] if value else None

    @classmethod
    def _http_error(
        cls, status_code: int, content: bytes
    ) -> ExternalServiceError:
        code = ""
        try:
            payload = json.loads(content)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        if code in SENSITIVE_OUTPUT_CODES:
            return ExternalServiceError(
                "ai_cover_moderation_rejected",
                "Seedream 因内容安全审核拒绝了这张参考图",
                False,
            )
        detail = cls._safe_provider_code(code)
        return ExternalServiceError(
            "seedream_request_failed",
            f"Seedream 请求失败（HTTP {status_code}）"
            + (f"，错误码 {detail}" if detail else ""),
            status_code == 429 or status_code >= 500,
        )

    @classmethod
    def _raise_provider_error(cls, error: dict[str, Any]) -> None:
        code = str(error.get("code") or "")
        if code in SENSITIVE_OUTPUT_CODES or "Sensitive" in code:
            raise ExternalServiceError(
                "ai_cover_moderation_rejected",
                "Seedream 因内容安全审核拒绝了这张参考图",
                False,
            )
        detail = cls._safe_provider_code(code)
        raise ExternalServiceError(
            "seedream_generation_failed",
            "Seedream 生成图片失败"
            + (f"（错误码 {detail}）" if detail else ""),
            False,
        )

    @staticmethod
    def _safe_provider_code(value: str) -> str:
        value = value[:80]
        return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else ""

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
