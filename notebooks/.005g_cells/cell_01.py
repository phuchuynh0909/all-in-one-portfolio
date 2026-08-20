import warnings
warnings.filterwarnings('ignore')

import os, sys, pathlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numba as nb
import vectorbt as vbt
import optuna
import talib
optuna.logging.set_verbosity(optuna.logging.WARNING)

sys.path.insert(0, str(pathlib.Path('..').resolve()))
from backend.app.services.indicators.trailing_sl import atr_trailing_nb
from backend.app.services.indicators.wiliams_vix_fix import williams_vix_fix_indicator
from backend.app.services.indicators.gkyz_volatility import calculate_gkyz_volatility
from backend.app.services.indicators.yang_zhang_volatility import calculate_yz_volatility
from backend.app.services.indicators.vwap import avwap_func_nb

plt.style.use('dark_background')
print('imports ok')