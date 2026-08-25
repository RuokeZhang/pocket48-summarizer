from __future__ import annotations

import ipaddress
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_release: str = "development"
    allow_remote_bind: bool = False
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    enable_worker: bool = True
    enable_clipper: bool = True
    data_dir: Path = Path("./data")
    maintenance_dir: Path | None = None
    database_name: str = "pocket48.sqlite3"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    auth_required: bool = False
    session_cookie_secure: bool = False
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    daily_job_limit: int = Field(default=3, ge=1, le=100)
    unlimited_job_usernames: str = "ruoke"
    session_cookie_name: str = "p48_session"
    csrf_cookie_name: str = "p48_csrf"

    pocket_api_base_url: str = "https://pocketapi.48.cn"
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    external_retry_attempts: int = Field(default=3, ge=1, le=8)
    max_api_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    member_catalog_url: str = (
        "https://h5.48.cn/resource/jsonp/"
        "allmembers_simple.php?gid=00"
    )
    member_catalog_sync_interval_seconds: int = Field(
        default=86400, ge=300
    )
    member_catalog_timeout_seconds: float = Field(default=20.0, gt=0)
    member_catalog_retry_attempts: int = Field(default=3, ge=1, le=5)
    member_catalog_max_response_bytes: int = Field(
        default=2 * 1024 * 1024, ge=1024
    )
    max_manifest_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    max_danmaku_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_hls_segments: int = Field(default=20_000, ge=1)
    hls_concurrent_fragments: int = Field(default=16, ge=1, le=32)
    max_replay_hours: float = Field(default=0.0, ge=0, le=168)
    max_clip_minutes: float = Field(default=10.0, gt=0, le=30)
    clip_concurrency: int = Field(default=2, ge=1, le=4)
    clip_retry_attempts: int = Field(default=3, ge=1, le=5)
    clip_retry_delay_seconds: float = Field(default=5.0, ge=0, le=60)
    clip_editor_context_minutes: float = Field(default=10.0, gt=0, le=60)
    clip_sentence_snap_threshold_ms: int = Field(
        default=1000, ge=100, le=5000
    )
    clip_silence_search_ms: int = Field(default=1500, ge=250, le=5000)
    clip_silence_noise_db: float = Field(default=-35.0, ge=-100, le=0)
    clip_silence_min_duration_ms: int = Field(
        default=200, ge=50, le=2000
    )
    clip_analysis_timeout_seconds: int = Field(
        default=45, ge=5, le=180
    )
    clip_analysis_concurrency: int = Field(default=2, ge=1, le=4)
    clip_font_name: str = "Noto Sans CJK SC"
    max_audio_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024)
    failed_audio_retention_hours: int = Field(default=24, ge=1, le=168)

    aliyun_access_key_id: SecretStr | None = None
    aliyun_access_key_secret: SecretStr | None = None
    aliyun_oss_endpoint: str | None = None
    aliyun_oss_public_endpoint: str | None = None
    aliyun_oss_bucket: str | None = None
    aliyun_oss_prefix: str = "pocket48-summarizer"
    aliyun_oss_signed_url_seconds: int = Field(default=7200, ge=300, le=86_400)
    aliyun_oss_clip_prefix: str = "pocket48-clips"
    aliyun_oss_clip_signed_url_seconds: int = Field(
        default=3600, ge=300, le=86_400
    )

    dashscope_api_key: SecretStr | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"
    dashscope_asr_model: str = "paraformer-v2"
    dashscope_poll_seconds: float = Field(default=5.0, ge=1, le=60)
    dashscope_timeout_seconds: int = Field(default=4 * 60 * 60, ge=60, le=12 * 60 * 60)
    dashscope_diarization_enabled: bool = False
    dashscope_vocabulary_enabled: bool = True
    dashscope_vocabulary_prefix: str = "p48vocab"
    dashscope_vocabulary_weight: int = Field(default=4, ge=1, le=5)
    dashscope_vocabulary_max_terms: int = Field(default=500, ge=1, le=500)
    dashscope_vocabulary_ready_timeout_seconds: int = Field(
        default=60, ge=10, le=300
    )

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    llm_max_input_chars: int = Field(default=12_000, ge=2_000, le=100_000)
    llm_max_output_tokens: int = Field(default=32_768, ge=512, le=65_536)
    llm_chunk_overlap_segments: int = Field(default=3, ge=0, le=20)
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_response_format: Literal[
        "none", "json_object", "json_schema"
    ] = "json_object"
    llm_schema_retry_attempts: int = Field(default=3, ge=1, le=5)
    llm_extra_headers_json: str = "{}"
    translation_max_input_chars: int = Field(
        default=10_000, ge=2_000, le=50_000
    )
    translation_retry_attempts: int = Field(default=3, ge=1, le=8)

    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=30)
    worker_lease_seconds: int = Field(default=120, ge=30, le=900)

    @model_validator(mode="after")
    def validate_bind_address(self) -> "Settings":
        if self.dashscope_vocabulary_enabled:
            prefix = self.dashscope_vocabulary_prefix
            if (
                not prefix
                or len(prefix) > 10
                or not prefix.isascii()
                or not prefix.isalnum()
                or prefix.lower() != prefix
            ):
                raise ValueError(
                    "DASHSCOPE_VOCABULARY_PREFIX must contain only "
                    "lowercase ASCII letters and digits and be at most "
                    "10 characters"
                )
        if self.auth_required and not self.session_cookie_secure:
            if self.app_host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError(
                    "SESSION_COOKIE_SECURE must be true when authentication "
                    "is enabled outside localhost"
                )
        if self.allow_remote_bind:
            return self
        if self.app_host == "localhost":
            return self
        try:
            address = ipaddress.ip_address(self.app_host)
        except ValueError as exc:
            raise ValueError(
                "APP_HOST must be a loopback IP or localhost unless "
                "ALLOW_REMOTE_BIND=true"
            ) from exc
        if not address.is_loopback:
            raise ValueError(
                "Refusing a non-loopback APP_HOST unless ALLOW_REMOTE_BIND=true"
            )
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def trusted_host_list(self) -> list[str]:
        hosts = [
            host.strip()
            for host in self.trusted_hosts.split(",")
            if host.strip()
        ]
        if not hosts:
            raise ConfigurationError("TRUSTED_HOSTS 至少需要一个主机名")
        return hosts

    @property
    def unlimited_job_username_set(self) -> set[str]:
        return {
            username.strip().casefold()
            for username in self.unlimited_job_usernames.split(",")
            if username.strip()
        }

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def clip_maintenance_path(self) -> Path:
        return (self.maintenance_dir or self.data_dir) / "clip-maintenance"

    @property
    def worker_maintenance_path(self) -> Path:
        return (self.maintenance_dir or self.data_dir) / "worker-maintenance"

    @property
    def clip_operation_lock_path(self) -> Path:
        return (self.maintenance_dir or self.data_dir) / "clip-operation.lock"

    @property
    def worker_operation_lock_path(self) -> Path:
        return (self.maintenance_dir or self.data_dir) / "worker-operation.lock"

    @property
    def worker_ready_path(self) -> Path:
        return (self.maintenance_dir or self.data_dir) / "worker-ready"

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        if self.maintenance_dir:
            self.maintenance_dir.mkdir(parents=True, exist_ok=True)

    def ffmpeg_executable(self) -> str | None:
        candidate = Path(self.ffmpeg_path).expanduser()
        if candidate.parent != Path("."):
            return str(candidate) if candidate.is_file() else None
        return shutil.which(self.ffmpeg_path)

    def ffprobe_executable(self) -> str | None:
        candidate = Path(self.ffprobe_path).expanduser()
        if candidate.parent != Path("."):
            return str(candidate) if candidate.is_file() else None
        return shutil.which(self.ffprobe_path)

    def missing_clip_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self.ffmpeg_executable():
            missing.append("FFMPEG_PATH")
        required: dict[str, Any] = {
            "ALIYUN_ACCESS_KEY_ID": self.aliyun_access_key_id,
            "ALIYUN_ACCESS_KEY_SECRET": self.aliyun_access_key_secret,
            "ALIYUN_OSS_ENDPOINT": self.aliyun_oss_endpoint,
            "ALIYUN_OSS_BUCKET": self.aliyun_oss_bucket,
        }
        missing.extend(name for name, value in required.items() if not value)
        return missing

    def missing_processing_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self.ffmpeg_executable():
            missing.append("FFMPEG_PATH")
        required: dict[str, Any] = {
            "ALIYUN_ACCESS_KEY_ID": self.aliyun_access_key_id,
            "ALIYUN_ACCESS_KEY_SECRET": self.aliyun_access_key_secret,
            "ALIYUN_OSS_ENDPOINT": self.aliyun_oss_endpoint,
            "ALIYUN_OSS_BUCKET": self.aliyun_oss_bucket,
            "DASHSCOPE_API_KEY": self.dashscope_api_key,
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_API_KEY": self.llm_api_key,
            "LLM_MODEL": self.llm_model,
        }
        missing.extend(name for name, value in required.items() if not value)
        return missing

    def require_processing_configuration(self) -> None:
        missing = self.missing_processing_configuration()
        if missing:
            raise ConfigurationError(
                "Missing processing configuration: " + ", ".join(sorted(missing))
            )
