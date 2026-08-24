from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Settings
from ..errors import AppError
from ..models import MemberCatalogEntry
from ..security import (
    MEMBER_CATALOG_HOSTS,
    strip_control_chars,
    validate_https_url,
)

JSONP_RE = re.compile(
    r"\A\s*[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
    r"\s*\((.*)\)\s*;?\s*\Z",
    re.DOTALL,
)
DIGITS_RE = re.compile(r"^\d+$")


class OfficialMemberPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sid: str = Field(min_length=1, max_length=20)
    status: str = Field(min_length=1, max_length=10)
    gid: str = Field(default="", max_length=10)
    tid: str = Field(default="", max_length=10)
    sname: str = Field(min_length=1, max_length=80)
    ranking: str | int | None = Field(default=None)
    pinyin: str = Field(default="", max_length=160)
    gname: str = Field(default="", max_length=40)
    tname: str = Field(default="", max_length=80)


class OfficialMemberEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: str | int
    rows: list[OfficialMemberPayload]


class MemberCatalogClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.url = validate_https_url(
            settings.member_catalog_url,
            MEMBER_CATALOG_HOSTS,
            code="member_catalog_url_invalid",
            label="官方成员目录",
        )
        parsed_url = urlsplit(self.url)
        if (
            parsed_url.path
            != "/resource/jsonp/allmembers_simple.php"
            or parse_qs(parsed_url.query) != {"gid": ["00"]}
        ):
            raise AppError(
                "member_catalog_url_invalid",
                "官方成员目录地址不是受支持的固定端点",
                False,
            )
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=settings.member_catalog_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "Pocket48ReplaySummarizer/0.1"},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch_members(self) -> list[MemberCatalogEntry]:
        last_error: AppError | None = None
        for attempt in range(self.settings.member_catalog_retry_attempts):
            try:
                return await self._fetch_once()
            except AppError as exc:
                last_error = exc
                if (
                    not exc.retryable
                    or attempt + 1
                    >= self.settings.member_catalog_retry_attempts
                ):
                    raise
                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
        if last_error is not None:
            raise last_error
        raise AppError(
            "member_catalog_fetch_failed",
            "官方成员目录请求失败",
            True,
        )

    async def _fetch_once(self) -> list[MemberCatalogEntry]:
        try:
            async with self.client.stream("GET", self.url) as response:
                if response.status_code != 200:
                    retryable = (
                        response.status_code == 429
                        or response.status_code >= 500
                    )
                    raise AppError(
                        "member_catalog_http_error",
                        f"官方成员目录返回 HTTP {response.status_code}",
                        retryable,
                    )
                content_length = response.headers.get("content-length")
                if (
                    content_length
                    and content_length.isdigit()
                    and int(content_length)
                    > self.settings.member_catalog_max_response_bytes
                ):
                    raise AppError(
                        "member_catalog_response_too_large",
                        "官方成员目录响应超过大小限制",
                        False,
                    )
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if (
                        len(payload)
                        > self.settings.member_catalog_max_response_bytes
                    ):
                        raise AppError(
                            "member_catalog_response_too_large",
                            "官方成员目录响应超过大小限制",
                            False,
                        )
        except AppError:
            raise
        except httpx.HTTPError as exc:
            raise AppError(
                "member_catalog_transport_error",
                "无法连接官方成员目录",
                True,
            ) from exc

        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError(
                "member_catalog_encoding_invalid",
                "官方成员目录不是有效的 UTF-8 数据",
                False,
            ) from exc
        return self._parse_payload(text)

    @staticmethod
    def _parse_payload(text: str) -> list[MemberCatalogEntry]:
        stripped = text.strip()
        if not stripped:
            raise AppError(
                "member_catalog_empty",
                "官方成员目录响应为空",
                False,
            )
        json_text = stripped
        if not stripped.startswith("{"):
            match = JSONP_RE.fullmatch(stripped)
            if match is None:
                raise AppError(
                    "member_catalog_jsonp_invalid",
                    "官方成员目录 JSONP 包装无效",
                    False,
                )
            json_text = match.group(1)
        try:
            raw = json.loads(json_text)
            envelope = OfficialMemberEnvelope.model_validate(raw)
            total = int(envelope.total)
        except (
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise AppError(
                "member_catalog_schema_invalid",
                "官方成员目录数据结构已变化",
                False,
            ) from exc
        if total != len(envelope.rows) or not envelope.rows:
            raise AppError(
                "member_catalog_count_invalid",
                "官方成员目录数量校验失败",
                False,
            )

        members: list[MemberCatalogEntry] = []
        seen_ids: set[str] = set()
        for item in envelope.rows:
            member_id = item.sid.strip()
            status = item.status.strip()
            if (
                not DIGITS_RE.fullmatch(member_id)
                or not DIGITS_RE.fullmatch(status)
                or member_id in seen_ids
            ):
                raise AppError(
                    "member_catalog_member_invalid",
                    "官方成员目录包含无效或重复的成员 ID",
                    False,
                )
            seen_ids.add(member_id)
            try:
                ranking = int(item.ranking or 0)
            except (TypeError, ValueError) as exc:
                raise AppError(
                    "member_catalog_ranking_invalid",
                    "官方成员目录包含无效排名",
                    False,
                ) from exc
            canonical_name = strip_control_chars(item.sname)
            if not canonical_name:
                raise AppError(
                    "member_catalog_name_invalid",
                    "官方成员目录包含空成员姓名",
                    False,
                )
            members.append(
                MemberCatalogEntry(
                    member_id=member_id,
                    canonical_name=canonical_name,
                    pinyin=strip_control_chars(item.pinyin),
                    group_id=strip_control_chars(item.gid),
                    group_name=strip_control_chars(item.gname),
                    team_id=strip_control_chars(item.tid),
                    team_name=strip_control_chars(item.tname),
                    status=status,
                    ranking=max(ranking, 0),
                    active=status == "99",
                )
            )
        return members
