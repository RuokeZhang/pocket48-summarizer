from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel

from ..config import Settings
from ..errors import AppError, ConfigurationError, ExternalServiceError


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        if not (
            settings.llm_base_url
            and settings.llm_api_key
            and settings.llm_model
        ):
            raise ConfigurationError("OpenAI-compatible 模型 API 配置不完整")
        self.client = client or httpx.AsyncClient(
            timeout=settings.llm_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        try:
            extra_headers = json.loads(settings.llm_extra_headers_json)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "LLM_EXTRA_HEADERS_JSON 必须是 JSON 对象"
            ) from exc
        if not isinstance(extra_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in extra_headers.items()
        ):
            raise ConfigurationError(
                "LLM_EXTRA_HEADERS_JSON 只能包含字符串键值"
            )
        self.extra_headers: dict[str, str] = extra_headers

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel] | None = None,
    ) -> dict[str, Any]:
        endpoint = (
            self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        )
        headers = {
            "Authorization": (
                "Bearer " + self.settings.llm_api_key.get_secret_value()
            ),
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        body = {
            "model": self.settings.llm_model,
            "stream": False,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.settings.llm_response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.settings.llm_response_format == "json_schema":
            if response_model is None:
                raise ConfigurationError(
                    "LLM_RESPONSE_FORMAT=json_schema 需要响应模型"
                )
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": self._strict_json_schema(response_model),
                },
            }
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                response = await self.client.post(
                    endpoint, headers=headers, json=body
                )
                if response.is_redirect:
                    raise AppError(
                        "unexpected_redirect",
                        "模型 API 返回了未允许的重定向",
                        False,
                    )
                if response.status_code == 200:
                    try:
                        return self._parse_response(response)
                    except ExternalServiceError as exc:
                        retry_limit = (
                            self.settings.llm_truncation_retry_max_tokens
                        )
                        current_limit = int(body["max_tokens"])
                        if (
                            exc.code != "llm_output_truncated"
                            or current_limit >= retry_limit
                            or attempt + 1
                            >= self.settings.external_retry_attempts
                        ):
                            raise
                        body["max_tokens"] = retry_limit
                        last_error = exc
                retryable = (
                    response.status_code == 429
                    or response.status_code >= 500
                )
                if response.status_code != 200:
                    detail = self._safe_error_detail(response)
                    last_error = ExternalServiceError(
                        "llm_request_failed",
                        f"模型 API 请求失败（HTTP {response.status_code}）"
                        + (f"：{detail}" if detail else ""),
                        retryable,
                    )
                    if not retryable:
                        raise last_error
            except httpx.RequestError as exc:
                last_error = exc
            if attempt + 1 < self.settings.external_retry_attempts:
                await asyncio.sleep(0.5 * (2**attempt))
        if isinstance(last_error, AppError):
            raise last_error
        raise ExternalServiceError(
            "llm_request_failed",
            "连接模型 API 失败",
            True,
        ) from last_error

    @staticmethod
    def _strict_json_schema(
        response_model: type[BaseModel],
    ) -> dict[str, Any]:
        schema = response_model.model_json_schema()

        def require_defined_fields(node: Any) -> None:
            if isinstance(node, dict):
                properties = node.get("properties")
                if node.get("type") == "object" and isinstance(
                    properties, dict
                ):
                    node["additionalProperties"] = False
                    node["required"] = list(properties)
                for value in node.values():
                    require_defined_fields(value)
            elif isinstance(node, list):
                for value in node:
                    require_defined_fields(value)

        require_defined_fields(schema)
        return schema

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
            choice = payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError(
                "llm_invalid_response",
                "模型 API 返回了无法识别的数据",
                True,
            ) from exc
        if finish_reason == "length":
            raise ExternalServiceError(
                "llm_output_truncated",
                "模型输出达到长度限制，请提高 LLM_MAX_OUTPUT_TOKENS",
                True,
            )
        if finish_reason == "content_filter":
            raise ExternalServiceError(
                "llm_output_filtered",
                "模型输出被内容安全策略拦截",
                False,
            )
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise ExternalServiceError(
                "llm_invalid_response",
                "模型 API 没有返回文本内容",
                True,
            )
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise ExternalServiceError(
                    "llm_invalid_json",
                    "模型没有返回有效 JSON",
                    True,
                )
            try:
                parsed = json.loads(content[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ExternalServiceError(
                    "llm_invalid_json",
                    "模型没有返回有效 JSON",
                    True,
                ) from exc
        if not isinstance(parsed, dict):
            raise ExternalServiceError(
                "llm_invalid_json",
                "模型 JSON 顶层必须是对象",
                True,
            )
        return parsed

    @staticmethod
    def _safe_error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")[:300]
        return str(payload.get("message") or payload.get("code") or "")[:300]
