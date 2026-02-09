import vectorbt as vbt
import numpy as np

def squeeze_ttm(close: np.ndarray, high: np.ndarray, low: np.ndarray, 
        bb_period: int = 20, bb_mult: float = 1.2, bb_matype: int = 3, 
        kc_period: int = 10, kc_mult: float = 1.2,
        donichan_period: int = 10, osc_smoothing_period: int = 10):
    
    bb_indicator = vbt.IndicatorFactory.from_talib('BBANDS')
    bb = bb_indicator.run(close, timeperiod=bb_period, nbdevup=bb_mult, nbdevdn=bb_mult, matype=bb_matype)

    atr_indicator = vbt.IndicatorFactory.from_talib('ATR')
    atr = atr_indicator.run(high, low, close, timeperiod=kc_period)
    ema_indicator = vbt.IndicatorFactory.from_talib('EMA')
    ema = ema_indicator.run(close, timeperiod=kc_period)
    kc = ema.real + kc_mult * atr.real

    diff = bb.upperband.vbt - kc

    sma_indicator = vbt.IndicatorFactory.from_talib('SMA')
    sma = sma_indicator.run(close, timeperiod=donichan_period).real
    MAX = vbt.IndicatorFactory.from_talib("MAX")
    hh = MAX.run(high, timeperiod=donichan_period).real
    ll = MAX.run(low, timeperiod=donichan_period).real
    mid = (hh + ll) / 2
    histogram = close - ((mid + sma) / 2)

    linear_reg_indicator = vbt.IndicatorFactory.from_talib('LINEARREG')
    ttms = linear_reg_indicator.run(histogram, timeperiod=osc_smoothing_period).real

    return diff, ttms