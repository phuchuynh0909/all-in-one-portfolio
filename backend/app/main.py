import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from loguru import logger
from functools import wraps

from app.core.logging_bridge import install_logging_bridge
from app.core.settings import settings
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.portfolio import router as portfolio_router
from app.api.v1.routes.sector import router as sector_router
from app.api.v1.routes.timeseries import router as timeseries_router
from app.api.v1.routes.report import router as report_router
from app.api.v1.routes.backtest import router as backtest_router
from app.api.v1.routes.financial_statements import router as financial_router
from app.api.v1.routes.data_crawler import router as crawler_router
from app.api.v1.routes.scanner import router as scanner_router
from app.api.v1.routes.workflows import router as workflows_router
from app.api.v1.routes.isp_alerts import router as isp_alerts_router
from app.api.v1.routes.large_orders import router as large_orders_router
from app.api.v1.routes.trade_flow import router as trade_flow_router
from app.api.v1.routes.price_alerts import router as price_alerts_router
from app.api.v1.routes.chat import router as chat_router
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.future import router as future_router
from app.api.v1.routes.cw import router as cw_router
from app.api.v1.routes.regime import router as regime_router
from app.api.v1.routes.trading_agents import router as trading_agents_router
from app.api.v1.routes.quote import router as quote_router
from app.api.v1.routes.mvf import router as mvf_router
from app.api.v1.routes.corporate_actions import router as corporate_actions_router


def get_app() -> FastAPI:
    # Before anything else builds a logger: the tradingagents runner and the
    # vendored package log through stdlib logging, which reaches nothing until
    # this points it at loguru. See app/core/logging_bridge.py.
    install_logging_bridge()

    app = FastAPI(title=settings.project_name, version="0.1.0")

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

    api_prefix = settings.api_v1_prefix
    app.include_router(health_router, prefix=api_prefix)
    app.include_router(portfolio_router, prefix=api_prefix)
    app.include_router(sector_router, prefix=api_prefix)
    app.include_router(timeseries_router, prefix=api_prefix)
    app.include_router(report_router, prefix=api_prefix)
    app.include_router(backtest_router, prefix=api_prefix)
    app.include_router(financial_router, prefix=api_prefix)
    app.include_router(crawler_router, prefix=api_prefix)
    app.include_router(scanner_router, prefix=api_prefix)
    app.include_router(workflows_router, prefix=api_prefix)
    app.include_router(
        isp_alerts_router, prefix=f"{api_prefix}/isp", tags=["ISP Alerts"]
    )
    app.include_router(
        large_orders_router, prefix=api_prefix, tags=["Large Orders"]
    )
    app.include_router(trade_flow_router, prefix=api_prefix)
    app.include_router(price_alerts_router, prefix=api_prefix)
    app.include_router(chat_router, prefix=api_prefix)
    app.include_router(auth_router, prefix=api_prefix)
    app.include_router(future_router, prefix=api_prefix)
    app.include_router(cw_router, prefix=api_prefix)
    app.include_router(regime_router, prefix=api_prefix)
    app.include_router(trading_agents_router, prefix=api_prefix)
    app.include_router(quote_router, prefix=api_prefix)
    app.include_router(mvf_router, prefix=api_prefix)
    app.include_router(corporate_actions_router, prefix=api_prefix)

    # Create a custom cache decorator that logs hits and misses
    def cache_with_logging(**cache_kwargs):
        cache_decorator = cache(**cache_kwargs)

        def wrapper(func):
            @wraps(func)
            async def wrapped(*args, **kwargs):
                # Try to get from cache first
                cache_key = cache_kwargs.get(
                    "key_builder", FastAPICache.get_key_builder()
                )(func, *args, **kwargs)
                try:
                    cached_value = await FastAPICache.get_backend().get(cache_key)
                    if cached_value is not None:
                        logger.info(f"Cache HIT for key: {cache_key}")
                        return cached_value
                    logger.info(f"Cache MISS for key: {cache_key}")
                except Exception as e:
                    logger.error(f"Cache error: {e}")

                # If not in cache, execute function
                return await cache_decorator(func)(*args, **kwargs)

            return wrapped

        return wrapper

    # Make the custom decorator available globally
    app.state.cache_with_logging = cache_with_logging

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

    @app.on_event("shutdown")
    async def shutdown():
        # Analyses now outlive the request that started them, so leaving without
        # this strands worker threads mid-graph. Imported here so app startup
        # keeps paying no TradingAgents import cost.
        from app.services.tradingagents import jobs

        jobs.shutdown()

    return app


app = get_app()
