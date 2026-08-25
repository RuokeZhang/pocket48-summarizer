from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import unicodedata
from dataclasses import dataclass

from .clients.dashscope import DashScopeClient
from .config import Settings
from .errors import AppError, ConfigurationError, ExternalServiceError
from .repository import JobRepository, normalize_glossary_text
from .security import strip_control_chars

SUPPORTED_VOCABULARY_MODELS = {
    "paraformer-v2",
    "paraformer-8k-v2",
}


@dataclass(frozen=True, slots=True)
class ActiveVocabulary:
    vocabulary_id: str
    fingerprint: str


class VocabularyManager:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        dashscope: DashScopeClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.dashscope = dashscope
        self.logger = logging.getLogger(__name__)
        self._failed_fingerprint: str | None = None
        self._retry_after = 0.0
        self._last_error: tuple[str, str, bool] | None = None

    async def ensure_current(self) -> ActiveVocabulary | None:
        if not self.settings.dashscope_vocabulary_enabled:
            return None
        if (
            self.settings.dashscope_asr_model
            not in SUPPORTED_VOCABULARY_MODELS
        ):
            raise ConfigurationError(
                "DASHSCOPE_VOCABULARY_ENABLED=true 仅支持 "
                "paraformer-v2 或 paraformer-8k-v2"
            )

        vocabulary = self._build_vocabulary()
        if not vocabulary:
            raise ConfigurationError(
                "没有符合 DashScope 限制的有效成员或术语热词"
            )
        fingerprint = self._fingerprint(vocabulary)
        state = self.repository.get_glossary_sync_state()
        if (
            state.active_vocabulary_id
            and state.vocabulary_fingerprint == fingerprint
        ):
            return ActiveVocabulary(
                state.active_vocabulary_id,
                fingerprint,
            )

        previous_id = state.active_vocabulary_id
        previous_fingerprint = state.vocabulary_fingerprint or ""
        if (
            self._failed_fingerprint == fingerprint
            and time.monotonic() < self._retry_after
        ):
            if previous_id:
                return ActiveVocabulary(
                    previous_id,
                    previous_fingerprint,
                )
            if self._last_error:
                code, message, retryable = self._last_error
                raise ExternalServiceError(code, message, retryable)
        new_id: str | None = None
        try:
            new_id = await self.dashscope.create_vocabulary(
                prefix=self.settings.dashscope_vocabulary_prefix,
                target_model=self.settings.dashscope_asr_model,
                vocabulary=vocabulary,
            )
            await self._wait_until_ready(new_id)
        except AppError as exc:
            self._failed_fingerprint = fingerprint
            self._retry_after = time.monotonic() + 60
            self._last_error = (exc.code, exc.message, exc.retryable)
            if new_id:
                await self._delete_failed_build(new_id)
            self.repository.record_vocabulary_error(exc.message)
            if previous_id:
                self.logger.warning(
                    "Vocabulary rebuild failed; retaining %s: %s",
                    previous_id,
                    exc.message,
                )
                return ActiveVocabulary(
                    previous_id,
                    previous_fingerprint,
                )
            raise

        self.repository.activate_vocabulary(new_id, fingerprint)
        self._failed_fingerprint = None
        self._retry_after = 0.0
        self._last_error = None
        if previous_id and previous_id != new_id:
            try:
                await self.dashscope.delete_vocabulary(previous_id)
            except AppError as exc:
                message = (
                    "新热词列表已启用，但旧列表清理失败："
                    f"{exc.message}"
                )
                self.repository.record_vocabulary_error(message)
                self.logger.warning(message)
        return ActiveVocabulary(new_id, fingerprint)

    def _build_vocabulary(self) -> list[dict[str, object]]:
        vocabulary: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw_text in self.repository.list_active_vocabulary_texts():
            text = self._valid_hotword(raw_text)
            if text is None:
                continue
            _, normalized = normalize_glossary_text(text)
            if normalized in seen:
                continue
            seen.add(normalized)
            vocabulary.append(
                {
                    "text": text,
                    "weight": self.settings.dashscope_vocabulary_weight,
                    "lang": "zh",
                }
            )
            if (
                len(vocabulary)
                >= self.settings.dashscope_vocabulary_max_terms
            ):
                break
        return vocabulary

    @staticmethod
    def _valid_hotword(value: str) -> str | None:
        text = strip_control_chars(unicodedata.normalize("NFKC", value))
        if not text:
            return None
        if any(ord(character) > 127 for character in text):
            return text if len(text) <= 15 else None
        fragments = text.split()
        if not fragments or len(fragments) > 7:
            return None
        return text

    def _fingerprint(self, vocabulary: list[dict[str, object]]) -> str:
        payload = json.dumps(
            {
                "target_model": self.settings.dashscope_asr_model,
                "vocabulary": vocabulary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    async def _wait_until_ready(self, vocabulary_id: str) -> None:
        deadline = (
            time.monotonic()
            + self.settings.dashscope_vocabulary_ready_timeout_seconds
        )
        while True:
            output = await self.dashscope.query_vocabulary(vocabulary_id)
            status = str(output.get("status", "")).upper()
            if status == "OK":
                target_model = output.get("target_model")
                if target_model != self.settings.dashscope_asr_model:
                    raise ExternalServiceError(
                        "dashscope_vocabulary_model_mismatch",
                        "DashScope 热词列表模型与 ASR 模型不一致",
                        False,
                    )
                return
            if status in {"FAILED", "ERROR", "DELETED"}:
                raise ExternalServiceError(
                    "dashscope_vocabulary_failed",
                    f"DashScope 热词列表创建失败：{status}",
                    False,
                )
            if time.monotonic() >= deadline:
                raise ExternalServiceError(
                    "dashscope_vocabulary_timeout",
                    "等待 DashScope 热词列表就绪超时",
                    True,
                )
            await asyncio.sleep(2)

    async def _delete_failed_build(self, vocabulary_id: str) -> None:
        try:
            await self.dashscope.delete_vocabulary(vocabulary_id)
        except AppError as exc:
            self.logger.warning(
                "Failed to clean unusable vocabulary %s: %s",
                vocabulary_id,
                exc.message,
            )
