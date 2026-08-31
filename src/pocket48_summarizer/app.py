from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .clients.member_catalog import MemberCatalogClient
from .clients.oss_store import OSSStore
from .clients.seedream import SeedreamClient
from .config import Settings
from .auth import AuthRepository, AuthService
from .db import Database
from .errors import AppError
from .glossary import MemberCatalogService
from .media.clips import VideoClipService
from .media.ai_covers import AICoverService
from .repository import JobRepository
from .room_voice_admin import RoomVoiceAdminService
from .routes import router
from .services import ApplicationServices, build_services


def create_app(
    settings: Settings | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.prepare_directories()
    database = Database(settings.database_path)
    database.initialize()
    repository = services.repository if services else JobRepository(database)
    if services is None:
        if settings.enable_worker and not settings.missing_processing_configuration():
            services = build_services(
                settings,
                repository,
                include_clipper=settings.enable_clipper,
            )
        else:
            services = ApplicationServices(repository=repository)
    if services.auth is None:
        services.auth = AuthService(settings, AuthRepository(database))
    if services.member_catalog is None:
        services.member_catalog_client = MemberCatalogClient(settings)
        services.member_catalog = MemberCatalogService(
            settings,
            repository,
            services.member_catalog_client,
        )
    if (
        settings.enable_clipper
        and services.clipper is None
        and not settings.missing_clip_configuration()
    ):
        services.clipper = VideoClipService(
            settings, repository, OSSStore(settings)
        )
    if (
        settings.enable_ai_covers
        and services.ai_covers is None
        and not settings.missing_ai_cover_configuration()
    ):
        services.ai_covers = AICoverService(
            settings,
            repository,
            OSSStore(settings),
            SeedreamClient(settings),
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if services.clipper:
            await services.clipper.startup()
        if services.ai_covers:
            await services.ai_covers.startup()
        if services.worker:
            await services.worker.start()
        yield
        await services.close()

    app = FastAPI(
        title="Pocket48 Replay Summarizer",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )
    package_dir = __import__(
        "pocket48_summarizer", fromlist=["__path__"]
    ).__path__[0]
    app.state.settings = settings
    app.state.services = services
    app.state.auth = services.auth
    app.state.room_voice_admin = RoomVoiceAdminService(settings)
    app.state.templates = Jinja2Templates(directory=f"{package_dir}/templates")
    app.mount(
        "/static",
        StaticFiles(directory=f"{package_dir}/static"),
        name="static",
    )
    app.include_router(router)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        status = 404 if exc.code == "job_not_found" else 400
        if exc.code == "authentication_required":
            status = 401
        if exc.code == "csrf_failed":
            status = 403
        if exc.code == "admin_required":
            status = 403
        if exc.code == "daily_quota_exceeded":
            status = 429
        if exc.code == "room_voice_sms_cooldown":
            status = 429
        if exc.retryable and exc.code.endswith("_not_ready"):
            status = 409
        if exc.code in {
            "ai_cover_configuration_missing",
            "ai_cover_maintenance",
            "clipper_maintenance",
            "configuration_error",
            "member_catalog_unavailable",
            "worker_unavailable",
        }:
            status = 503
        if exc.code in {"ai_cover_already_running", "ai_cover_not_ready"}:
            status = 409
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message}},
                status_code=status,
            )
        if exc.code == "authentication_required":
            return RedirectResponse("/login", status_code=303)
        return app.state.templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"error": exc},
            status_code=status,
        )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    return app


app = create_app()
