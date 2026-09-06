"""MVF training-cache tests: cutoff derivation, key stability, and fallbacks.

Offline — the ClickHouse loader is patched with a synthetic OHLCV panel and
every LSTM is trained for one epoch on a few hundred bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.schemas.mvf import MvfRequest
from app.services import mvf_lstm_service as svc


def _long_ohlcv(symbols: list[str], start: str, end: str, seed: int = 0) -> pd.DataFrame:
    """Long-format OHLCV, the shape _load_delta_stocks returns."""
    dates = pd.bdate_range(start, end)
    frames = []
    for i, sym in enumerate(symbols):
        # A generator per symbol: sharing one would make each symbol's draws
        # start at an offset that depends on the number of dates, so extending
        # the range would silently change earlier bars and the test would be
        # measuring its own fixture instead of the cache key.
        rng = np.random.default_rng(seed + 1000 * (i + 1))
        close = 10.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.015, len(dates))))
        frames.append(pd.DataFrame({
            "date": dates,
            "symbol": sym,
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture()
def req() -> MvfRequest:
    return MvfRequest(
        tickers=["AAA", "BBB"], benchmark="VNINDEX",
        seq_len=10, horizon=5, epochs=1, batch_size=64, years=3,
        cov_lookback=60,
    )


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_CACHE_DIR", tmp_path / "lstm_cache")


def test_train_cutoff_is_previous_calendar_year_end():
    assert svc._train_cutoff(pd.Timestamp("2026-08-30")) == pd.Timestamp("2025-12-31")
    assert svc._train_cutoff(pd.Timestamp("2026-01-02")) == pd.Timestamp("2025-12-31")
    assert svc._train_cutoff(pd.Timestamp("2026-12-31")) == pd.Timestamp("2025-12-31")

def test_weekly_dates_use_last_available_bar_after_cutoff():
    dates = pd.bdate_range("2025-12-29", "2026-01-16")
    assert [d.strftime("%Y-%m-%d") for d in svc._weekly_dates(dates, pd.Timestamp("2025-12-31"))] == [
        "2026-01-02",
        "2026-01-09",
        "2026-01-16",
    ]


def test_panels_window_starts_at_cutoff_minus_years(monkeypatch, req):
    seen: dict[str, object] = {}

    def fake_load(symbols, start, columns):
        seen["start"] = start
        return _long_ohlcv(list(symbols), "2020-01-01", "2026-08-30")

    monkeypatch.setattr(svc, "_load_delta_stocks", fake_load)
    cutoff = pd.Timestamp("2025-12-31")
    svc._panels(["AAA", "BBB", "VNINDEX"], years=req.years, cutoff=cutoff)

    # Anchored to the cutoff, not to "now" — this is what stops the key moving.
    assert seen["start"] == cutoff - pd.DateOffset(years=req.years)


def _fingerprints(monkeypatch, req, last_bar: str) -> dict[str, str]:
    """Training-slice fingerprints for every symbol, as stream_mvf computes them."""
    monkeypatch.setattr(
        svc, "_load_delta_stocks",
        lambda symbols, start, columns: _long_ohlcv(list(symbols), "2020-01-01", last_bar),
    )
    cutoff = pd.Timestamp("2025-12-31")
    prep = svc._prepare(req, ["AAA", "BBB"], cutoff)
    ts = svc._training_set(prep)
    out = {}
    for sym in prep.universe:
        feat, tgt = svc._train_arrays(prep, ts, sym)
        out[sym] = svc._fingerprint(sym, feat, tgt, req, cutoff)
    return out


def test_fingerprint_is_unchanged_when_post_cutoff_bars_are_appended(monkeypatch, req):
    """The property this whole change exists for.

    Before the cutoff anchoring, every extra trading day changed the window on
    both ends, so the key moved daily and the cache never hit.
    """
    a = _fingerprints(monkeypatch, req, "2026-08-30")
    b = _fingerprints(monkeypatch, req, "2026-09-15")
    assert a == b, "appending bars after the cutoff must not change the training key"


def test_fingerprint_changes_when_a_training_knob_changes(monkeypatch, req):
    base = _fingerprints(monkeypatch, req, "2026-08-30")
    other = _fingerprints(monkeypatch, req.model_copy(update={"epochs": 2}), "2026-08-30")
    assert base != other


def test_standardisation_uses_only_pre_cutoff_bars(monkeypatch, req):
    monkeypatch.setattr(
        svc, "_load_delta_stocks",
        lambda symbols, start, columns: _long_ohlcv(list(symbols), "2020-01-01", "2026-08-30"),
    )
    cutoff = pd.Timestamp("2025-12-31")
    prep = svc._prepare(req, ["AAA", "BBB"], cutoff)
    ts = svc._training_set(prep)

    expected = prep.log_ret.loc[prep.log_ret.index <= cutoff].mean()
    pd.testing.assert_series_equal(ts.mu_r, expected)
    assert ts.n_train == int((prep.log_ret.index <= cutoff).sum())
    assert ts.n_train < len(prep.log_ret), "there must be post-cutoff bars to forecast over"


def test_second_run_reuses_the_cached_model(monkeypatch, req):
    monkeypatch.setattr(
        svc, "_load_delta_stocks",
        lambda symbols, start, columns: _long_ohlcv(list(symbols), "2021-01-01", "2026-08-30"),
    )
    first = {e["symbol"]: e["source"] for k, e in svc.stream_mvf(req) if k == "asset"}
    assert set(first.values()) == {"trained"}

    second = {e["symbol"]: e["source"] for k, e in svc.stream_mvf(req) if k == "asset"}
    assert set(second.values()) == {"cached"}


def test_short_history_symbol_trains_without_caching(monkeypatch, req):
    """A ticker listed after the cutoff still gets modelled, just not cached."""
    def fake_load(symbols, start, columns):
        old = _long_ohlcv([s for s in symbols if s != "NEW"], "2021-01-01", "2026-08-30")
        new = _long_ohlcv(["NEW"], "2026-01-05", "2026-08-30", seed=3)
        return pd.concat([old, new], ignore_index=True)

    monkeypatch.setattr(svc, "_load_delta_stocks", fake_load)
    r = req.model_copy(update={"tickers": ["AAA", "BBB", "NEW"]})
    sources = {e["symbol"]: e["source"] for k, e in svc.stream_mvf(r) if k == "asset"}

    assert sources.get("NEW") == "trained-uncached"
    assert sources["AAA"] == "trained"
    assert not list((svc._CACHE_DIR).glob("mvf_NEW_*.pt")), "NEW must not be cached"


def test_result_reports_the_training_cutoff_and_weekly_history(monkeypatch, req):
    monkeypatch.setattr(
        svc, "_load_delta_stocks",
        lambda symbols, start, columns: _long_ohlcv(list(symbols), "2021-01-01", "2026-08-30"),
    )
    result = next(p for k, p in svc.stream_mvf(req) if k == "result")
    assert result["train_cutoff"] == "2025-12-31"

    history = result["allocation_history"]
    dates = [snapshot["as_of"] for snapshot in history]
    assert dates == sorted(set(dates))
    assert all("2025-12-31" < date <= result["as_of"] for date in dates)
    assert dates[-1] == result["as_of"]


def test_a_young_listing_does_not_truncate_its_peers(monkeypatch, req):
    """One recent listing must not shorten everyone else's training history.

    `_prepare` used to dropna() across columns, so adding a 2026 listing pulled
    the whole panel's start date forward to that listing and every asset trained
    on a few months of data instead of years — silently.
    """
    def fake_load(symbols, start, columns):
        old = _long_ohlcv([s for s in symbols if s != "NEW"], "2021-01-01", "2026-08-30")
        new = _long_ohlcv(["NEW"], "2026-01-05", "2026-08-30", seed=3)
        return pd.concat([old, new], ignore_index=True)

    monkeypatch.setattr(svc, "_load_delta_stocks", fake_load)
    cutoff = pd.Timestamp("2025-12-31")
    prep = svc._prepare(req.model_copy(update={"tickers": ["AAA", "BBB", "NEW"]}),
                        ["AAA", "BBB", "NEW"], cutoff)
    ts = svc._training_set(prep)

    aaa, _ = svc._train_arrays(prep, ts, "AAA")
    new, _ = svc._train_arrays(prep, ts, "NEW")

    assert len(aaa) > 1000, f"AAA should keep years of pre-cutoff history, got {len(aaa)}"
    assert len(new) == 0, "NEW listed after the cutoff, so it has no cacheable slice"
    assert not np.isnan(aaa).any(), "training features must not contain NaN"


def test_forecast_still_uses_bars_after_the_cutoff(monkeypatch, req):
    """The point of the cutoff: fit through year-end, then roll forward."""
    monkeypatch.setattr(
        svc, "_load_delta_stocks",
        lambda symbols, start, columns: _long_ohlcv(list(symbols), "2021-01-01", "2026-08-30"),
    )
    loaded = next(p for k, p in svc.stream_mvf(req) if k == "loaded")
    assert loaded["train_cutoff"] == "2025-12-31"
    assert loaded["train_bars"] < loaded["bars"], "there must be unseen bars to forecast over"
    assert loaded["end"] > "2026-01-01"
