from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
import warnings
from scipy import linalg
from sklearn.preprocessing import StandardScaler

from pypfopt import (
    EfficientFrontier, 
    HRPOpt, 
    risk_models, 
    expected_returns, 
    objective_functions, 
    CLA, 
    EfficientCVaR, 
    black_litterman
)

from app.schemas.portfolio import (
    OptimizationRequest,
    OptimizationResult,
    OptimizationMethod,
    RiskModel,
    ReturnPredictionMethod,
)
from app.services.stock_service import _load_delta_stocks


class BayesianVAR:
    """
    Bayesian Vector Autoregression model for predicting expected returns.
    
    This implementation uses a Minnesota prior (Litterman prior) which is commonly
    used in BVAR models for financial time series.
    """
    
    def __init__(self, lags: int = 2, lambda_: float = 0.1, alpha: float = 2.0, 
                 theta: float = 0.5, forecast_periods: int = 1):
        """
        Initialize BVAR model.
        
        Args:
            lags: Number of lags to include
            lambda_: Overall tightness of the prior (0 = very tight, 1 = loose)
            alpha: Decay factor for lag coefficients
            theta: Tightness on cross-variable coefficients relative to own lags
            forecast_periods: Number of periods to forecast ahead
        """
        self.lags = lags
        self.lambda_ = lambda_
        self.alpha = alpha
        self.theta = theta
        self.forecast_periods = forecast_periods
        self.coefficients_ = None
        self.fitted_ = False
        self.variables_ = None
        self.scaler_ = None
        
    def _create_lag_matrix(self, data: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Create lagged variables matrix and target matrix."""
        n_vars = data.shape[1]
        n_obs = data.shape[0]
        
        if n_obs <= self.lags:
            raise ValueError(f"Not enough observations ({n_obs}) for {self.lags} lags")
        
        # Create lagged matrix
        X = []
        y = []
        
        for t in range(self.lags, n_obs):
            # Create lag features for time t
            lag_features = []
            for lag in range(1, self.lags + 1):
                lag_features.extend(data.iloc[t - lag].values)
            
            # Add constant term
            lag_features.append(1.0)
            X.append(lag_features)
            y.append(data.iloc[t].values)
        
        X_array = np.array(X)
        y_array = np.array(y)
        
        # Validation
        expected_features = n_vars * self.lags + 1  # +1 for constant
        if X_array.shape[1] != expected_features:
            raise ValueError(f"Feature matrix has wrong shape: {X_array.shape[1]} != {expected_features}")
        
        return X_array, y_array
    
    def _create_minnesota_prior(self, n_vars: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Create Minnesota (Litterman) prior for BVAR.
        
        The prior assumes:
        1. Each variable follows a random walk (own first lag coefficient = 1)
        2. Cross-variable effects are small
        3. Higher lags have smaller effects
        """
        n_coeffs = n_vars * self.lags + 1  # +1 for constant
        
        # Prior mean: random walk assumption
        prior_mean = np.zeros((n_vars, n_coeffs))
        
        # Set own first lag to 1 (random walk)
        for i in range(n_vars):
            prior_mean[i, i] = 1.0
        
        # Prior variance matrix
        prior_var = np.zeros((n_vars, n_coeffs))
        
        for i in range(n_vars):
            for j in range(n_vars):
                for lag in range(1, self.lags + 1):
                    coeff_idx = (lag - 1) * n_vars + j
                    
                    if i == j:  # Own lag
                        prior_var[i, coeff_idx] = (self.lambda_ / lag) ** self.alpha
                    else:  # Cross-variable lag
                        # Scale by relative variance of variables
                        prior_var[i, coeff_idx] = (self.lambda_ * self.theta / lag) ** self.alpha
            
            # Constant term
            prior_var[i, -1] = self.lambda_ ** 2
        
        return prior_mean, prior_var
    
    def fit(self, data: pd.DataFrame) -> 'BayesianVAR':
        """
        Fit the BVAR model to the data.
        
        Args:
            data: DataFrame with returns data (variables in columns, time in rows)
        """
        # Store variable names and standardize data
        self.variables_ = data.columns.tolist()
        self.scaler_ = StandardScaler()
        data_scaled = pd.DataFrame(
            self.scaler_.fit_transform(data),
            columns=data.columns,
            index=data.index
        )
        
        # Create lag matrices
        X, y = self._create_lag_matrix(data_scaled)
        n_vars = y.shape[1]
        
        # Get prior
        prior_mean, prior_var = self._create_minnesota_prior(n_vars)
        
        # Bayesian estimation for each equation
        self.coefficients_ = np.zeros((n_vars, X.shape[1]))
        
        for i in range(n_vars):
            # Prior precision matrix (inverse of variance)
            prior_precision = np.diag(1.0 / (prior_var[i] + 1e-8))
            
            # Posterior precision
            posterior_precision = prior_precision + X.T @ X
            
            # Posterior mean
            try:
                posterior_cov = linalg.inv(posterior_precision)
                posterior_mean = posterior_cov @ (prior_precision @ prior_mean[i] + X.T @ y[:, i])
                self.coefficients_[i] = posterior_mean
            except linalg.LinAlgError:
                # Fallback to OLS if inversion fails
                warnings.warn(f"Bayesian estimation failed for variable {i}, using OLS")
                self.coefficients_[i] = linalg.lstsq(X, y[:, i])[0]
        
        self.fitted_ = True
        return self
    
    def predict(self, data: pd.DataFrame, periods: int = None) -> pd.DataFrame:
        """
        Generate forecasts from the BVAR model.
        
        Args:
            data: Recent data for forecasting (should include at least 'lags' periods)
            periods: Number of periods to forecast (defaults to forecast_periods)
        
        Returns:
            DataFrame with forecasted returns
        """
        if not self.fitted_:
            raise ValueError("Model must be fitted before prediction")
        
        if periods is None:
            periods = self.forecast_periods
        
        # Standardize recent data
        data_scaled = pd.DataFrame(
            self.scaler_.transform(data),
            columns=data.columns,
            index=data.index
        )
        
        # Get the most recent observations for forecasting
        if len(data_scaled) < self.lags:
            raise ValueError(f"Need at least {self.lags} observations for prediction, got {len(data_scaled)}")
            
        recent_data = data_scaled.tail(self.lags).values.flatten()
        recent_data = np.append(recent_data, 1.0)  # Add constant
        
        # Validate state vector shape
        n_vars = len(self.variables_)
        expected_state_size = n_vars * self.lags + 1
        if len(recent_data) != expected_state_size:
            raise ValueError(f"State vector has wrong size: {len(recent_data)} != {expected_state_size}")
        
        forecasts = []
        current_state = recent_data.copy()
        
        for _ in range(periods):
            # Generate forecast for next period
            next_forecast = self.coefficients_ @ current_state
            forecasts.append(next_forecast)
            
            # Update state for next iteration if we need more forecasts
            if _ < periods - 1:  # Don't update state on last iteration
                n_vars = len(self.variables_)
                
                # Create new state vector: [newest_forecast, previous_lags..., constant]
                new_state = np.zeros(n_vars * self.lags + 1)
                
                # Add the new forecast as the most recent observation
                new_state[:n_vars] = next_forecast
                
                # Shift previous observations (exclude the oldest lag and constant)
                if self.lags > 1:
                    # Copy all but the oldest lag from current state
                    prev_obs_length = n_vars * (self.lags - 1)
                    new_state[n_vars:n_vars + prev_obs_length] = current_state[:prev_obs_length]
                
                # Keep constant term at the end
                new_state[-1] = 1.0
                current_state = new_state
        
        # Convert back to original scale
        forecasts_array = np.array(forecasts)
        forecasts_unscaled = self.scaler_.inverse_transform(forecasts_array)
        
        # Create result DataFrame
        forecast_index = pd.date_range(
            start=data.index[-1] + pd.Timedelta(days=1),
            periods=periods,
            freq='D'
        )
        
        return pd.DataFrame(
            forecasts_unscaled,
            columns=self.variables_,
            index=forecast_index
        )


def _predict_returns_bvar(prices: pd.DataFrame, forecast_periods: int = 21) -> pd.Series:
    """
    Use Bayesian VAR to predict expected returns.
    
    Args:
        prices: Historical price data
        forecast_periods: Number of days to forecast (default 21 for monthly)
    
    Returns:
        Series of predicted annualized returns for each asset
    """
    # Calculate returns
    returns = prices.pct_change().dropna()
    
    # Ensure we have enough data
    min_obs = 50
    if len(returns) < min_obs:
        warnings.warn(f"Insufficient data for BVAR ({len(returns)} < {min_obs}), using historical mean")
        return expected_returns.mean_historical_return(prices, frequency=252)
    
    try:
        # Adaptive lag selection with better bounds
        n_assets = len(returns.columns)
        max_lags = min(5, len(returns) // (n_assets * 2))  # More conservative lag selection
        lags = max(1, max_lags)  # Ensure at least 1 lag
        
        # Ensure we have enough observations for the selected lags
        if len(returns) < lags + 10:  # Need some buffer for stable estimation
            warnings.warn(f"Insufficient data for BVAR with {lags} lags, using historical mean")
            return expected_returns.mean_historical_return(prices, frequency=252)
        
        # Fit BVAR model
        bvar = BayesianVAR(
            lags=lags,
            lambda_=0.1,
            alpha=2.0,
            theta=0.5,
            forecast_periods=1
        )
        
        bvar.fit(returns)
        
        # Generate forecasts
        forecasts = bvar.predict(returns, periods=forecast_periods)
        
        # Calculate expected returns (mean of forecasts, annualized)
        expected_returns_pred = forecasts.mean() * 252
        
        # Sanity check: ensure all returns are finite
        if not np.all(np.isfinite(expected_returns_pred)):
            warnings.warn("BVAR produced non-finite returns, falling back to historical mean")
            return expected_returns.mean_historical_return(prices, frequency=252)
        
        return expected_returns_pred
        
    except Exception as e:
        warnings.warn(f"BVAR prediction failed: {str(e)}, falling back to historical mean")
        return expected_returns.mean_historical_return(prices, frequency=252)


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
    """
    Optimize portfolio allocation using various optimization methods.
    
    Args:
        db: Database session
        req: Optimization request with parameters
        
    Returns:
        OptimizationResult with weights and performance metrics
    """
    # Default start date is 5 years ago
    if req.start_date is None:
        req.start_date = datetime.now() - timedelta(days=365 * 5)

    # Load historical price data
    df = _load_delta_stocks(symbols=req.tickers, start=req.start_date, end=req.end_date)
    
    # Pick date, close, symbol columns and transform to price matrix
    df = df[['date', 'close', 'symbol']]
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices = prices.bfill().ffill()  # Backfill missing values
    
    # Calculate expected returns using the specified method
    if req.return_prediction_method == ReturnPredictionMethod.BVAR:
        mu = _predict_returns_bvar(prices, forecast_periods=req.bvar_forecast_periods or 21)
    else:
        mu = expected_returns.mean_historical_return(prices, frequency=252)
    
    # Calculate risk model
    risk_model_func = _get_risk_model_function(req.risk_model)
    S = risk_model_func(prices)
    
    # Apply optimization method
    if req.method == OptimizationMethod.HRP:
        weights, ret, vol, sharpe = _optimize_hrp(prices, req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.CVAR:
        weights, ret, vol, sharpe = _optimize_cvar(mu, prices)
    elif req.method == OptimizationMethod.CLA:
        weights, ret, vol, sharpe = _optimize_cla(mu, S, req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.MIN_VOLATILITY:
        weights, ret, vol, sharpe = _optimize_min_volatility(mu, S, req.risk_free_rate or 0.0)
    elif req.method == OptimizationMethod.MAX_QUADRATIC_UTILITY:
        weights, ret, vol, sharpe = _optimize_max_quadratic_utility(mu, S, req)
    elif req.method == OptimizationMethod.EFFICIENT_RISK:
        weights, ret, vol, sharpe = _optimize_efficient_risk(mu, S, req)
    elif req.method == OptimizationMethod.EFFICIENT_RETURN:
        weights, ret, vol, sharpe = _optimize_efficient_return(mu, S, req)
    elif req.method == OptimizationMethod.BLACK_LITTERMAN:
        weights, ret, vol, sharpe = _optimize_black_litterman(mu, S, req)
    else:  # Default to MAX_SHARPE/EFFICIENT_FRONTIER
        weights, ret, vol, sharpe = _optimize_max_sharpe(mu, S, req.risk_free_rate or 0.0)

    return OptimizationResult(
        method=req.method,
        weights={k: float(v) for k, v in weights.items() if v > 0},
        expected_return=float(ret),
        volatility=float(vol),
        sharpe_ratio=float(sharpe),
    )


def _optimize_hrp(prices: pd.DataFrame, risk_free_rate: float):
    """Hierarchical Risk Parity optimization."""
    returns = prices.pct_change().dropna()
    hrp = HRPOpt(returns)
    weights = hrp.optimize()
    perf = hrp.portfolio_performance(risk_free_rate=risk_free_rate)
    ret, vol, sharpe = perf
    return weights, ret, vol, sharpe


def _optimize_cvar(mu: pd.Series, prices: pd.DataFrame):
    """Conditional Value at Risk optimization."""
    returns = prices.pct_change().dropna()
    e_cvar = EfficientCVaR(mu, returns=returns, beta=0.95, weight_bounds=(0, 1))
    e_cvar.add_objective(objective_functions.L2_reg, gamma=0.1)
    
    w_min_cvar = e_cvar.min_cvar()
    weights = e_cvar.clean_weights()
    ret, vol = e_cvar.portfolio_performance(verbose=False)
    sharpe = 0  # CVaR doesn't calculate Sharpe ratio
    return weights, ret, vol, sharpe


def _optimize_cla(mu: pd.Series, S: pd.DataFrame, risk_free_rate: float):
    """Critical Line Algorithm optimization."""
    cla = CLA(mu, S)
    weights = cla.max_sharpe()
    ret, vol, sharpe = cla.portfolio_performance(risk_free_rate=risk_free_rate)
    return weights, ret, vol, sharpe


def _optimize_min_volatility(mu: pd.Series, S: pd.DataFrame, risk_free_rate: float):
    """Minimum volatility optimization."""
    ef = EfficientFrontier(mu, S)
    ef.min_volatility()
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=risk_free_rate)
    return weights, ret, vol, sharpe


def _optimize_max_quadratic_utility(mu: pd.Series, S: pd.DataFrame, req: OptimizationRequest):
    """Maximum quadratic utility optimization."""
    if req.risk_aversion is None:
        raise ValueError("risk_aversion parameter is required for max_quadratic_utility method")
    
    ef = EfficientFrontier(mu, S)
    ef.max_quadratic_utility(risk_aversion=req.risk_aversion)
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    return weights, ret, vol, sharpe


def _optimize_efficient_risk(mu: pd.Series, S: pd.DataFrame, req: OptimizationRequest):
    """Efficient risk optimization (maximize return for given target risk)."""
    if req.target_risk is None:
        raise ValueError("target_risk parameter is required for efficient_risk method")
    
    ef = EfficientFrontier(mu, S)
    ef.efficient_risk(target_volatility=req.target_risk)
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    return weights, ret, vol, sharpe


def _optimize_efficient_return(mu: pd.Series, S: pd.DataFrame, req: OptimizationRequest):
    """Efficient return optimization (minimize risk for given target return)."""
    if req.target_return is None:
        raise ValueError("target_return parameter is required for efficient_return method")
    
    ef = EfficientFrontier(mu, S)
    ef.efficient_return(target_return=req.target_return)
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    return weights, ret, vol, sharpe


def _optimize_black_litterman(mu: pd.Series, S: pd.DataFrame, req: OptimizationRequest):
    """Black-Litterman optimization."""
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
    # Posterior returns, not mu -- the views can move an asset either side of the rate.
    _require_an_asset_above_the_risk_free_rate(
        pd.Series(ef.expected_returns, index=ef.tickers), req.risk_free_rate or 0.0
    )
    ef.max_sharpe(risk_free_rate=req.risk_free_rate or 0.0)
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=req.risk_free_rate or 0.0)
    return weights, ret, vol, sharpe


def _require_an_asset_above_the_risk_free_rate(mu: pd.Series, risk_free_rate: float) -> None:
    """Guard the tangency portfolio, which is undefined when cash wins.

    pypfopt raises here too, but its message names neither the rate nor the
    closest asset, so the caller cannot tell whether to lower the rate, move the
    window, or switch method. A universe of holdings that all fell over the
    period reaches this legitimately -- it is bad input, not a server fault.

    Note the window advice cuts both ways: these holdings are all negative over
    5y and over 1y, but PAN and YEG are positive over 3y. A longer window is not
    the fix, a better-matched one is.
    """
    best = mu.max()
    if best > risk_free_rate:
        return
    raise ValueError(
        f"max_sharpe is undefined for these assets: none of the {len(mu)} has an "
        f"expected return above the risk-free rate of {risk_free_rate:.2%}. "
        f"The closest is {mu.idxmax()} at {best:.2%}. Lower the risk-free rate, "
        f"try a different date range, or optimise with min_volatility or hrp instead."
    )


def _optimize_max_sharpe(mu: pd.Series, S: pd.DataFrame, risk_free_rate: float):
    """Maximum Sharpe ratio optimization."""
    _require_an_asset_above_the_risk_free_rate(mu, risk_free_rate)
    ef = EfficientFrontier(mu, S)
    ef.max_sharpe(risk_free_rate=risk_free_rate)
    weights = ef.clean_weights()
    ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=risk_free_rate)
    return weights, ret, vol, sharpe
