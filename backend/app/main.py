from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from loguru import logger
from functools import wraps

from app.core.settings import settings
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.portfolio import router as portfolio_router
from app.api.v1.routes.sector import router as sector_router
from app.api.v1.routes.timeseries import router as timeseries_router
from app.api.v1.routes.report import router as report_router
from app.api.v1.routes.backtest import router as backtest_router


def get_app() -> FastAPI:
    app = FastAPI(title=settings.project_name, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o) for o in settings.backend_cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_prefix = settings.api_v1_prefix
    app.include_router(health_router, prefix=api_prefix)
    app.include_router(portfolio_router, prefix=api_prefix)
    app.include_router(sector_router, prefix=api_prefix)
    app.include_router(timeseries_router, prefix=api_prefix)
    app.include_router(report_router, prefix=api_prefix)
    app.include_router(backtest_router, prefix=api_prefix)

    # Create a custom cache decorator that logs hits and misses
    def cache_with_logging(**cache_kwargs):
        cache_decorator = cache(**cache_kwargs)
        
        def wrapper(func):
            @wraps(func)
            async def wrapped(*args, **kwargs):
                # Try to get from cache first
                cache_key = cache_kwargs.get('key_builder', FastAPICache.get_key_builder())(
                    func, *args, **kwargs
                )
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
            expire=3600  # Default expiration of 1 hour
        )
        logger.info("Cache initialized successfully with backend: {}", backend.__class__.__name__)

    return app


app = get_app()
