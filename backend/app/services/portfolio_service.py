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
    RiskModel,
    ClosePositionRequest,
    ClosePositionResponse,
)
from app.services.stock_service import get_current_price
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pypfopt import EfficientFrontier, HRPOpt, risk_models, expected_returns, objective_functions, CLA, EfficientCVaR, black_litterman

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


def close_position(db: Session, request: ClosePositionRequest) -> ClosePositionResponse:
    """
    Close a position (partially or fully) and create a corresponding sell transaction.
    
    Args:
        db: Database session
        request: Close position request with details
        
    Returns:
        ClosePositionResponse with operation details
        
    Raises:
        ValueError: If position not found or insufficient quantity
    """
    # Get the position
    db_position = get_position(db, request.position_id)
    if not db_position:
        raise ValueError(f"Position with ID {request.position_id} not found")
    
    # Validate quantity
    if request.quantity_to_close > db_position.quantity:
        raise ValueError(
            f"Cannot close {request.quantity_to_close} shares. "
            f"Position only has {db_position.quantity} shares available."
        )
    
    # Calculate realized P/L
    purchase_value = db_position.purchase_price * request.quantity_to_close
    closing_value = request.closing_price * request.quantity_to_close
    realized_pl = closing_value - purchase_value - (request.fees or Decimal(0))
    realized_pl_pct = ((request.closing_price / db_position.purchase_price) - 1) * 100
    
    # Create sell transaction
    transaction_data = TransactionCreate(
        ticker=db_position.ticker,
        transaction_type="sell",
        quantity=request.quantity_to_close,
        price=db_position.purchase_price,
        close_price=request.closing_price,
        transaction_date=request.closing_date,
        fees=request.fees or Decimal(0),
        notes=request.notes or f"Position closure - Realized P/L: {realized_pl}"
    )
    
    db_transaction = create_transaction(db, transaction_data)
    
    # Update or delete position
    remaining_quantity = db_position.quantity - request.quantity_to_close
    position_updated = True
    
    if remaining_quantity == 0:
        # Delete position completely
        db.delete(db_position)
        position_updated = False
        remaining_quantity = None
    else:
        # Update position with remaining quantity
        db_position.quantity = remaining_quantity
    
    db.commit()
    
    return ClosePositionResponse(
        success=True,
        message=f"Successfully closed {request.quantity_to_close} shares of {db_position.ticker}",
        position_updated=position_updated,
        remaining_quantity=remaining_quantity,
        transaction_id=db_transaction.id,
        realized_pl=realized_pl,
        realized_pl_pct=realized_pl_pct
    )


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


def delete_transaction(db: Session, transaction_id: int) -> bool:
    db_transaction = get_transaction(db, transaction_id)
    if not db_transaction:
        return False

    db.delete(db_transaction)
    db.commit()
    return True


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
    
    # Calculate realized P/L from sell transactions
    total_realized_pl = _calculate_realized_pl(db)
    
    return PortfolioSummary(
        total_value=total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        total_realized_pl=total_realized_pl,
        positions=positions,
    )


def _calculate_realized_pl(db: Session) -> Decimal:
    """Calculate total realized profit/loss from all sell transactions."""
    # Get all sell transactions
    sell_transactions = (
        db.query(Transaction)
        .filter(Transaction.transaction_type == "sell")
        .all()
    )
    
    total_realized_pl = Decimal(0)
    
    for transaction in sell_transactions:
        # For sell transactions, realized P/L is calculated as:
        # (close_price - purchase_price) * quantity - fees
        # If close_price is None, we use the transaction price as both buy and sell price (no gain/loss)
        if transaction.close_price is not None:
            # Realized P/L = (selling_price - purchase_price) * quantity - fees
            selling_price = transaction.close_price
            purchase_price = transaction.price
            realized_pl = (selling_price - purchase_price) * transaction.quantity - (transaction.fees or Decimal(0))
        else:
            # If no close_price, assume no realized gain/loss, just deduct fees
            realized_pl = -(transaction.fees or Decimal(0))
        
        total_realized_pl += realized_pl
    
    return total_realized_pl


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


