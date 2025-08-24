import vectorbt as vbt
from app.services.indicators import avwap_func_nb, atr_trailing_nb, lowest_at_entry
import numpy as np
from scipy.stats import norm
import pandas as pd

class SqueezeBreakoutStrategy:

    def __init__(self, data: pd.DataFrame, bb_window: int, bb_multiplier: float, kc_window: int, kc_multiplier: float, atr_multiplier: float = 1.8, squeeze_threshold: float = 0.1):
        self.data = data
        self.bb_window = bb_window
        self.bb_multiplier = bb_multiplier
        self.kc_window = kc_window
        self.kc_multiplier = kc_multiplier
        self.atr_multiplier = atr_multiplier
        self.squeeze_threshold = squeeze_threshold

        self.indicators = {
            'bbands': vbt.IndicatorFactory.from_ta("BollingerBands"),
            'keltner_channel': vbt.IndicatorFactory.from_ta("KeltnerChannel"),
            'atr': vbt.IndicatorFactory.from_ta("AverageTrueRange"),
            'vwap': vbt.IndicatorFactory(
                class_name='AVWAP',
                short_name='avwap',
                input_names=['close', 'high', 'low', 'volume'],
                param_names=['is_highest', 'window'],
                output_names=['avwap']
            ).from_apply_func(avwap_func_nb).run(self.data.close, self.data.high, self.data.low, self.data.volume, is_highest=[True, False], window=200).avwap
        }

    def get_entries(self):
        kc = self.indicators['keltner_channel'].run(
            self.data.high, self.data.low, self.data.close, 
            window=self.kc_window, window_atr=self.kc_window, multiplier=self.kc_multiplier, original_version=False)
        bb = self.indicators['bbands'].run(self.data.close, window=self.bb_window, window_dev=self.bb_multiplier)

        vwap_lowest = self.indicators['vwap'].xs(False, level='avwap_is_highest', axis=1)

        recent_volatility = self.indicators['atr'].run(
            self.data.high, self.data.low, self.data.close, window=10).average_true_range
        expanding_mean_vol = recent_volatility.expanding().mean()
        expanding_mean_vol = expanding_mean_vol.replace(0, np.nan).ffill().fillna(1e-9)
        recent_volatility = recent_volatility.fillna(1e-9)
        dynamic_squeeze_threshold = self.squeeze_threshold * recent_volatility / expanding_mean_vol
        dynamic_squeeze_threshold = dynamic_squeeze_threshold.clip(lower=0)

        isSqueeze = ((bb.bollinger_hband.vbt <= kc.keltner_channel_hband.vbt * (1 + dynamic_squeeze_threshold)) &
                (bb.bollinger_lband.vbt >= kc.keltner_channel_lband.vbt * (1 - dynamic_squeeze_threshold)))

        subEntry1_1 = self.data.close.vbt.crossed_above(bb.bollinger_hband)
        entry1 = subEntry1_1.vbt.signals.AND(isSqueeze.vbt)

        subEntry2_1 = self.data.close.vbt > bb.bollinger_hband.vbt
        entry2 = subEntry2_1.vbt.signals.AND(~isSqueeze.vbt)

        cond1 = self.data.close.vbt > vwap_lowest.vbt

        entries = cond1.vbt.signals.AND(entry1.vbt.signals.OR(entry2))
        return entries

    def get_exits(self, entries):
        atr_scope = self.indicators['atr'].run(
            self.data.high, self.data.low, self.data.close, window=10).average_true_range
        atr_trailing_indicator = vbt.IndicatorFactory(
            input_names=['close', 'atr'],
            param_names=['atr_multiplier'],
            output_names=['atr_trailing']
        ).from_apply_func(atr_trailing_nb, atr_multiplier=1.8)
        atr_sl = atr_trailing_indicator.run(self.data.close, atr_scope, atr_multiplier=self.atr_multiplier)
        exit1 = self.data.close.vbt.crossed_below(atr_sl.atr_trailing)

        lowest_at_entry_indicator = vbt.IndicatorFactory(
            input_names=['low', 'entry'],
            output_names=['lowest_low']
        ).from_apply_func(lowest_at_entry).run(self.data.low, entries)
        exit2 = self.data.close.vbt.crossed_below(lowest_at_entry_indicator.lowest_low)

        exists = exit1.vbt.signals.OR(exit2)
        return exists

    def get_portfolio(self):
        entries = self.get_entries()
        exists = self.get_exits(entries)

        portfolio = vbt.Portfolio.from_signals(
            self.data.close,
            entries=entries,
            exits=exists,
            cash_sharing=False,
            freq='1d',
            group_by=['symbol']
        )
        return portfolio