from __future__ import annotations

import asyncio
import os

from pocket48_summarizer.config import Settings
from pocket48_summarizer.room_voice_admin import ensure_reviewed_pa_seed

CONFIRMATION = "I_UNDERSTAND_THIS_IMPORTS_A_REVIEWED_PROTOCOL_CONSTANT"


async def run() -> None:
    if os.environ.get("P48_PROVISION_ROOM_VOICE_PA") != CONFIRMATION:
        raise SystemExit(
            "Refusing PA seed provisioning. Set "
            f"P48_PROVISION_ROOM_VOICE_PA={CONFIRMATION}."
        )

    settings = Settings(enable_worker=False)
    await ensure_reviewed_pa_seed(
        settings.pocket48_pa_signing_seed_path,
        force_download=True,
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