def _get_risk_model_function(risk_model: RiskModel):
    """Map risk model enum to the corresponding pypfopt risk_models function."""
    def ledoit_wolf_basic(prices):
        return risk_models.CovarianceShrinkage(prices).ledoit_wolf()
    
    def ledoit_wolf_constant_variance(prices):
        try:
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf(shrinkage_target="constant_variance")
        except (TypeError, ValueError):
            # Fallback to basic ledoit_wolf if shrinkage_target not supported
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf()
    
    def ledoit_wolf_single_factor(prices):
        try:
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf(shrinkage_target="single_factor")
        except (TypeError, ValueError):
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf()
    
    def ledoit_wolf_constant_correlation(prices):
        try:
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf(shrinkage_target="constant_correlation")
        except (TypeError, ValueError):
            return risk_models.CovarianceShrinkage(prices).ledoit_wolf()
    
    def oracle_approximating(prices):
        return risk_models.CovarianceShrinkage(prices).oracle_approximating()
    
    risk_model_mapping = {
        RiskModel.SAMPLE_COV: risk_models.sample_cov,
        RiskModel.SEMICOVARIANCE: risk_models.semicovariance,
        RiskModel.EXP_COV: risk_models.exp_cov,
        RiskModel.LEDOIT_WOLF: ledoit_wolf_basic,
        RiskModel.LEDOIT_WOLF_CONSTANT_VARIANCE: ledoit_wolf_constant_variance,
        RiskModel.LEDOIT_WOLF_SINGLE_FACTOR: ledoit_wolf_single_factor,
        RiskModel.LEDOIT_WOLF_CONSTANT_CORRELATION: ledoit_wolf_constant_correlation,
        RiskModel.ORACLE_APPROXIMATING: oracle_approximating,
    }
    return risk_model_mapping[risk_model]


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
    ## Calculate the covariance matrix using selected risk model
    risk_model_func = _get_risk_model_function(req.risk_model)
    S = risk_model_func(prices)
    

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
    elif req.method == OptimizationMethod.MIN_VOLATILITY:
        # Minimize portfolio volatility
        ef = EfficientFrontier(mu, S)
        ef.min_volatility()
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.MAX_QUADRATIC_UTILITY:
        # Maximize quadratic utility with given risk aversion
        if req.risk_aversion is None:
            raise ValueError("risk_aversion parameter is required for max_quadratic_utility method")
        ef = EfficientFrontier(mu, S)
        ef.max_quadratic_utility(risk_aversion=req.risk_aversion)
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.EFFICIENT_RISK:
        # Maximize return for given target risk
        if req.target_risk is None:
            raise ValueError("target_risk parameter is required for efficient_risk method")
        ef = EfficientFrontier(mu, S)
        ef.efficient_risk(target_volatility=req.target_risk)
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.EFFICIENT_RETURN:
        # Minimize risk for given target return
        if req.target_return is None:
            raise ValueError("target_return parameter is required for efficient_return method")
        ef = EfficientFrontier(mu, S)
        ef.efficient_return(target_return=req.target_return)
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.BLACK_LITTERMAN:
        # Black-Litterman optimization
        if req.risk_aversion is None:
            raise ValueError("risk_aversion parameter is required for black_litterman method")
        
        # Market capitalization weights (equilibrium portfolio)
        if req.market_caps:
            # Normalize market caps to get weights
            total_market_cap = sum(req.market_caps.values())
            market_cap_weights = {ticker: cap / total_market_cap for ticker, cap in req.market_caps.items()}
            # Ensure all tickers are present
            missing_tickers = set(req.tickers) - set(market_cap_weights.keys())
            if missing_tickers:
                # Assign small equal weights to missing tickers
                remaining_weight = 0.05
                equal_weight = remaining_weight / len(missing_tickers) if missing_tickers else 0
                # Scale down existing weights
                scale_factor = (1 - remaining_weight)
                market_cap_weights = {ticker: weight * scale_factor for ticker, weight in market_cap_weights.items()}
                for ticker in missing_tickers:
                    market_cap_weights[ticker] = equal_weight
            
            # Convert to pandas Series with correct order
            market_cap_weights = pd.Series([market_cap_weights.get(ticker, 1.0/len(req.tickers)) for ticker in req.tickers], 
                                         index=req.tickers)
        else:
            # Default to equal weights if no market caps provided
            market_cap_weights = pd.Series([1.0/len(req.tickers)] * len(req.tickers), index=req.tickers)
        
        # Calculate implied equilibrium returns
        implied_returns = black_litterman.market_implied_prior_returns(
            market_cap_weights, req.risk_aversion, S
        )
        
        # If views are provided, incorporate them
        if req.views and req.view_confidences:
            # Convert views to matrix format expected by Black-Litterman
            P = np.zeros((len(req.views), len(req.tickers)))
            Q = np.zeros(len(req.views))
            omega_diag = []
            
            for i, (view_ticker, view_return) in enumerate(req.views.items()):
                if view_ticker in req.tickers:
                    ticker_idx = req.tickers.index(view_ticker)
                    P[i, ticker_idx] = 1.0
                    Q[i] = view_return
                    # Use confidence if provided, otherwise default to 0.1
                    confidence = req.view_confidences.get(view_ticker, 0.1)
                    omega_diag.append(confidence)
            
            # Create omega matrix (uncertainty in views)
            omega = np.diag(omega_diag)
            
            # Apply Black-Litterman with views
            bl_returns, bl_cov = black_litterman.black_litterman(
                implied_returns, S, P, Q, omega
            )
            
            # Optimize with updated returns and covariance
            ef = EfficientFrontier(bl_returns, bl_cov)
        else:
            # Use implied returns without views
            ef = EfficientFrontier(implied_returns, S)
        
        # Optimize for maximum Sharpe ratio with Black-Litterman inputs
        ef.max_sharpe(risk_free_rate=req.risk_free_rate or 0.0)
        weights = ef.clean_weights()
        ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    else:  # Efficient Frontier max Sharpe (for both EFFICIENT_FRONTIER and MAX_SHARPE)
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
