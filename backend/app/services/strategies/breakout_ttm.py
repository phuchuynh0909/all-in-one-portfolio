import vectorbt as vbt
import numpy as np
import pandas as pd
import numba as nb
from app.services.indicators import atr_trailing_nb, exrem_func_nb, williams_vix_fix_indicator


@nb.njit
def shift_numba(arr, num, fill_value=np.nan):
    result = np.empty_like(arr)
    if num > 0:
        result[:num] = fill_value
        result[num:] = arr[:-num]
    elif num < 0:
        result[num:] = fill_value
        result[:num] = arr[-num:]
    else:
        result[:] = arr
    return result


@nb.njit
def count_consecutive_neg_2d(arr):
    n, m = arr.shape
    out = np.zeros((n, m), dtype=np.int64)
    for j in range(m):
        count = 0
        for i in range(n):
            if arr[i, j] < 0:
                count += 1
                out[i, j] = count
            else:
                count = 0
    return out


class BreakoutTTMVersion2:

    def __init__(self, data: pd.DataFrame, 
                 bb_window: int = 16,
                 bb_multiplier: float = 1.0,
                 kc_window: int = 40,
                 kc_multiplier: float = 1.2,
                 atr_window: int = 14,
                 momentum_window: int = 12,
                 donichan_window: int = 12,
                 entry_version: str = 'v1',
                 kc_atr_period: int = 10,
                 osc_smoothing_period: int = 10,
                 matype: int = 0,
                 william_vix_period: int = 20,
                 consecutive_neg_threshold: int = 7):
        
        self.data = data
        self.bb_window = bb_window
        self.bb_multiplier = bb_multiplier
        self.kc_window = kc_window
        self.kc_multiplier = kc_multiplier
        self.atr_window = atr_window
        self.momentum_window = momentum_window
        self.donichan_window = donichan_window
        self.entry_version = entry_version
        self.kc_atr_period = kc_atr_period
        self.osc_smoothing_period = osc_smoothing_period
        self.matype = matype
        self.william_vix_period = william_vix_period
        self.consecutive_neg_threshold = consecutive_neg_threshold

    def get_entries(self):
        if self.entry_version == 'v3':
            return self._entries_v3()
        return self._entries_v1_v2()

    def _entries_v3(self):
        bb_indicator = vbt.IndicatorFactory.from_talib('BBANDS')
        bb = bb_indicator.run(
            self.data.close,
            timeperiod=self.bb_window,
            nbdevup=self.bb_multiplier,
            nbdevdn=self.bb_multiplier,
            matype=self.matype,
        )

        atr_indicator = vbt.IndicatorFactory.from_talib('ATR')
        atr = atr_indicator.run(self.data.high, self.data.low, self.data.close, timeperiod=self.kc_atr_period)
        ema_indicator = vbt.IndicatorFactory.from_talib('EMA')
        ema = ema_indicator.run(self.data.close, timeperiod=self.kc_window)
        kc = ema.real + self.kc_multiplier * atr.real

        squeeze_diff = bb.upperband.vbt - kc

        sma_indicator = vbt.IndicatorFactory.from_talib('SMA')
        sma = sma_indicator.run(self.data.close, timeperiod=self.donichan_window).real
        MAX = vbt.IndicatorFactory.from_talib("MAX")
        hh = MAX.run(self.data.high, timeperiod=self.donichan_window).real
        ll = MAX.run(self.data.low, timeperiod=self.donichan_window).real
        mid = (hh + ll) / 2
        histogram = self.data.close - ((mid + sma) / 2)

        linear_reg_indicator = vbt.IndicatorFactory.from_talib('LINEARREG')
        ttms = linear_reg_indicator.run(histogram, timeperiod=self.osc_smoothing_period).real

        squeeze_diff_np = squeeze_diff.to_numpy()
        ttms_np = ttms.to_numpy()
        consecutive_bar_ttm_np = count_consecutive_neg_2d(ttms_np)

        wvf, rangeHigh, filtered, williams_vix_fix_signal = williams_vix_fix_indicator(
            self.data.close,
            self.data.high,
            self.data.low,
            period=self.william_vix_period,
            mult=self.bb_multiplier,
            bbl=self.bb_window,
            lb=20,
            ph=0.9,
            ltLB=33,
            mtLB=10,
            strength_str=1,
        )

        entry_1 = (shift_numba(squeeze_diff_np, 1) < 0) & (squeeze_diff_np > 0) & (ttms > 0)
        entry_2 = (
            (shift_numba(squeeze_diff_np, 1) < 0)
            & (squeeze_diff_np > 0)
            & (consecutive_bar_ttm_np > self.consecutive_neg_threshold)
        )
        return entry_1 | entry_2 | williams_vix_fix_signal

    def _entries_v1_v2(self):
        bb = vbt.IndicatorFactory.from_ta("BollingerBands").run(
            self.data.close,
            window=self.bb_window,
            window_dev=self.bb_multiplier,
        )
        kc = vbt.IndicatorFactory.from_ta("KeltnerChannel").run(
            self.data.high,
            self.data.low,
            self.data.close,
            window=self.kc_window,
            window_atr=self.atr_window,
            multiplier=self.kc_multiplier,
            original_version=False,
        )

        sqz_on = (bb.bollinger_hband.vbt < kc.keltner_channel_hband.vbt) & (bb.bollinger_lband.vbt > kc.keltner_channel_lband.vbt)
        sqz_off = (bb.bollinger_hband.vbt > kc.keltner_channel_hband.vbt) & (bb.bollinger_lband.vbt < kc.keltner_channel_lband.vbt)
        no_sqz = (sqz_on == 0) & (sqz_off == 0)

        donchian_midline = (
            self.data.high.vbt.rolling_max(self.donichan_window)
            + self.data.low.vbt.rolling_min(self.donichan_window)
            + self.data.close.vbt.rolling_mean(self.donichan_window)
        ) / 3
        histogram = self.data.close - donchian_midline
        momentum = vbt.IndicatorFactory.from_talib("LINEARREG").run(histogram, timeperiod=self.momentum_window).real

        cond_2 = (momentum > momentum.vbt.fshift(1)) & (momentum.vbt.fshift(1).vbt.crossed_above(0))
        cond_3 = (momentum > momentum.vbt.fshift(2)) & (momentum.vbt.fshift(2).vbt.crossed_above(0))

        if self.entry_version == 'v2':
            return no_sqz.vbt & (momentum.vbt.crossed_above(0) | cond_2 | cond_3)
        return no_sqz.vbt & (momentum > 0)

    def get_exits(self, entries):
        atr = vbt.IndicatorFactory.from_talib("ATR").run(self.data.high, self.data.low, self.data.close, timeperiod=10)
        atr_scope = atr.real
        atr_trailing_indicator = vbt.IndicatorFactory(
            input_names=['close', 'atr'],
            param_names=['atr_multiplier'],
            output_names=['atr_trailing']
        ).from_apply_func(atr_trailing_nb)
        atr_sl = atr_trailing_indicator.run(self.data.close, atr_scope, atr_multiplier=1.9)
        exit1 = self.data.close.vbt.crossed_below(atr_sl.atr_trailing)

        new_entries = vbt.IndicatorFactory(
            input_names=['entries', 'exits'],
            output_names=['new_entries']
        ).from_apply_func(exrem_func_nb).run(entries, exit1).new_entries

        exit2 = self.data.close.vbt < atr_sl.atr_trailing.vbt

        exists = exit1
        # exists = exit1
        return exists
    
    def get_portfolio(self):
        entries = self.get_entries()
        exits = self.get_exits(entries)
        portfolio = vbt.Portfolio.from_signals(
            self.data.close,
            entries=entries,
            exits=exits,
            freq='1d',
            group_by=['symbol']
        )
        return portfolio