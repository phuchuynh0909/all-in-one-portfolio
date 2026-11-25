from fastapi import APIRouter

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
from app.api.v1.routes.broker import router as broker_router
from app.api.v1.routes.future import router as future_router
from app.api.v1.routes.cw import router as cw_router
from app.api.v1.routes.regime import router as regime_router
from app.api.v1.routes.trading_agents import router as trading_agents_router
from app.api.v1.routes.quote import router as quote_router
from app.api.v1.routes.mvf import router as mvf_router
from app.api.v1.routes.corporate_actions import router as corporate_actions_router
from app.api.v1.routes.daily_picks import router as daily_picks_router
from app.api.v1.routes.openscripts import router as openscripts_router
from app.api.v1.routes.chart_layouts import router as chart_layouts_router
from app.api.v1.routes.watchlists import router as watchlists_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(portfolio_router)
api_router.include_router(sector_router)
api_router.include_router(timeseries_router)
api_router.include_router(report_router)
api_router.include_router(backtest_router)
api_router.include_router(financial_router)
api_router.include_router(crawler_router)
api_router.include_router(scanner_router)
api_router.include_router(workflows_router)
api_router.include_router(
    isp_alerts_router, prefix="/isp", tags=["ISP Alerts"]
)
api_router.include_router(
    large_orders_router, tags=["Large Orders"]
)
api_router.include_router(trade_flow_router)
api_router.include_router(price_alerts_router)
api_router.include_router(chat_router)
api_router.include_router(auth_router)
api_router.include_router(broker_router)
api_router.include_router(future_router)
api_router.include_router(cw_router)
api_router.include_router(regime_router)
api_router.include_router(trading_agents_router)
api_router.include_router(quote_router)
api_router.include_router(mvf_router)
api_router.include_router(corporate_actions_router)
api_router.include_router(daily_picks_router)
api_router.include_router(openscripts_router)
api_router.include_router(chart_layouts_router)
api_router.include_router(watchlists_router)
