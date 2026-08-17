"""Unit tests for per-trade MAE/MFE scatter computation (pure numeric logic)."""
import types

import pandas as pd

from app.services.backtest_plot_service import _compute_mfe_mae_trades


def _make_data():
    # 5 bars; highs/lows chosen so excursions are easy to reason about.
    return pd.DataFrame(
        {
            "High": [100.0, 110.0, 105.0, 130.0, 120.0],
            "Low": [100.0, 90.0, 95.0, 100.0, 100.0],
        },
        index=pd.to_datetime(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        ),
    )


def _make_stats(trades: pd.DataFrame):
    return types.SimpleNamespace(_trades=trades)


def test_long_trade_mae_mfe_and_latest_flag():
    data = _make_data()
    trades = pd.DataFrame(
        [
            # closed long: bars 0..2, entry 100
            {"EntryBar": 0, "ExitBar": 2, "EntryPrice": 100.0, "Size": 10,
             "ExitPrice": 105.0, "ReturnPct": 0.05},
            # latest long, still open (ExitBar at/after last bar), entry 100
            {"EntryBar": 3, "ExitBar": 4, "EntryPrice": 100.0, "Size": 10,
             "ExitPrice": float("nan"), "ReturnPct": float("nan")},
        ]
    )
    points = _compute_mfe_mae_trades(_make_stats(trades), data)

    assert len(points) == 2
    first, latest = points

    # bars 0..2: max high 110 -> MFE 10%, min low 90 -> MAE 10%
    assert first["mfe"] == 10.0
    assert first["mae"] == 10.0
    assert first["direction"] == "long"
    assert first["is_open"] is False
    assert first["is_latest"] is False
    assert first["return_pct"] == 5.0

    # bars 3..4: max high 130 -> MFE 30%, min low 100 -> MAE 0%
    assert latest["mfe"] == 30.0
    assert latest["mae"] == 0.0
    assert latest["is_latest"] is True
    assert latest["is_open"] is True
    assert latest["return_pct"] is None


def test_short_trade_excursions_are_inverted():
    data = _make_data()
    trades = pd.DataFrame(
        [
            {"EntryBar": 0, "ExitBar": 2, "EntryPrice": 100.0, "Size": -10,
             "ExitPrice": 95.0, "ReturnPct": 0.05},
        ]
    )
    (point,) = _compute_mfe_mae_trades(_make_stats(trades), data)

    # short: favourable = price drop (min low 90 -> 10%), adverse = price rise (max high 110 -> 10%)
    assert point["direction"] == "short"
    assert point["mfe"] == 10.0
    assert point["mae"] == 10.0


def test_empty_trades_returns_empty_list():
    assert _compute_mfe_mae_trades(_make_stats(pd.DataFrame()), _make_data()) == []
    assert _compute_mfe_mae_trades(None, _make_data()) == []
