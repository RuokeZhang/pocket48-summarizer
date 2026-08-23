from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Settings
from ..errors import AppError, ExternalServiceError
from ..models import ReplayMetadata
from ..security import MEDIA_HOSTS, SOURCE_HOSTS, validate_https_url

POCKET_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://h5.48.cn",
    "Referer": "https://h5.48.cn/",
    "User-Agent": "pocket48-summarizer/0.1",
}


class PocketUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(alias="userId")
    user_name: str = Field(alias="userName")


class PocketLiveContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    live_id: str = Field(alias="liveId")
    review: bool = False
    play_stream_path: str = Field(default="", alias="playStreamPath")
    msg_file_path: str = Field(default="", alias="msgFilePath")
    cover_path: str = Field(default="", alias="coverPath")
    title: str = ""
    ctime: str = ""
    user: PocketUser


class PocketEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: int
    success: bool
    message: str = ""
    content: PocketLiveContent | None = None


class Pocket48Client:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def resolve_replay(self, live_id: str) -> ReplayMetadata:
        endpoint = (
            self.settings.pocket_api_base_url.rstrip("/")
            + "/live/api/v1/live/getLiveOne"
        )
        response = await self._request_with_retry(
            "POST",
            endpoint,
            json={"liveId": live_id},
            headers=POCKET_HEADERS,
        )
        if len(response.content) > self.settings.max_api_response_bytes:
            raise ExternalServiceError(
                "pocket48_api_changed",
                "口袋48返回的数据超过允许大小",
                True,
            )
        try:
            envelope = PocketEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ExternalServiceError(
                "pocket48_api_changed",
                "口袋48返回了无法识别的数据格式",
                True,
            ) from exc
        if (
            response.status_code != 200
            or not envelope.success
            or envelope.status != 200
            or envelope.content is None
        ):
            raise ExternalServiceError(
                "pocket48_lookup_failed",
                envelope.message or "无法获取口袋48回放信息",
                response.status_code >= 500,
            )
        content = envelope.content
        if content.live_id != live_id:
            raise ExternalServiceError(
                "pocket48_live_id_mismatch",
                "口袋48返回的直播 ID 与请求不一致",
                False,
            )
        if not content.review or not content.play_stream_path:
            raise AppError(
                "replay_not_ready",
                "该链接不是可处理的已结束公开回放",
                True,
            )
        media_url = validate_https_url(
            content.play_stream_path,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        danmaku_url = None
        if content.msg_file_path:
            danmaku_url = validate_https_url(
                content.msg_file_path,
                SOURCE_HOSTS,
                code="invalid_danmaku_url",
                label="弹幕",
            )
        cover_url = None
        if content.cover_path:
            candidate = (
                content.cover_path
                if content.cover_path.startswith("https://")
                else urljoin("https://source.48.cn/", content.cover_path)
            )
            try:
                cover_url = validate_https_url(
                    candidate,
                    SOURCE_HOSTS,
                    code="invalid_cover_url",
                    label="封面",
                )
            except AppError:
                cover_url = None
        replay_started_at = None
        if content.ctime.isdigit():
            replay_started_at = datetime.fromtimestamp(
                int(content.ctime) / 1000, tz=UTC
            ).isoformat()
        return ReplayMetadata(
            live_id=content.live_id,
            member_id=content.user.user_id,
            member_name=content.user.user_name,
            title=content.title or "未命名直播",
            cover_url=cover_url,
            replay_started_at=replay_started_at,
            media_url=media_url,
            danmaku_url=danmaku_url,
        )

    async def fetch_danmaku(self, url: str) -> str:
        validate_https_url(
            url,
            SOURCE_HOSTS,
            code="invalid_danmaku_url",
            label="弹幕",
        )
        content = await self._bounded_get_with_retry(
            url,
            self.settings.max_danmaku_bytes,
            headers={"User-Agent": POCKET_HEADERS["User-Agent"]},
        )
        return content.decode("utf-8-sig", errors="replace")

    async def _request_with_retry(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.is_redirect:
                    raise ExternalServiceError(
                        "unexpected_redirect",
                        "外部服务返回了未允许的重定向",
                        False,
                    )
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = ExternalServiceError(
                    "external_service_unavailable",
                    f"外部服务暂时不可用（HTTP {response.status_code}）",
                    True,
                )
            except httpx.RequestError as exc:
                last_error = exc
            if attempt + 1 < self.settings.external_retry_attempts:
                await asyncio.sleep(0.5 * (2**attempt))
        if isinstance(last_error, AppError):
            raise last_error
        raise ExternalServiceError(
            "external_request_failed",
            "连接口袋48服务失败",
            True,
        ) from last_error

    async def _bounded_get_with_retry(
        self, url: str, limit: int, *, headers: dict[str, str]
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                async with self.client.stream(
                    "GET", url, headers=headers
                ) as response:
                    if response.is_redirect:
                        raise AppError(
                            "unexpected_redirect",
                            "外部服务返回了未允许的重定向",
                            False,
                        )
                    if response.status_code == 200:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > limit:
                                raise AppError(
                                    "danmaku_too_large",
                                    "弹幕文件超过允许大小",
                                    False,
                                )
                        return bytes(content)
                    retryable = (
                        response.status_code == 429
                        or response.status_code >= 500
                    )
                    last_error = ExternalServiceError(
                        "danmaku_download_failed",
                        f"弹幕下载失败（HTTP {response.status_code}）",
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
            "danmaku_download_failed", "连接弹幕服务失败", True
        ) from last_error
