from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import shutil
import uuid
from pathlib import Path

from ..clients.oss_store import OSSStore
from ..config import Settings
from ..errors import AppError
from ..models import AICoverAssetRecord, AICoverGenerationRecord
from ..repository import JobRepository
from .cover_providers import CoverImageProvider
from .ffmpeg import FFmpegRunner
from .overlays import (
    normalize_ai_cover_extra_text,
    normalize_ai_cover_highlight,
    normalize_ai_cover_layout_style,
    normalize_ai_cover_title,
)

AI_COVER_PROMPT_MAX_LENGTH = 4000

DEFAULT_AI_COVER_PROMPT = """
以提供的直播画面为主体进行再创作，保留人物的五官、表情、姿态、发型、服装，
以及可辨认的原始房间，不要改变长相或身体比例。输出一张完整的横版 {ratio} 封面。
人物完整位于画面左侧，整张脸、头发、下巴和肩膀都不被裁切、不被遮挡。
画面右半侧是原房间的自然延伸，视觉干净、细节少，留出放标题的空间。
保持原场景的配色与主色调，不要默认使用粉色，也不要更换主题色。
只做克制的专业修饰：自然曝光、白平衡校正、真实皮肤质感、轻微调色。
不要添加舞台、幻想场景、花朵、粒子、闪光、翅膀、丝带、光束或繁复的播报图形。

在画面右半侧写出这行中文标题：{title}
标题使用粗壮有力的中文黑体，字号很大，占据右半侧的显著位置，
纯白色字体配深色描边或柔和阴影，保证在任何底色上都清晰可读。
文字必须完整、写法正确、不重复、不出现多余字符或乱码，并且不遮挡人物的脸。
除这行标题外，画面上不得出现任何其他文字、字母、数字、字幕、logo、签名或水印。
""".strip()


def normalize_ai_cover_prompt(value: str | None) -> str:
    if value is None:
        return DEFAULT_AI_COVER_PROMPT
    cleaned = "".join(
        character
        for character in value.replace("\r\n", "\n").replace("\r", "\n")
        if character == "\n" or character >= " "
    ).strip()
    if not cleaned:
        return DEFAULT_AI_COVER_PROMPT
    if len(cleaned) > AI_COVER_PROMPT_MAX_LENGTH:
        raise AppError(
            "ai_cover_prompt_too_long",
            f"提示词最多 {AI_COVER_PROMPT_MAX_LENGTH} 个字符",
            False,
        )
    return cleaned


def render_ai_cover_prompt(
    template: str,
    *,
    title: str,
    ratio: str,
) -> str:
    resolved = template.replace("{ratio}", ratio)
    if "{title}" in resolved:
        return resolved.replace("{title}", title)
    return resolved + f"\n\n画面上需要写出的文字内容是：{title}"


