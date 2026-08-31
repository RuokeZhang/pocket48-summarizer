from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

from pydantic import SecretStr

from pocket48_summarizer.clients.pocket48_voice import (
    Pocket48VoiceClient,
    Pocket48VoiceCredentials,
)
from pocket48_summarizer.clients.pocket48_auth import (
    load_pa_generator,
    load_room_voice_credentials,
)
from pocket48_summarizer.config import Settings
from pocket48_summarizer.errors import ConfigurationError
from pocket48_summarizer.media.room_voice import RoomVoiceProbeRecorder

QUERY_CONFIRMATION = (
    "I_UNDERSTAND_THIS_USES_MY_PRIVATE_ACCOUNT"
)
RECORD_CONFIRMATION = (
    "I_UNDERSTAND_THIS_RECORDS_PRIVATE_AUDIO"
)


def require_secret(value: SecretStr | None, name: str) -> SecretStr:
    if value is None or not value.get_secret_value().strip():
        raise ConfigurationError(f"Missing room voice probe setting: {name}")
    return value


def require_text(value: str | None, name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ConfigurationError(f"Missing room voice probe setting: {name}")
    return normalized


def require_positive_int(value: str | None, name: str) -> int:
    normalized = (value or "").strip()
    if not normalized.isdigit() or int(normalized) <= 0:
        raise ConfigurationError(
            f"Missing or invalid room voice probe setting: {name}"
        )
    return int(normalized)


async def run() -> None:
    if os.environ.get("P48_RUN_ROOM_VOICE_PROBE") != QUERY_CONFIRMATION:
        raise SystemExit(
            "Refusing private-account probe. Set "
            f"P48_RUN_ROOM_VOICE_PROBE={QUERY_CONFIRMATION}."
        )
    record_confirmation = os.environ.get(
        "P48_RECORD_ROOM_VOICE_PROBE"
    )
    if record_confirmation and record_confirmation != RECORD_CONFIRMATION:
        raise SystemExit(
            "Refusing private-audio recording. Set "
            "P48_RECORD_ROOM_VOICE_PROBE="
            f"{RECORD_CONFIRMATION}."
        )

    settings = Settings(enable_worker=False)
    pa_generator = (
        None
        if settings.pocket48_voice_pa is not None
        else load_pa_generator(settings.pocket48_pa_signing_seed_path)
    )
    pa_provider = (
        pa_generator.generate if pa_generator is not None else None
    )
    if settings.pocket48_voice_token is not None:
        credentials = Pocket48VoiceCredentials(
            token=require_secret(
                settings.pocket48_voice_token,
                "POCKET48_VOICE_TOKEN",
            ),
            app_info=require_secret(
                settings.pocket48_voice_app_info,
                "POCKET48_VOICE_APP_INFO",
            ),
            user_agent=require_text(
                settings.pocket48_voice_user_agent,
                "POCKET48_VOICE_USER_AGENT",
            ),
            pa=settings.pocket48_voice_pa,
            pa_provider=pa_provider,
        )
    else:
        credentials = load_room_voice_credentials(
            settings.pocket48_voice_credentials_path,
            pa=settings.pocket48_voice_pa,
            pa_provider=pa_provider,
        )
    client = Pocket48VoiceClient(settings, credentials)
    try:
        channel_id_setting = settings.pocket48_voice_channel_id
        if not (channel_id_setting or "").strip():
            member_id = require_positive_int(
                settings.pocket48_voice_member_id,
                "POCKET48_VOICE_MEMBER_ID",
            )
            room = await client.resolve_member_room(member_id)
            print(
                json.dumps(
                    {
                        "action": "member_room",
                        "member_id": room.member_id,
                        "channel_id": room.channel_id,
                        "server_id": room.server_id,
                        "next": (
                            "Set POCKET48_VOICE_CHANNEL_ID and "
                            "POCKET48_VOICE_SERVER_ID, then rerun for the "
                            "one-shot voice status query."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        channel_id = require_positive_int(
            channel_id_setting, "POCKET48_VOICE_CHANNEL_ID"
        )
        server_id_setting = settings.pocket48_voice_server_id
        if not (server_id_setting or "").strip():
            server_id = await client.resolve_server_id(channel_id)
            print(
                json.dumps(
                    {
                        "action": "room_info",
                        "channel_id": channel_id,
                        "server_id": server_id,
                        "next": (
                            "Set POCKET48_VOICE_SERVER_ID to this value "
                            "and rerun for the one-shot voice status query."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        server_id = require_positive_int(
            server_id_setting, "POCKET48_VOICE_SERVER_ID"
        )
        status = await client.fetch_status(channel_id, server_id)
        summary = status.redacted_summary()
        allowed_hosts = settings.pocket48_voice_stream_host_list
        endpoint = status.stream_endpoint()
        summary["stream_host_approved"] = bool(
            endpoint is not None and endpoint[1] in allowed_hosts
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if record_confirmation == RECORD_CONFIRMATION:
            stream_url = status.require_recordable_stream_url(
                allowed_hosts
            )
            settings.prepare_directories()
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output_path = (
                settings.data_dir
                / "room-voice-probe"
                / f"room-voice-probe-{timestamp}.mp3"
            )
            recorder = RoomVoiceProbeRecorder(settings)
            await recorder.record(
                stream_url,
                output_path,
                duration_seconds=settings.pocket48_voice_probe_seconds,
                allowed_hosts=allowed_hosts,
            )
            print(
                json.dumps(
                    {
                        "recording": "completed",
                        "path": str(output_path),
                        "bytes": output_path.stat().st_size,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
