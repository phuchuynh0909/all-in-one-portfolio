from datetime import date
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.schemas.portfolio import (
    PositionCreate,
    TransactionCreate,
    InvestmentAmountCreate,
    PortfolioSummary,
    OptimizationRequest,
    OptimizationResult,
    OptimizationMethod,
)
from app.services.stock_service import get_current_price
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pypfopt import EfficientFrontier, HRPOpt, risk_models, expected_returns, objective_functions, CLA, EfficientCVaR

from app.services.stock_service import _load_delta_stocks

def create_position(db: Session, position: PositionCreate) -> Position:
    db_position = Position(**position.model_dump())
    db.add(db_position)
    db.commit()
    db.refresh(db_position)
    return db_position


async def get_positions(db: Session) -> List[Position]:
    positions = db.query(Position).order_by(Position.ticker).all()
    
    # Get current prices for all positions
    for position in positions:
        current_price = await get_current_price(position.ticker)
        position.current_price = current_price if current_price is not None else position.purchase_price
    
    return positions


def get_position(db: Session, position_id: int) -> Optional[Position]:
    return db.query(Position).filter(Position.id == position_id).first()


def update_position(
    db: Session, position_id: int, position: PositionCreate
) -> Optional[Position]:
    db_position = get_position(db, position_id)
    if not db_position:
        return None

    for key, value in position.model_dump().items():
        setattr(db_position, key, value)

    db.commit()
    db.refresh(db_position)
    return db_position


def delete_position(db: Session, position_id: int) -> bool:
    db_position = get_position(db, position_id)
    if not db_position:
        return False

    db.delete(db_position)
    db.commit()
    return True


def create_transaction(db: Session, transaction: TransactionCreate) -> Transaction:
    db_transaction = Transaction(**transaction.model_dump())
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


def get_transactions(db: Session) -> List[Transaction]:
    return (
        db.query(Transaction)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )


def get_transaction(db: Session, transaction_id: int) -> Optional[Transaction]:
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()


def set_investment_amount(
    db: Session, investment: InvestmentAmountCreate
) -> InvestmentAmount:
    # Update or create investment amount
    db_investment = (
        db.query(InvestmentAmount)
        .order_by(InvestmentAmount.date.desc())
        .first()
    )

    if db_investment:
        for key, value in investment.model_dump().items():
            setattr(db_investment, key, value)
    else:
        db_investment = InvestmentAmount(**investment.model_dump())
        db.add(db_investment)

    db.commit()
    db.refresh(db_investment)
    return db_investment


def get_investment_amount(db: Session) -> Optional[InvestmentAmount]:
    return (
        db.query(InvestmentAmount)
        .order_by(InvestmentAmount.date.desc())
        .first()
    )


async def get_portfolio_summary(db: Session) -> PortfolioSummary:
    positions = await get_positions(db)
    
    total_value = Decimal(0)
    total_invested = Decimal(0)
    
    for pos in positions:
        pos_value = Decimal(str(pos.current_price)) * pos.quantity
        invested = pos.purchase_price * pos.quantity
        
        total_value += pos_value
        total_invested += invested
    
    total_profit_loss = total_value - total_invested
    total_profit_loss_pct = (
        ((total_value / total_invested) - 1) * 100
        if total_invested > 0
        else Decimal(0)
    )
    
    return PortfolioSummary(
        total_value=total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        positions=positions,
    )


def _load_price_history(db: Session, tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Load historical prices for given tickers from transactions table fallback.

    This is a basic implementation placeholder. In a real setup, you'd source
    OHLCV from your Delta Lake or an external provider. Here we compute daily
    price using latest known close_price or purchase price as proxy.
    """
    # Fallback: use latest prices from positions if available
    from app.db.models.portfolio import Position
    rows = (
        db.query(Position)
        .filter(Position.ticker.in_(tickers))
        .all()
    )
    # Build a dummy price matrix with constant prices over date range
    dates = pd.date_range(start=start, end=end, freq="B")
    data = {}
    for row in rows:
        price = float(row.current_price or row.purchase_price)
        data[row.ticker] = np.full(len(dates), price)
    df = pd.DataFrame(data, index=dates)
    df.index.name = "date"
    return df


def optimize_portfolio(db: Session, req: OptimizationRequest) -> OptimizationResult:
    ## default start date is 5 year ago
    if req.start_date is None:
        req.start_date = datetime.now() - timedelta(days=365 * 5)

    df = _load_delta_stocks(symbols=req.tickers, start=req.start_date, end=req.end_date)
    ## Pick date, close, symbol column
    df = df[['date', 'close', 'symbol']]
    ## Transform to a matrix of price
    prices = df.pivot(index='date', columns='symbol', values='close')
    ## Backfill missing values
    prices = prices.bfill().ffill()
    
    ## Calculate the expected returns
    mu = expected_returns.mean_historical_return(prices, frequency=252)
    ## Calculate the covariance matrix
    S = risk_models.sample_cov(prices)
    

    if req.method == OptimizationMethod.HRP:
        returns = prices.pct_change().dropna()
        hrp = HRPOpt(returns)
        weights = hrp.optimize()
        perf = hrp.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
        ret, vol, sharpe = perf
    elif req.method == OptimizationMethod.CVAR:
        returns = prices.pct_change().dropna()
        e_cvar = EfficientCVaR(mu, returns=returns, beta=0.95, weight_bounds=(0, 1))
        e_cvar.add_objective(objective_functions.L2_reg, gamma=0.1)

        w_min_cvar = e_cvar.min_cvar()
        weights = e_cvar.clean_weights()
        ret, vol = e_cvar.portfolio_performance(verbose=False)
        sharpe = 0
    elif req.method == OptimizationMethod.CLA:
        # Critical Line Algorithm for the entire efficient frontier
        cla = CLA(mu, S)
        # Get optimal weights for maximum Sharpe Ratio point
        weights = cla.max_sharpe()
        ret, vol, sharpe = cla.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    else:  # Efficient Frontier max Sharpe
        ef = EfficientFrontier(mu, S)
        ef.max_sharpe(risk_free_rate=req.risk_free_rate or 0.0)
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)

    return OptimizationResult(
        method=req.method,
        weights={k: float(v) for k, v in weights.items() if v > 0},
        expected_return=float(ret),
        volatility=float(vol),
        sharpe_ratio=float(sharpe),
    )
