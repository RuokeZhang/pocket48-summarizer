from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pocket48_summarizer.clients.seedream import SeedreamClient
from pocket48_summarizer.config import Settings
from pocket48_summarizer.media.ai_covers import AI_COVER_PROMPT

CONFIRMATION = "I_UNDERSTAND_THIS_UPLOADS_AN_IMAGE_AND_COSTS_MONEY"


async def run_probe() -> None:
    if os.environ.get("P48_RUN_SEEDREAM_PROBE") != CONFIRMATION:
        raise SystemExit(
            "Set P48_RUN_SEEDREAM_PROBE="
            f"{CONFIRMATION} to run the paid probe."
        )
    reference_url = os.environ.get("AI_COVER_PROBE_IMAGE_URL", "").strip()
    if not reference_url:
        raise SystemExit("AI_COVER_PROBE_IMAGE_URL is required.")
    orientation = os.environ.get(
        "AI_COVER_PROBE_ORIENTATION", "landscape"
    ).strip()
    if orientation not in {"landscape", "four_three"}:
        raise SystemExit(
            "AI_COVER_PROBE_ORIENTATION must be landscape or four_three."
        )

    settings = Settings()
    missing = []
    if not settings.enable_ai_covers:
        missing.append("ENABLE_AI_COVERS")
    if not settings.ark_api_key:
        missing.append("ARK_API_KEY")
    if not settings.ark_seedream_model:
        missing.append("ARK_SEEDREAM_MODEL")
    if missing:
        raise SystemExit(
            "Missing AI cover configuration: " + ", ".join(missing)
        )
    if orientation == "landscape":
        width = settings.ai_cover_landscape_width
        height = settings.ai_cover_landscape_height
        ratio = "16:9"
    else:
        width = settings.ai_cover_four_three_width
        height = settings.ai_cover_four_three_height
        ratio = "4:3"

    client = SeedreamClient(settings)
    try:
        generated = await client.generate(
            reference_image_url=reference_url,
            prompt=AI_COVER_PROMPT.format(ratio=ratio),
            width=width,
            height=height,
            seed=None,
        )
    finally:
        await client.close()

    default_suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[generated.content_type]
    output = Path(
        os.environ.get(
            "AI_COVER_PROBE_OUTPUT",
            f"seedream-{orientation}-probe{default_suffix}",
        )
    ).expanduser()
    valid_suffixes = {
        "image/png": {".png"},
        "image/jpeg": {".jpg", ".jpeg"},
        "image/webp": {".webp"},
    }[generated.content_type]
    if output.suffix.casefold() not in valid_suffixes:
        output = output.with_suffix(default_suffix)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(generated.content)
    print(f"Saved {generated.content_type} result to {output}")
    print(f"Bytes: {len(generated.content)}")
    if generated.provider_request_id:
        print(f"Provider request ID: {generated.provider_request_id}")


if __name__ == "__main__":
    asyncio.run(run_probe())
