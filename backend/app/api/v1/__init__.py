from fastapi import APIRouter
from app.api.v1.routes import health, portfolio, sector, timeseries, report, financial_statements, isp_alerts

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(portfolio.router)
api_router.include_router(sector.router)
api_router.include_router(timeseries.router)
api_router.include_router(report.router)
api_router.include_router(financial_statements.router)
api_router.include_router(isp_alerts.router, prefix="/isp", tags=["ISP Alerts"])
