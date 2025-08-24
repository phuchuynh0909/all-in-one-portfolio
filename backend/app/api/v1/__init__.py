from fastapi import APIRouter
from app.api.v1.routes import health, portfolio, sector, timeseries, report

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(portfolio.router)
api_router.include_router(sector.router)
api_router.include_router(timeseries.router)
api_router.include_router(report.router)