class AICoverService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        oss: OSSStore,
        provider: CoverImageProvider,
        ffmpeg: FFmpegRunner | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.oss = oss
        self.provider = provider
        self.ffmpeg = ffmpeg or FFmpegRunner(settings)
        self.output_dir = settings.data_dir / "ai-covers"
        self.logger = logging.getLogger(__name__)
        self._capacity = asyncio.Semaphore(settings.ai_cover_concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def startup(self) -> None:
        if self.output_dir.exists():
            try:
                await asyncio.to_thread(
                    shutil.rmtree, self.output_dir
                )
            except OSError:
                self.logger.exception(
                    "Failed to clean stale AI cover work directory"
                )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.repository.recover_running_ai_cover_generations()

    def start_generation(
        self,
        *,
        job_id: str,
        timeline_index: int,
        requested_by_user_id: str,
        request_id: str,
        source_timestamp_ms: int,
        title_text: str,
        extra_text: list[str],
        manifest_url: str,
        layout_style: str = "sticker_pop",
        highlight_text: str = "",
        prompt_template: str | None = None,
    ) -> AICoverGenerationRecord:
        style = normalize_ai_cover_layout_style(layout_style)
        prompt = normalize_ai_cover_prompt(prompt_template)
        title = normalize_ai_cover_title(title_text)
        highlight = normalize_ai_cover_highlight(highlight_text)
        extras = normalize_ai_cover_extra_text(extra_text)
        for existing in self.repository.list_ai_cover_generations(
            job_id,
            timeline_index=timeline_index,
            limit=100,
        ):
            if existing.status in {"queued", "running"}:
                if existing.request_id == request_id:
                    return existing
                raise AppError(
                    "ai_cover_already_running",
                    "当前时间线已有 AI 封面正在生成，请等待完成",
                    True,
                )
        generation_id = str(uuid.uuid4())
        generation, created = self.repository.begin_ai_cover_generation(
            generation_id=generation_id,
            job_id=job_id,
            timeline_index=timeline_index,
            requested_by_user_id=requested_by_user_id,
            request_id=request_id,
            source_timestamp_ms=source_timestamp_ms,
            provider=self.settings.ai_cover_provider,
            model=self.settings.ark_seedream_model or "",
            prompt_version=self.settings.ai_cover_prompt_version,
            prompt_template=prompt,
            shared_seed=secrets.randbelow(2_147_483_647),
            layout_style=style,
            title_text=title,
            highlight_text=highlight,
            extra_text=extras,
            landscape_size=(
                self.settings.ai_cover_landscape_width,
                self.settings.ai_cover_landscape_height,
            ),
            four_three_size=(
                self.settings.ai_cover_four_three_width,
                self.settings.ai_cover_four_three_height,
            ),
        )
        if created:
            self._start_generation_task(generation, manifest_url)
        return generation

    def retry_generation(
        self,
        *,
        job_id: str,
        generation_id: str,
        manifest_url: str,
    ) -> AICoverGenerationRecord:
        generation = self.repository.retry_ai_cover_generation(
            job_id, generation_id
        )
        current = self._tasks.get(generation.id)
        if current is None or current.done():
            self._start_generation_task(generation, manifest_url)
        return generation

    async def signed_download_url(
        self, asset: AICoverAssetRecord
    ) -> str:
        if asset.status != "completed" or not asset.final_oss_object_key:
            raise AppError(
                "ai_cover_not_ready",
                "AI 封面尚未生成完成",
                True,
            )
        return await self.oss.signed_ai_cover_url(
            asset.final_oss_object_key
        )

    def _start_generation_task(
        self,
        generation: AICoverGenerationRecord,
        manifest_url: str,
    ) -> None:
        self._tasks[generation.id] = asyncio.create_task(
            self._run_generation(generation, manifest_url)
        )

    async def _run_generation(
        self,
        generation: AICoverGenerationRecord,
        manifest_url: str,
    ) -> None:
        work_dir = self.output_dir / generation.job_id / generation.id
        source_path = work_dir / "source.png"
        source_key = self.oss.ai_cover_source_object_key(
            generation.job_id, generation.id
        )
        source_uploaded = False
        try:
            async with self._capacity:
                self.repository.mark_ai_cover_generation_running(
                    generation.id
                )
                assets = [
                    asset
                    for asset in self.repository.list_ai_cover_assets(
                        generation.id
                    )
                    if asset.status != "completed"
                ]
                source_url = ""
                if any(
                    not asset.background_oss_object_key
                    for asset in assets
                ):
                    await self.ffmpeg.extract_cover_source_frame(
                        manifest_url,
                        source_path,
                        generation.source_timestamp_ms,
                    )
                    await self.oss.upload_ai_cover_image(
                        source_path, source_key
                    )
                    source_uploaded = True
                    source_url = (
                        await self.oss.signed_ai_cover_source_url(
                            source_key
                        )
                    )
                for asset in assets:
                    await self._generate_asset(
                        generation,
                        asset,
                        source_url,
                        work_dir,
                    )
        except asyncio.CancelledError:
            self.repository.fail_ai_cover_generation(
                generation.id,
                "ai_cover_interrupted",
                "服务重启中，请重试 AI 封面",
            )
            raise
        except AppError as exc:
            self.repository.fail_ai_cover_generation(
                generation.id, exc.code, exc.message
            )
        except Exception:
            self.logger.exception(
                "Unexpected AI cover generation failure",
                extra={"generation_id": generation.id},
            )
            self.repository.fail_ai_cover_generation(
                generation.id,
                "ai_cover_failed",
                "AI 封面生成失败，请查看服务日志",
            )
        finally:
            source_path.unlink(missing_ok=True)
            for path in work_dir.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
            try:
                work_dir.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                self.logger.warning(
                    "AI cover work directory was not empty",
                    extra={"generation_id": generation.id},
                )
            if source_uploaded:
                try:
                    await self.oss.delete(source_key)
                except AppError:
                    self.logger.exception(
                        "Failed to delete AI cover source object",
                        extra={"generation_id": generation.id},
                    )
            self._tasks.pop(generation.id, None)

    async def _generate_asset(
        self,
        generation: AICoverGenerationRecord,
        asset: AICoverAssetRecord,
        source_url: str,
        work_dir: Path,
    ) -> None:
        self.repository.mark_ai_cover_asset_running(asset.id)
        provider_path = work_dir / f"{asset.orientation}-provider.image"
        background_path = work_dir / f"{asset.orientation}-background.png"
        try:
            background_key = asset.background_oss_object_key
            background_sha256 = asset.background_sha256
            provider_task_id = asset.provider_task_id
            provider_request_id = asset.provider_request_id
            if background_key:
                await self.oss.download_ai_cover_image(
                    background_key,
                    background_path,
                )
                downloaded_sha256 = self._sha256(background_path)
                if (
                    background_sha256
                    and downloaded_sha256 != background_sha256
                ):
                    raise AppError(
                        "ai_cover_background_changed",
                        "AI 封面背景校验失败，请重新生成",
                        False,
                    )
                background_sha256 = downloaded_sha256
            else:
                generated = await self.provider.generate(
                    reference_image_url=source_url,
                    prompt=render_ai_cover_prompt(
                        generation.prompt_template
                        or DEFAULT_AI_COVER_PROMPT,
                        title=generation.title_text,
                        ratio=(
                            "4:3"
                            if asset.orientation == "four_three"
                            else "16:9"
                        ),
                    ),
                    width=asset.width,
                    height=asset.height,
                    seed=generation.shared_seed,
                )
                provider_path.parent.mkdir(parents=True, exist_ok=True)
                provider_path.write_bytes(generated.content)
                await self.ffmpeg.normalize_cover_image(
                    provider_path,
                    background_path,
                    width=asset.width,
                    height=asset.height,
                )
                background_key = self.oss.ai_cover_object_key(
                    generation.job_id,
                    generation.id,
                    asset.orientation,
                    "background",
                )
                background_sha256 = self._sha256(background_path)
                provider_task_id = generated.provider_task_id
                provider_request_id = generated.provider_request_id
                await self.oss.upload_ai_cover_image(
                    background_path, background_key
                )
                self.repository.save_ai_cover_asset_background(
                    asset.id,
                    background_oss_object_key=background_key,
                    background_sha256=background_sha256,
                    provider_task_id=provider_task_id,
                    provider_request_id=provider_request_id,
                )
            if not background_key or not background_sha256:
                raise AppError(
                    "ai_cover_background_missing",
                    "AI 封面图片不存在，请重新生成",
                    False,
                )
            self.repository.complete_ai_cover_asset(
                asset.id,
                background_oss_object_key=background_key,
                final_oss_object_key=background_key,
                background_sha256=background_sha256,
                final_sha256=background_sha256,
                provider_task_id=provider_task_id,
                provider_request_id=provider_request_id,
            )
        except AppError as exc:
            self.repository.fail_ai_cover_asset(
                asset.id, exc.code, exc.message
            )
        except Exception:
            self.logger.exception(
                "Unexpected AI cover asset failure",
                extra={
                    "generation_id": generation.id,
                    "orientation": asset.orientation,
                },
            )
            self.repository.fail_ai_cover_asset(
                asset.id,
                "ai_cover_asset_failed",
                "AI 封面图片生成失败，请查看服务日志",
            )
        finally:
            for path in (provider_path, background_path):
                path.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.provider.close()
