from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from ..config import Settings
from ..errors import AppError, ExternalServiceError
from ..security import MEDIA_HOSTS, validate_https_url

HLS_HEADERS = {
    "Origin": "https://h5.48.cn",
    "Referer": "https://h5.48.cn/",
    "User-Agent": "pocket48-summarizer/0.1",
}


@dataclass(frozen=True, slots=True)
class HLSManifest:
    url: str
    duration_ms: int
    segment_count: int


class HLSInspector:
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

    async def inspect(self, url: str, depth: int = 0) -> HLSManifest:
        if depth > 2:
            raise AppError(
                "hls_nested_too_deep",
                "HLS 播放列表嵌套层级过深",
                False,
            )
        validate_https_url(
            url, MEDIA_HOSTS, code="invalid_media_url", label="回放媒体"
        )
        try:
            async with self.client.stream(
                "GET", url, headers=HLS_HEADERS
            ) as response:
                if response.is_redirect:
                    raise AppError(
                        "unexpected_redirect",
                        "HLS 播放列表返回了未允许的重定向",
                        False,
                    )
                if response.status_code != 200:
                    raise ExternalServiceError(
                        "manifest_download_failed",
                        f"HLS 播放列表下载失败（HTTP {response.status_code}）",
                        response.status_code >= 500,
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.settings.max_manifest_bytes:
                        raise AppError(
                            "manifest_too_large",
                            "HLS 播放列表超过允许大小",
                            False,
                        )
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "manifest_download_failed",
                "无法下载 HLS 播放列表",
                True,
            ) from exc
        text = bytes(content).decode("utf-8-sig", errors="replace")
        if not text.lstrip().startswith("#EXTM3U"):
            raise AppError(
                "invalid_hls_manifest",
                "回放地址没有返回有效的 HLS 播放列表",
                False,
            )
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                for candidate in lines[index + 1 :]:
                    if not candidate.startswith("#"):
                        variant = urljoin(url, candidate)
                        validate_https_url(
                            variant,
                            MEDIA_HOSTS,
                            code="invalid_media_url",
                            label="HLS 子播放列表",
                        )
                        return await self.inspect(variant, depth + 1)
                raise AppError(
                    "invalid_hls_manifest",
                    "HLS 主播放列表缺少子播放列表地址",
                    False,
                )
        if "#EXT-X-ENDLIST" not in lines:
            raise AppError(
                "replay_not_complete",
                "HLS 播放列表尚未结束，当前仅支持完整回放",
                True,
            )
        duration_seconds = 0.0
        segment_count = 0
        pending_duration: float | None = None
        for line in lines:
            if line.startswith("#EXTINF:"):
                try:
                    pending_duration = float(
                        line.removeprefix("#EXTINF:").split(",", 1)[0]
                    )
                except ValueError as exc:
                    raise AppError(
                        "invalid_hls_manifest",
                        "HLS 分片时长格式无效",
                        False,
                    ) from exc
                continue
            if line.startswith("#"):
                continue
            segment_url = urljoin(url, line)
            validate_https_url(
                segment_url,
                MEDIA_HOSTS,
                code="invalid_media_segment_url",
                label="HLS 分片",
            )
            segment_count += 1
            if segment_count > self.settings.max_hls_segments:
                raise AppError(
                    "too_many_hls_segments",
                    "HLS 分片数量超过允许上限",
                    False,
                )
            if pending_duration is not None:
                duration_seconds += pending_duration
                pending_duration = None
        if segment_count == 0 or duration_seconds <= 0:
            raise AppError(
                "invalid_hls_manifest",
                "HLS 播放列表不包含可处理的媒体分片",
                False,
            )
        if duration_seconds > self.settings.max_replay_hours * 3600:
            raise AppError(
                "replay_too_long",
                f"回放超过 {self.settings.max_replay_hours:g} 小时上限",
                False,
            )
        return HLSManifest(
            url=url,
            duration_ms=round(duration_seconds * 1000),
            segment_count=segment_count,
        )
