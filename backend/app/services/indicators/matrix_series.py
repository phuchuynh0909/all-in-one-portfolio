"""
Matrix Series Indicator - Converted from PineScript
Original: http://www.wisestocktrader.com/indicators/2739-flower-indicator

Calculates the up/down oscillator lines based on price deviation
from EMA, normalized by standard deviation.
"""
import numpy as np
import vectorbt as vbt

def matrix_series(close: np.ndarray, high: np.ndarray, low: np.ndarray, price_period: int = 20, supResPeriod: int = 50, supResPercentage: int = 100, smoother: int = 5):
    Value1 = vbt.IndicatorFactory.from_talib('CCI').run(high, low, close, timeperiod=price_period).real
    Value2 = vbt.IndicatorFactory.from_talib('MAX').run(Value1, timeperiod=supResPeriod).real
    Value3 = vbt.IndicatorFactory.from_talib('MIN').run(Value1, timeperiod=supResPeriod).real
    Value4 = Value2 - Value3
    Value5 = Value4 * (supResPercentage / 100.0)

    ResistanceLine = Value3 + Value5
    SupportLine = Value2 - Value5

    # Signal line 
    ys1 = (high + low + close * 2) / 4.0
    # rk3 = vbt.MA.run(ys1, window=smoother, ewm=True).ma
    rk3 = vbt.IndicatorFactory.from_talib('EMA').run(ys1, timeperiod=smoother).real.to_numpy()
    rk4 = vbt.IndicatorFactory.from_talib('STDDEV').run(ys1, timeperiod=smoother, nbdev=1).real.to_numpy()
    rk5 = (ys1 - rk3) * 200.0 / rk4

    # rk6 = vbt.MA.run(rk5, window=smoother, ewm=True).ma
    rk6 = vbt.IndicatorFactory.from_talib('EMA').run(rk5, timeperiod=smoother).real
    UP_line = vbt.IndicatorFactory.from_talib('EMA').run(rk6, timeperiod=smoother).real
    DOWN_line = vbt.IndicatorFactory.from_talib('EMA').run(UP_line, timeperiod=smoother).real

    # Candle OHLC
    Hh = UP_line.where(UP_line < DOWN_line, DOWN_line)      # High = min(up, down)
    Ll = DOWN_line.where(UP_line < DOWN_line, UP_line)      # Low = max(up, down)  
    
    return Hh, Ll, SupportLine, ResistanceLine