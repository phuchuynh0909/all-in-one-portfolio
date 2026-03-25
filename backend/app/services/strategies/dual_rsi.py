import vectorbt as vbt
import numpy as np
import pandas as pd
from app.services.indicators import avwap_func_nb

class DualRSI:

    def __init__(self, data: pd.DataFrame, 
                 rsi_window_high: int = 14,
                 rsi_window_low: int = 5,
                 vwap_window: int = 200,
                 sl_stop: float = 0.05):
        
        self.data = data
        self.rsi_window_high = rsi_window_high
        self.rsi_window_low = rsi_window_low
        self.vwap_window = vwap_window
        self.sl_stop = sl_stop

        self.indicators = {
            'rsi': vbt.IndicatorFactory.from_talib("RSI"),
            'vwap': vbt.IndicatorFactory(
                class_name='AVWAP',
                short_name='avwap',
                input_names=['close', 'high', 'low', 'volume'],
                param_names=['is_highest', 'window'],
                output_names=['avwap']
            ).from_apply_func(avwap_func_nb).run(self.data.close.round(2), self.data.high.round(2), self.data.low.round(2), self.data.volume, is_highest=[True, False], window=self.vwap_window).avwap
        }

    def get_entries(self):
        rsi_df = self.indicators['rsi'].run(self.data.close.round(2), timeperiod=[self.rsi_window_high, self.rsi_window_low])
        rsi5 = rsi_df.real.xs(self.rsi_window_low, level='rsi_timeperiod', axis=1)
        rsi14 = rsi_df.real.xs(self.rsi_window_high, level='rsi_timeperiod', axis=1)

        ## AVWAP
        vwap = self.indicators['vwap']
        vwap_highest = vwap.xs(True, level='avwap_is_highest', axis=1)

        close_price = self.data.close.round(2)
        entries = (
            ## Condition by rsi
            (rsi14.shift(1) <= 30) & 
            # (rsi5.shift(1).rolling(3).min() <= 30): 
            # This line checks if the minimum value of the previous 3 shifted RSI(5) values is less than or equal to 30.
            # Effectively, it captures if the rsi5 has recently (in the past 3 bars prior to the current bar) dipped into or below the oversold region.
            (rsi5.shift(1).rolling(3).min() <= 30) &
            # (rsi5.shift(1) <= rsi5.shift(2)) & (rsi5.shift(2) <= rsi5.shift(3)):
            # This pair of conditions checks for a monotonically decreasing pattern in rsi5 over the last 3 shifted periods. 
            # It ensures that rsi5 from 3 bars ago to 1 bar ago has been non-increasing, i.e., each value is less than or equal to the previous one.
            # In summary, while the rolling(3).min() condition looks for any dip <= 30 in the past 3 bars,
            # this sequence explicitly enforces that rsi5 has not increased in that window (or is flat or decreasing).
            (rsi5.shift(1) <= rsi5.shift(2)) & (rsi5.shift(2) <= rsi5.shift(3)) & 
            ## Price reversal
            (close_price > close_price.shift(1)) & 
            ## Condition by avwap (percentage from vwap & close > 8%)
            (abs((close_price - vwap_highest.shift(1)) / vwap_highest.shift(1)) > 0.08)
        )
        return entries

    def get_exits(self, entries):
        vwap_highest = self.indicators['vwap'].xs(True, level='avwap_is_highest', axis=1)
        exits1 = self.data.high.vbt > vwap_highest.shift(1).vbt
        
        lowest_low = self.data.low.vbt.rolling_min(self.donichan_window)
        exits2 = self.data.close < lowest_low.vbt.fshift(1)
        return exits1 | exits2
    
    def get_portfolio(self):
        entries = self.get_entries()
        exits = self.get_exits(entries)
        portfolio = vbt.Portfolio.from_signals(
            self.data.close.round(2),
            entries=entries,
            exits=exits,
            freq='1d',
            group_by=['symbol'],
            sl_stop=self.sl_stop,  # 5% hard stop loss
        )
        return portfolio