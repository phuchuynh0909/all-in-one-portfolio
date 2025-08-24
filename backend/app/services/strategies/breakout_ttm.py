import vectorbt as vbt
import numpy as np
import pandas as pd
from app.services.indicators import atr_trailing_nb, exrem_func_nb

class BreakoutTTMVersion2:

    def __init__(self, data: pd.DataFrame, 
                 bb_window: int = 16,
                 bb_multiplier: float = 1.0,
                 kc_window: int = 40,
                 kc_multiplier: float = 1.2,
                 atr_window: int = 14,
                 momentum_window: int = 12,
                 donichan_window: int = 12,
                 entry_version: str = 'v1'):
        
        self.data = data
        self.bb_window = bb_window
        self.bb_multiplier = bb_multiplier
        self.kc_window = kc_window
        self.kc_multiplier = kc_multiplier
        self.atr_window = atr_window
        self.momentum_window = momentum_window
        self.donichan_window = donichan_window
        self.entry_version = entry_version

    def get_entries(self):
        bb = vbt.IndicatorFactory.from_ta("BollingerBands").run(self.data.close, window=self.bb_window, window_dev=self.bb_multiplier)
        kc = vbt.IndicatorFactory.from_ta("KeltnerChannel").run(self.data.high, self.data.low, self.data.close, window=self.kc_window, window_atr=self.atr_window, multiplier=self.kc_multiplier, original_version=False)

        sqzOn = (bb.bollinger_hband.vbt < kc.keltner_channel_hband.vbt) & (bb.bollinger_lband.vbt > kc.keltner_channel_lband.vbt)
        sqzOff = (bb.bollinger_hband.vbt > kc.keltner_channel_hband.vbt) & (bb.bollinger_lband.vbt < kc.keltner_channel_lband.vbt)
        noSqz = (sqzOn == 0) & (sqzOff == 0)

        donchianMidline = (self.data.high.vbt.rolling_max(self.donichan_window)  + 
                           self.data.low.vbt.rolling_min(self.donichan_window) + 
                           self.data.close.vbt.rolling_mean(self.donichan_window)) / 3
        histogram = self.data.close - donchianMidline
        momentum = vbt.IndicatorFactory.from_talib("LINEARREG").run(histogram, timeperiod=self.momentum_window).real

        cond_2 = (momentum > momentum.vbt.fshift(1)) & (momentum.vbt.fshift(1).vbt.crossed_above(0))
        cond_3 = (momentum > momentum.vbt.fshift(2)) & (momentum.vbt.fshift(2).vbt.crossed_above(0))
        
        if self.entry_version == 'v1':
            entries = noSqz.vbt & (momentum > 0)
        elif self.entry_version == 'v2':
            entries = noSqz.vbt & ( (momentum.vbt.crossed_above(0)) | cond_2 | cond_3 )
    
        return entries

    def get_exits(self, entries):
        atr = vbt.IndicatorFactory.from_talib("ATR").run(self.data.high, self.data.low, self.data.close, timeperiod=10)
        atr_scope = atr.real
        atr_trailing_indicator = vbt.IndicatorFactory(
            input_names=['close', 'atr'],
            param_names=['atr_multiplier'],
            output_names=['atr_trailing']
        ).from_apply_func(atr_trailing_nb)
        atr_sl = atr_trailing_indicator.run(self.data.close, atr_scope, atr_multiplier=1.8)
        exit1 = self.data.close.vbt.crossed_below(atr_sl.atr_trailing)

        new_entries = vbt.IndicatorFactory(
            input_names=['entries', 'exits'],
            output_names=['new_entries']
        ).from_apply_func(exrem_func_nb).run(entries, exit1).new_entries

        exit2 = self.data.close.vbt < atr_sl.atr_trailing.vbt

        exists = exit1.vbt | exit2.vbt
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