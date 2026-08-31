from __future__ import annotations

import asyncio
import hashlib
import os
import re

import httpx
from pydantic import SecretStr

from pocket48_summarizer.clients.pocket48_auth import (
    PA_REFERENCE_SHA256,
    PA_REFERENCE_URL,
    save_pa_signing_seed,
)
from pocket48_summarizer.config import Settings
from pocket48_summarizer.errors import ExternalServiceError

CONFIRMATION = "I_UNDERSTAND_THIS_IMPORTS_A_REVIEWED_PROTOCOL_CONSTANT"
SEED_RE = re.compile(rb'paSecret\s*=\s*"([A-F0-9]{32})"')
MAX_REFERENCE_BYTES = 128 * 1024


async def run() -> None:
    if os.environ.get("P48_PROVISION_ROOM_VOICE_PA") != CONFIRMATION:
        raise SystemExit(
            "Refusing PA seed provisioning. Set "
            f"P48_PROVISION_ROOM_VOICE_PA={CONFIRMATION}."
        )

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=False
    ) as client:
        response = await client.get(PA_REFERENCE_URL)
    if response.status_code != 200 or response.is_redirect:
        raise ExternalServiceError(
            "pa_reference_download_failed",
            "无法下载固定版本的 pa 协议参考文件",
            True,
        )
    if len(response.content) > MAX_REFERENCE_BYTES:
        raise ExternalServiceError(
            "pa_reference_too_large",
            "pa 协议参考文件超过允许大小",
            False,
        )
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != PA_REFERENCE_SHA256:
        raise ExternalServiceError(
            "pa_reference_hash_mismatch",
            "pa 协议参考文件哈希不匹配，已拒绝导入",
            False,
        )
    matches = SEED_RE.findall(response.content)
    if len(matches) != 1:
        raise ExternalServiceError(
            "pa_reference_schema_changed",
            "固定版本参考文件中未找到唯一 pa 签名种子",
            False,
        )

    settings = Settings(enable_worker=False)
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr(matches[0].decode("ascii")),
    )
    print(
        "已验证固定提交和 SHA-256，并将 pa 签名种子写入权限为 0600 "
        f"的本地文件：{settings.pocket48_pa_signing_seed_path}"
    )
    print("签名种子未打印、未写入 Git，也不会在运行时再次联网获取。")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
