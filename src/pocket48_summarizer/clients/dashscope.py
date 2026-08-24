from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..config import Settings
from ..errors import AppError, ConfigurationError, ExternalServiceError
from ..security import redact_url


class DashScopeClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        if not settings.dashscope_api_key:
            raise ConfigurationError("DASHSCOPE_API_KEY 未配置")
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                "Bearer " + self.settings.dashscope_api_key.get_secret_value()
            ),
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

    async def submit(self, audio_url: str) -> tuple[str, str]:
        endpoint = (
            self.settings.dashscope_base_url.rstrip("/")
            + "/api/v1/services/audio/asr/transcription"
        )
        parameters: dict[str, Any] = {
            "channel_id": [0],
            "language_hints": ["zh"],
        }
        if self.settings.dashscope_diarization_enabled:
            parameters["diarization_enabled"] = True
        response = await self._post(
            endpoint,
            json={
                "model": self.settings.dashscope_asr_model,
                "input": {"file_urls": [audio_url]},
                "parameters": parameters,
            },
        )
        output = response.get("output", {})
        task_id = output.get("task_id")
        status = output.get("task_status", "PENDING")
        if not isinstance(task_id, str) or not task_id:
            raise ExternalServiceError(
                "dashscope_submit_failed",
                "DashScope 没有返回任务 ID",
                True,
            )
        return task_id, str(status)

    async def wait_for_result(
        self,
        task_id: str,
        on_status=None,
        heartbeat=None,
    ) -> dict[str, Any]:
        endpoint = (
            self.settings.dashscope_base_url.rstrip("/")
            + f"/api/v1/tasks/{task_id}"
        )
        deadline = time.monotonic() + self.settings.dashscope_timeout_seconds
        while True:
            payload = await self._get_task(endpoint)
            output = payload.get("output", {})
            status = str(output.get("task_status", "UNKNOWN")).upper()
            if on_status:
                await on_status(status)
            if heartbeat:
                await heartbeat()
            if status == "SUCCEEDED":
                results = output.get("results", [])
                for result in results if isinstance(results, list) else []:
                    if (
                        isinstance(result, dict)
                        and result.get("subtask_status") == "SUCCEEDED"
                        and result.get("transcription_url")
                    ):
                        return await self.fetch_result(
                            str(result["transcription_url"])
                        )
                raise ExternalServiceError(
                    "dashscope_result_missing",
                    "DashScope 任务成功但没有返回识别结果地址",
                    True,
                )
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                message = output.get("message") or output.get("code") or status
                raise ExternalServiceError(
                    "dashscope_task_failed",
                    f"DashScope 识别任务失败：{message}",
                    status == "UNKNOWN",
                )
            if time.monotonic() >= deadline:
                raise ExternalServiceError(
                    "dashscope_timeout",
                    "DashScope 识别任务等待超时",
                    True,
                )
            await asyncio.sleep(self.settings.dashscope_poll_seconds)

    async def _get_task(self, url: str) -> dict[str, Any]:
        headers = {
            "Authorization": (
                "Bearer " + self.settings.dashscope_api_key.get_secret_value()
            )
        }
        try:
            response = await self.client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "dashscope_request_failed",
                "连接 DashScope 失败",
                True,
            ) from exc
        if response.is_redirect:
            raise AppError(
                "unexpected_redirect",
                "DashScope 返回了未允许的重定向",
                False,
            )
        if response.status_code != 200:
            detail = ""
            try:
                payload = response.json()
                detail = str(
                    payload.get("message") or payload.get("code") or ""
                )
            except ValueError:
                pass
            raise ExternalServiceError(
                "dashscope_request_failed",
                f"DashScope 请求失败（HTTP {response.status_code}）"
                + (f"：{detail}" if detail else ""),
                response.status_code == 429 or response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                "dashscope_invalid_response",
                f"DashScope 返回无效 JSON：{redact_url(url)}",
                True,
            ) from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError(
                "dashscope_invalid_response",
                "DashScope 返回未知数据格式",
                True,
            )
        return payload

    async def fetch_result(self, url: str) -> dict[str, Any]:
        try:
            response = await self.client.get(url)
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "dashscope_result_download_failed",
                "下载 DashScope 识别结果失败",
                True,
            ) from exc
        if response.is_redirect:
            raise AppError(
                "unexpected_redirect",
                "DashScope 结果地址返回了未允许的重定向",
                False,
            )
        if response.status_code != 200:
            raise ExternalServiceError(
                "dashscope_result_download_failed",
                f"下载 DashScope 识别结果失败（HTTP {response.status_code}）",
                response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                "invalid_asr_result",
                "DashScope 返回了无效的 JSON 结果",
                False,
            ) from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError(
                "invalid_asr_result",
                "DashScope 返回了未知结果格式",
                False,
            )
        return payload

    async def _post(self, url: str, **kwargs) -> dict[str, Any]:
        try:
            response = await self.client.post(
                url, headers=self.headers, **kwargs
            )
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "dashscope_request_failed",
                "连接 DashScope 失败",
                True,
            ) from exc
        if response.is_redirect:
            raise AppError(
                "unexpected_redirect",
                "DashScope 返回了未允许的重定向",
                False,
            )
        if response.status_code != 200:
            detail = ""
            try:
                payload = response.json()
                detail = str(
                    payload.get("message") or payload.get("code") or ""
                )
            except ValueError:
                pass
            raise ExternalServiceError(
                "dashscope_request_failed",
                f"DashScope 请求失败（HTTP {response.status_code}）"
                + (f"：{detail}" if detail else ""),
                response.status_code == 429 or response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                "dashscope_invalid_response",
                f"DashScope 返回无效 JSON：{redact_url(url)}",
                True,
            ) from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError(
                "dashscope_invalid_response",
                "DashScope 返回未知数据格式",
                True,
            )
        return payload
