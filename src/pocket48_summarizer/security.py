from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .errors import AppError

CANONICAL_SHARE_PATH = "/2019appshare/memberLiveShare/index.html"
SUPPORTED_SHARE_PATHS = {
    CANONICAL_SHARE_PATH,
    "/2019appshare/memberLiveShare",
    "/2019appshare/memberLiveShare/",
}
POCKET_API_HOSTS = {"pocketapi.48.cn"}
MEDIA_HOSTS = {"idol-vod.48.cn"}
SOURCE_HOSTS = {"source.48.cn", "source3.48.cn"}
LIVE_ID_RE = re.compile(r"^[0-9]{6,30}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def parse_share_url(value: str) -> tuple[str, str]:
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise AppError(
            "invalid_share_url", "分享链接格式无效", False
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "h5.48.cn"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in SUPPORTED_SHARE_PATHS
        or parsed.fragment
    ):
        raise AppError(
            "unsupported_share_url",
            "仅支持 h5.48.cn 的公开成员直播分享链接",
            False,
        )
    query = parse_qs(parsed.query, keep_blank_values=True)
    ids = query.get("id", [])
    if len(ids) != 1 or not LIVE_ID_RE.fullmatch(ids[0]):
        raise AppError(
            "invalid_live_id", "分享链接缺少有效的直播 ID", False
        )
    normalized = urlunsplit(
        (
            "https",
            "h5.48.cn",
            CANONICAL_SHARE_PATH,
            f"id={ids[0]}",
            "",
        )
    )
    return normalized, ids[0]


def validate_https_url(
    value: str,
    allowed_hosts: set[str],
    *,
    code: str,
    label: str,
) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise AppError(code, f"{label}地址格式无效", False) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path
        or parsed.fragment
    ):
        raise AppError(code, f"{label}地址不在允许的主机范围内", False)
    return value


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def strip_control_chars(value: str) -> str:
    return CONTROL_CHARS_RE.sub("", value).strip()


def redact_signed_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_signed_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_signed_urls(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return value
        if parsed.scheme in {"http", "https"} and parsed.query:
            return redact_url(value)
    return value
