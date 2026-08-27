from __future__ import annotations

import asyncio
from pathlib import Path

import oss2

from ..config import Settings
from ..errors import ConfigurationError, ExternalServiceError


class OSSStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not (
            settings.aliyun_access_key_id
            and settings.aliyun_access_key_secret
            and settings.aliyun_oss_endpoint
            and settings.aliyun_oss_bucket
        ):
            raise ConfigurationError("阿里云 OSS 配置不完整")
        auth = oss2.Auth(
            settings.aliyun_access_key_id.get_secret_value(),
            settings.aliyun_access_key_secret.get_secret_value(),
        )
        self.bucket = oss2.Bucket(
            auth,
            settings.aliyun_oss_endpoint,
            settings.aliyun_oss_bucket,
        )
        self.signing_bucket = oss2.Bucket(
            auth,
            settings.aliyun_oss_public_endpoint
            or settings.aliyun_oss_endpoint,
            settings.aliyun_oss_bucket,
        )

    def object_key(self, job_id: str) -> str:
        prefix = self.settings.aliyun_oss_prefix.strip("/")
        return f"{prefix}/{job_id}/audio.mp3"

    def clip_object_key(self, job_id: str, filename: str) -> str:
        prefix = self.settings.aliyun_oss_clip_prefix.strip("/")
        return f"{prefix}/{job_id}/{filename}"

    def ai_cover_source_object_key(
        self, job_id: str, generation_id: str
    ) -> str:
        prefix = self.settings.aliyun_oss_prefix.strip("/")
        return (
            f"{prefix}/ai-cover-sources/{job_id}/"
            f"{generation_id}/source.png"
        )

    def ai_cover_object_key(
        self,
        job_id: str,
        generation_id: str,
        orientation: str,
        kind: str,
    ) -> str:
        prefix = self.settings.aliyun_oss_clip_prefix.strip("/")
        return (
            f"{prefix}/{job_id}/ai-covers/{generation_id}/"
            f"{orientation}-{kind}.png"
        )

    async def upload(self, path: Path, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.bucket.put_object_from_file, key, str(path)
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "oss_upload_failed",
                "上传临时音频到 OSS 失败",
                True,
            ) from exc

    async def signed_get_url(self, key: str) -> str:
        try:
            return await asyncio.to_thread(
                self.signing_bucket.sign_url,
                "GET",
                key,
                self.settings.aliyun_oss_signed_url_seconds,
                slash_safe=True,
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "oss_sign_failed",
                "生成 OSS 临时访问地址失败",
                True,
            ) from exc

    async def signed_ai_cover_source_url(self, key: str) -> str:
        try:
            return await asyncio.to_thread(
                self.signing_bucket.sign_url,
                "GET",
                key,
                self.settings.ai_cover_source_url_seconds,
                slash_safe=True,
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "ai_cover_source_sign_failed",
                "生成 AI 封面参考图临时地址失败",
                True,
            ) from exc

    async def upload_ai_cover_image(self, path: Path, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.bucket.put_object_from_file,
                key,
                str(path),
                headers={"Content-Type": "image/png"},
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "ai_cover_upload_failed",
                "上传 AI 封面图片到 OSS 失败",
                True,
            ) from exc

    async def download_ai_cover_image(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(
                self.bucket.get_object_to_file,
                key,
                str(path),
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "ai_cover_download_failed",
                "下载 AI 封面图片失败",
                True,
            ) from exc

    async def signed_ai_cover_url(self, key: str) -> str:
        try:
            return await asyncio.to_thread(
                self.signing_bucket.sign_url,
                "GET",
                key,
                self.settings.ai_cover_signed_url_seconds,
                slash_safe=True,
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "ai_cover_sign_failed",
                "生成 AI 封面下载地址失败",
                True,
            ) from exc

    async def upload_clip(
        self, path: Path, key: str, filename: str
    ) -> None:
        try:
            await asyncio.to_thread(
                self.bucket.put_object_from_file,
                key,
                str(path),
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Disposition": (
                        f'attachment; filename="{filename}"'
                    ),
                },
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "oss_clip_upload_failed",
                "上传视频片段到 OSS 失败",
                True,
            ) from exc

    async def signed_clip_url(self, key: str) -> str:
        try:
            return await asyncio.to_thread(
                self.signing_bucket.sign_url,
                "GET",
                key,
                self.settings.aliyun_oss_clip_signed_url_seconds,
                slash_safe=True,
            )
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "oss_clip_sign_failed",
                "生成视频片段下载地址失败",
                True,
            ) from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self.bucket.delete_object, key)
        except oss2.exceptions.OssError as exc:
            raise ExternalServiceError(
                "oss_cleanup_failed",
                "删除 OSS 临时音频失败",
                True,
            ) from exc
