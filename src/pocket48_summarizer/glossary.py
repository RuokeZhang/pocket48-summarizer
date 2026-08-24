from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from .clients.member_catalog import MemberCatalogClient
from .config import Settings
from .errors import AppError
from .models import GlossarySyncStateRecord
from .repository import JobRepository


class MemberCatalogService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        client: MemberCatalogClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client

    async def sync_if_due(
        self, *, force: bool = False
    ) -> GlossarySyncStateRecord:
        state = self.repository.get_glossary_sync_state()
        if not force and not self._is_due(state):
            return state
        try:
            members = await self.client.fetch_members()
        except AppError as exc:
            self.repository.record_member_catalog_sync_failure(exc.message)
            raise
        serialized = json.dumps(
            [
                member.model_dump(mode="json")
                for member in sorted(
                    members, key=lambda member: member.member_id
                )
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        source_hash = hashlib.sha256(serialized).hexdigest()
        return self.repository.replace_member_catalog(
            members,
            source_url=self.settings.member_catalog_url,
            source_hash=source_hash,
        )

    def _is_due(self, state: GlossarySyncStateRecord) -> bool:
        if not state.last_attempt_at:
            return True
        try:
            last_attempt = datetime.fromisoformat(state.last_attempt_at)
        except ValueError:
            return True
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=UTC)
        interval = self.settings.member_catalog_sync_interval_seconds
        if state.sync_status == "failed":
            interval = min(interval, 15 * 60)
        return datetime.now(UTC) >= last_attempt + timedelta(
            seconds=interval
        )
