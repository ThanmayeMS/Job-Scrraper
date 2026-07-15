"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from jobradar.api.routers import admin, auth, cv, health, jobs, matches
from jobradar.config import settings
from jobradar.logging_config import configure_logging


def _frontend_dir() -> Path | None:
    for candidate in (Path("frontend"), Path(__file__).resolve().parents[2] / "frontend"):
        if candidate.exists():
            return candidate
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging("DEBUG" if settings.debug else "INFO")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        summary="Job aggregation, semantic matching, and LLM fit-scoring API.",
        lifespan=lifespan,
    )

    origins = (
        ["*"]
        if settings.cors_origins.strip() == "*"
        else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        health.router,
        auth.router,
        jobs.router,
        matches.router,
        cv.router,
        admin.router,
    ):
        app.include_router(router)

    frontend = _frontend_dir()
    if frontend is not None:
        # Mounted last so API routes (/api/*, /health, /docs) take precedence.
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")

    return app


app = create_app()
