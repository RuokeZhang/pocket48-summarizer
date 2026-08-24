from __future__ import annotations

from dataclasses import dataclass

from .auth import AuthService
from .clients.dashscope import DashScopeClient
from .clients.llm import OpenAICompatibleClient
from .clients.member_catalog import MemberCatalogClient
from .clients.oss_store import OSSStore
from .clients.pocket48 import Pocket48Client
from .config import Settings
from .glossary import MemberCatalogService
from .media.clips import VideoClipService
from .media.ffmpeg import FFmpegRunner
from .media.hls import HLSInspector
from .pipeline import ReplayPipeline
from .repository import JobRepository
from .summarization.service import SummarizationService
from .translation import SubtitleTranslationService
from .worker import DurableWorker


@dataclass(slots=True)
class ApplicationServices:
    repository: JobRepository
    auth: AuthService | None = None
    worker: DurableWorker | None = None
    clipper: VideoClipService | None = None
    pocket48: Pocket48Client | None = None
    hls: HLSInspector | None = None
    dashscope: DashScopeClient | None = None
    llm: OpenAICompatibleClient | None = None
    translator: SubtitleTranslationService | None = None
    member_catalog_client: MemberCatalogClient | None = None
    member_catalog: MemberCatalogService | None = None

    async def close(self) -> None:
        if self.worker:
            await self.worker.stop()
        if self.clipper:
            await self.clipper.close()
        for client in (
            self.pocket48,
            self.hls,
            self.dashscope,
            self.llm,
            self.member_catalog_client,
        ):
            if client is not None:
                await client.close()


def build_services(
    settings: Settings,
    repository: JobRepository,
    *,
    include_clipper: bool = True,
) -> ApplicationServices:
    settings.require_processing_configuration()
    pocket48 = Pocket48Client(settings)
    hls = HLSInspector(settings)
    dashscope = DashScopeClient(settings)
    llm = OpenAICompatibleClient(settings)
    member_catalog_client = MemberCatalogClient(settings)
    member_catalog = MemberCatalogService(
        settings, repository, member_catalog_client
    )
    oss = OSSStore(settings)
    summarizer = SummarizationService(settings, repository, llm)
    translator = SubtitleTranslationService(
        repository,
        llm,
        max_input_chars=settings.translation_max_input_chars,
    )
    pipeline = ReplayPipeline(
        settings=settings,
        repository=repository,
        pocket48=pocket48,
        hls=hls,
        ffmpeg=FFmpegRunner(settings),
        oss=oss,
        dashscope=dashscope,
        summarizer=summarizer,
    )
    worker = DurableWorker(
        settings,
        repository,
        pipeline,
        translator,
        member_catalog,
    )
    return ApplicationServices(
        repository=repository,
        worker=worker,
        clipper=(
            VideoClipService(settings, repository, oss)
            if include_clipper
            else None
        ),
        pocket48=pocket48,
        hls=hls,
        dashscope=dashscope,
        llm=llm,
        translator=translator,
        member_catalog_client=member_catalog_client,
        member_catalog=member_catalog,
    )
