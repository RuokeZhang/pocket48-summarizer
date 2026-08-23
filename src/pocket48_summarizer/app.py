from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import Database
from .errors import AppError
from .repository import JobRepository
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
            services = build_services(settings, repository)
        else:
            services = ApplicationServices(repository=repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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
        if exc.retryable and exc.code.endswith("_not_ready"):
            status = 409
        if exc.code in {"configuration_error", "worker_unavailable"}:
            status = 503
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message}},
                status_code=status,
            )
        return app.state.templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"error": exc},
            status_code=status,
        )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    return app


app = create_app()
