from __future__ import annotations

import asyncio
import getpass
import os
import re

from pocket48_summarizer.clients.pocket48_auth import (
    Pocket48AuthClient,
    Pocket48DeviceIdentity,
    load_pa_generator,
    save_room_voice_credentials,
)
from pocket48_summarizer.config import Settings

LOGIN_CONFIRMATION = "I_UNDERSTAND_THIS_REQUESTS_ONE_SMS"
MOBILE_RE = re.compile(r"^[0-9]{6,20}$")
AREA_RE = re.compile(r"^[0-9]{1,4}$")
CODE_RE = re.compile(r"^[0-9]{4,8}$")


def read_private_value(prompt: str, pattern: re.Pattern[str]) -> str:
    value = getpass.getpass(prompt).strip()
    if not pattern.fullmatch(value):
        raise SystemExit("输入格式无效，未发送任何额外请求。")
    return value


async def run() -> None:
    if os.environ.get("P48_RUN_ROOM_VOICE_LOGIN") != LOGIN_CONFIRMATION:
        raise SystemExit(
            "Refusing SMS login. Set "
            f"P48_RUN_ROOM_VOICE_LOGIN={LOGIN_CONFIRMATION}."
        )

    print(
        "警告：口袋48当前为单活会话；本次短信登录可能让官方手机 App 退出。"
    )
    settings = Settings(enable_worker=False)
    area = getpass.getpass("Country/region code [86]: ").strip() or "86"
    if not AREA_RE.fullmatch(area):
        raise SystemExit("国家/地区代码格式无效，尚未发送请求。")
    mobile = read_private_value("Pocket48 mobile number: ", MOBILE_RE)
    identity = Pocket48DeviceIdentity.create()
    pa_generator = (
        None
        if settings.pocket48_voice_pa is not None
        else load_pa_generator(settings.pocket48_pa_signing_seed_path)
    )
    client = Pocket48AuthClient(
        settings,
        identity,
        pa=settings.pocket48_voice_pa,
        pa_provider=(
            pa_generator.generate if pa_generator is not None else None
        ),
    )
    try:
        result = await client.send_sms(mobile=mobile, area=area)
        if result.challenge is not None:
            print(f"Verification question: {result.challenge.question}")
            for index, option in enumerate(
                result.challenge.options, start=1
            ):
                print(f"  {index}. {option}")
            answer = getpass.getpass("Verification answer: ").strip()
            if not answer:
                raise SystemExit("验证答案为空，未请求短信。")
            result = await client.send_sms(
                mobile=mobile,
                area=area,
                challenge_answer=answer,
            )
        if not result.sent:
            raise SystemExit("口袋48未确认验证码已发送。")

        code = read_private_value("SMS code: ", CODE_RE)
        credentials = await client.login_by_code(
            mobile=mobile, code=code
        )
        save_room_voice_credentials(
            settings.pocket48_voice_credentials_path,
            credentials,
        )
        print(
            "登录成功。token 已写入权限为 0600 的本地文件："
            f"{settings.pocket48_voice_credentials_path}"
        )
        print("手机号和短信验证码未保存。")
    finally:
        await client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
