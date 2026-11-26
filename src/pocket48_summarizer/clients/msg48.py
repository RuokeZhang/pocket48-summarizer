from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..config import Settings
from ..errors import ExternalServiceError

ARCHIVE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "pocket48-summarizer/0.1",
}
SEARCH_LIMIT = 10_000
AVATAR_HOSTS = {
    "source.48.cn", "www.snh48.com", "www.bej48.com", "www.gnz48.com",
    "www.shy48.com", "www.ckg48.com",
}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


class ArchiveNickname(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nickname: str = Field(min_length=1, max_length=64)
    first_used_ms: int = Field(alias="firstUsed", ge=0)


class ArchiveRoomInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_name: str = Field(default="", alias="ownerName", max_length=64)


class ArchiveMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    user_id: str
    nickname: str = Field(default="", max_length=64)
    timestamp_ms: int = Field(alias="timestamp", ge=0)
    msg_type: str = Field(default="", max_length=64)
    content: str = Field(default="", max_length=20_000)
    avatar: str = Field(default="", max_length=300)
    room_info: ArchiveRoomInfo = Field(default_factory=ArchiveRoomInfo)

    @field_validator("avatar", mode="before")
    @classmethod
    def blank_avatar(cls, value):
        return "" if value is None else value


class MemberIndexEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pocket_id: int = Field(alias="id", gt=0)
    name: str = Field(alias="ownerName", min_length=1, max_length=32)
    group_name: str = Field(default="", alias="groupName", max_length=32)
    team_name: str = Field(default="", alias="team", max_length=32)
    in_group: bool = Field(default=True, alias="isInGroup")
    pinyin: str = Field(default="", max_length=64)
    aliases: str = Field(default="", alias="nickname", max_length=200)
    avatar: str = Field(default="", max_length=300)

    @field_validator("group_name", "team_name", "pinyin", "aliases", "avatar", mode="before")
    @classmethod
    def blank_when_missing(cls, value):
        return "" if value is None else value


class Msg48Client:
    """Reads the public msg48.org room archive and the public member index."""

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

    async def user_nicknames(self, uid: str) -> list[ArchiveNickname]:
        url = f"{self.settings.msg48_base_url.rstrip('/')}/api/user-nicknames/{uid}"
        payload = await self._json("GET", url, self.settings.max_api_response_bytes)
        if not isinstance(payload, dict) or str(payload.get("userId", uid)) != uid:
            raise ExternalServiceError(
                "msg48_nicknames_invalid", "历史昵称接口返回格式异常", True
            )
        try:
            return [
                ArchiveNickname.model_validate(item)
                for item in payload.get("nicknames") or []
            ]
        except ValidationError as exc:
            raise ExternalServiceError(
                "msg48_nicknames_invalid", "历史昵称接口返回格式异常", True
            ) from exc

    async def user_messages(self, uid: str) -> tuple[list[ArchiveMessage], bool]:
        base_url = self.settings.msg48_base_url.rstrip("/")
        types_payload = await self._json(
            "GET", f"{base_url}/api/message-types",
            self.settings.max_api_response_bytes,
        )
        message_types = [
            str(item.get("value"))
            for item in (types_payload or {}).get("messageTypes") or []
            if isinstance(item, dict) and item.get("value")
        ]
        query = {
            "query": "", "search_type": "ik_max_phrase", "room_id": "",
            "idol_type": "", "team_filter": "", "msg_types": message_types,
            "user_id": uid, "nickname": "", "nickname_search_type": "ik_max_phrase",
            "start_date": "", "end_date": "", "limit": SEARCH_LIMIT,
            "sort_order": "asc",
        }
        payload = await self._json(
            "POST", f"{base_url}/api/search",
            self.settings.max_msg48_response_bytes, json_body=query,
        )
        rows = (payload or {}).get("results")
        if not isinstance(rows, list):
            raise ExternalServiceError(
                "msg48_search_invalid", "房间留言存档返回格式异常", True
            )
        messages = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            room_info = row.get("room_info")
            if isinstance(room_info, str):
                try:
                    room_info = json.loads(room_info)
                except ValueError:
                    room_info = {}
            try:
                message = ArchiveMessage.model_validate(
                    {**row, "room_info": room_info or {}}
                )
            except ValidationError:
                continue
            if message.user_id == uid:
                messages.append(message)
        return messages, len(rows) >= SEARCH_LIMIT

    async def member_index(self) -> list[MemberIndexEntry]:
        payload = await self._json(
            "GET", self.settings.member_index_url,
            self.settings.max_member_index_bytes,
        )
        rows = (payload or {}).get("roomId")
        if not isinstance(rows, list) or not rows:
            raise ExternalServiceError(
                "member_index_invalid", "成员名单返回格式异常", True
            )
        entries: dict[int, MemberIndexEntry] = {}
        for row in rows:
            try:
                entry = MemberIndexEntry.model_validate(row)
            except ValidationError:
                continue
            entries[entry.pocket_id] = entry
        if not entries:
            raise ExternalServiceError(
                "member_index_invalid", "成员名单返回格式异常", True
            )
        return list(entries.values())

    async def gift_price_table(self) -> dict:
        """Public gift price list maintained by the yaya_msg project."""
        payload = await self._json(
            "GET", self.settings.gift_price_url, self.settings.max_member_index_bytes
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("prices"), list) or not payload["prices"]:
            raise ExternalServiceError("gift_prices_invalid", "礼物价格返回格式异常", True)
        return build_gift_price_table(payload)

    async def fetch_avatar(self, url: str) -> bytes:
        """Download a profile picture from an official 48 Group image host."""
        if url.startswith("/"):
            url = "https://source.48.cn" + url
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or parsed.hostname not in AVATAR_HOSTS:
            raise ExternalServiceError("avatar_host_not_allowed", "头像地址不在允许范围内", False)
        url = parsed._replace(scheme="https").geturl()
        async with self.client.stream("GET", url, headers={"User-Agent": ARCHIVE_HEADERS["User-Agent"]}) as response:
            if response.status_code != 200:
                raise ExternalServiceError("avatar_download_failed", "头像下载失败", True)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_AVATAR_BYTES:
                    raise ExternalServiceError("avatar_too_large", "头像文件过大", False)
        return bytes(content)

    async def _json(
        self, method: str, url: str, limit: int, *, json_body: dict | None = None
    ):
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                async with self.client.stream(
                    method, url, headers=ARCHIVE_HEADERS, json=json_body
                ) as response:
                    if response.is_redirect:
                        raise ExternalServiceError(
                            "unexpected_redirect", "外部服务返回了未允许的重定向", False
                        )
                    if response.status_code == 200:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > limit:
                                raise ExternalServiceError(
                                    "archive_response_too_large",
                                    "公开存档返回的数据超过允许大小", False,
                                )
                        try:
                            return json.loads(bytes(content))
                        except ValueError as exc:
                            raise ExternalServiceError(
                                "archive_response_invalid",
                                "公开存档返回了无法识别的数据", True,
                            ) from exc
                    retryable = response.status_code == 429 or response.status_code >= 500
                    last_error = ExternalServiceError(
                        "archive_request_failed",
                        f"公开存档暂时不可用（HTTP {response.status_code}）",
                        retryable,
                    )
                    if not retryable:
                        raise last_error
            except httpx.RequestError as exc:
                last_error = exc
            if attempt + 1 < self.settings.external_retry_attempts:
                await asyncio.sleep(0.5 * (2**attempt))
        if isinstance(last_error, ExternalServiceError):
            raise last_error
        raise ExternalServiceError(
            "archive_request_failed", "连接公开存档失败", True
        ) from last_error


def build_gift_price_table(payload: dict) -> dict:
    """Reduce the public price list to {"prices": {name: 鸡腿}, "scores": {name: 分}}.

    Names that map to more than one price are left out because the danmaku
    only carries the name. Election (score) gifts are kept apart.
    """
    costs: dict[str, set[int]] = {}
    for row in payload.get("prices") or []:
        if not isinstance(row, dict):
            continue
        name, cost = str(row.get("name") or "").strip(), row.get("cost")
        if name and isinstance(cost, int) and cost >= 0:
            key = "荧光棒" if name.startswith("荧光棒") else name
            costs.setdefault(key, set()).add(cost)
    scores = {}
    for rule in payload.get("rules") or []:
        if isinstance(rule, dict) and rule.get("scorePerUnit") and rule.get("name"):
            try:
                scores[str(rule["name"]).strip()] = float(rule["scorePerUnit"])
            except ValueError:
                continue
    prices = {name: next(iter(values)) for name, values in costs.items() if len(values) == 1 and name not in scores}
    return {"prices": prices, "scores": scores}
