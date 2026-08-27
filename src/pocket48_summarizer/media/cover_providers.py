from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GeneratedCoverImage:
    content: bytes
    content_type: str
    provider_request_id: str | None = None
    provider_task_id: str | None = None


class CoverImageProvider(Protocol):
    async def generate(
        self,
        *,
        reference_image_url: str,
        prompt: str,
        width: int,
        height: int,
        seed: int | None,
    ) -> GeneratedCoverImage:
        ...

    async def close(self) -> None:
        ...
