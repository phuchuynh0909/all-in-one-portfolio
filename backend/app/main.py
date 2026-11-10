import asyncio
import time
from contextlib import suppress
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.core.logging_bridge import install_logging_bridge
from app.core.settings import settings
from app.api.deps import require_user
from app.api.v1 import api_router


def get_app() -> FastAPI:
    # Before anything else builds a logger: the tradingagents runner and the
    # vendored package log through stdlib logging, which reaches nothing until
    # this points it at loguru. See app/core/logging_bridge.py.
    install_logging_bridge()

    # Deny by default: every router mounted below is guarded, including any
    # added later. app/api/deps.py holds the exempt list.
    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        dependencies=[Depends(require_user)],
        openapi_url=None if settings.environment.lower() == "production" else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o) for o in settings.backend_cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_request_time(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "{method} {path} - {status} - {duration:.2f}ms",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration=duration_ms,
        )
        return response

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.on_event("startup")
    async def startup():
        logger.info("Initializing in-memory cache")
        backend = InMemoryBackend()
        FastAPICache.init(
            backend,
            prefix="fastapi-cache",
            expire=3600,  # Default expiration of 1 hour
        )
        logger.info(
            "Cache initialized successfully with backend: {}",
            backend.__class__.__name__,
        )
        async def dispatch_rag():
            from app.db.base import SessionLocal
            from app.services.rag_submissions import dispatch_next

            def dispatch_one():
                with SessionLocal() as db:
                    return dispatch_next(db)

            while True:
                try:
                    found = await run_in_threadpool(dispatch_one)
                except Exception:
                    logger.exception("RAG submission dispatcher failed")
                    found = False
                await asyncio.sleep(0.1 if found else 5)

        app.state.rag_dispatcher = asyncio.create_task(dispatch_rag())

    @app.on_event("shutdown")
    async def shutdown():
        app.state.rag_dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.rag_dispatcher
        # Analyses now outlive the request that started them, so leaving without
        # this strands worker threads mid-graph. Imported here so app startup
        # keeps paying no TradingAgents import cost.
        from app.services.tradingagents import jobs

        jobs.shutdown()

    return app


app = get_app()
