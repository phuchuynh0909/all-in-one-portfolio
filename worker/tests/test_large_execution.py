"""Offline tests for the large-execution ("block episode") detection core.

Pure functions only — no ClickHouse, MQTT, or network. Synthetic tapes are
built so the numbers are hand-checkable and the tests fail if the detection
logic (signs, prior-only baselines, stitching) regresses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.large_execution import (
    FLOW_CLUSTER,
    FLOW_CLUSTER_AND_LARGE_PRINT,
    LARGE_PRINT,
    DetectionParams,
    SymbolDetector,
    aggregate_bins,
    compute_bin_features,
    compute_event_features,
    detect,
    signed_sign,
    sign_to_side,
    stitch_episodes,
)
from core.tick_contract import SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN

BASE = datetime(2026, 6, 20, 2, 0, 0, tzinfo=timezone.utc)  # arbitrary UTC start


def tick(offset_s: float, side: int, price: float, qty: int, symbol: str = "FPT"):
    """Build a canonical tick `offset_s` seconds after BASE."""
    return {
        "symbol": symbol,
        "sending_time": BASE + timedelta(seconds=offset_s),
        "match_price": price,
        "match_qty": qty,
        "side": side,
    }


# ---------------------------------------------------------------------------
# Sign helpers
# ---------------------------------------------------------------------------
def test_signed_sign_and_side_roundtrip():
    assert signed_sign(SIDE_BUY) == 1
    assert signed_sign(SIDE_SELL) == -1
    assert signed_sign(SIDE_UNKNOWN) == 0
    assert sign_to_side(1) == SIDE_BUY
    assert sign_to_side(-1) == SIDE_SELL
    assert sign_to_side(0) == SIDE_UNKNOWN


# ---------------------------------------------------------------------------
# Tier 1 — event features
# ---------------------------------------------------------------------------
def test_rolling_signed_flow_direction_and_window():
    # Three buys then, 40s later (outside a 30s window), a sell.
    ticks = [
        tick(0, SIDE_BUY, 100.0, 10),
        tick(1, SIDE_BUY, 100.0, 10),
        tick(2, SIDE_BUY, 100.0, 10),
        tick(42, SIDE_SELL, 100.0, 5),
    ]
    feats = compute_event_features(ticks, DetectionParams(rolling_seconds=30))

    # After 3 buys the rolling flow is positive and imbalance is fully +1.
    assert feats[2]["rolling_signed_volume"] == 30
    assert feats[2]["rolling_signed_notional"] == pytest.approx(3000.0)
    assert feats[2]["rolling_flow_imbalance"] == pytest.approx(1.0)

    # The sell at +42s: the earlier buys have aged out of the 30s window, so
    # only the sell remains -> fully one-sided sell.
    assert feats[3]["rolling_signed_volume"] == -5
    assert feats[3]["rolling_flow_imbalance"] == pytest.approx(-1.0)


def test_large_print_detects_outlier_and_excludes_ineligible():
    # 40 varied small prints (all ineligible: min_prior=40), then one huge print
    # whose 40 priors make it eligible and clearly over the 99th percentile.
    params = DetectionParams(
        large_print_window=500, large_print_quantile=0.99, large_print_min_prior=40
    )
    ticks = [tick(i * 0.001, SIDE_BUY, 100.0, 8 + (i % 5)) for i in range(40)]
    ticks.append(tick(0.05, SIDE_BUY, 100.0, 10_000))  # ~1000x notional
    feats = compute_event_features(ticks, params)

    # First 40 have < min_prior priors -> ineligible, never flagged.
    assert not any(f["is_large_print"] for f in feats[:40])
    # The final oversized print clears the prior-40 99th percentile.
    assert feats[-1]["is_large_print"] is True


def test_large_print_needs_minimum_history():
    params = DetectionParams(large_print_min_prior=30)
    ticks = [tick(i * 0.001, SIDE_BUY, 100.0, 10) for i in range(5)]
    ticks.append(tick(0.02, SIDE_BUY, 100.0, 10_000))
    feats = compute_event_features(ticks, params)
    # Only 6 prior trades (< 30) -> large-print detection stays off.
    assert all(not f["is_large_print"] for f in feats)


# ---------------------------------------------------------------------------
# Tier 2 — bin features
# ---------------------------------------------------------------------------
def test_aggregate_bins_groups_by_second_and_signs():
    ticks = [
        tick(0.1, SIDE_BUY, 100.0, 10),
        tick(0.9, SIDE_BUY, 100.0, 10),
        tick(1.5, SIDE_SELL, 100.0, 4),
    ]
    events = compute_event_features(ticks)
    bins = aggregate_bins(events, DetectionParams(bin_seconds=1))
    assert len(bins) == 2

    b0, b1 = bins
    assert b0["num_trades"] == 2
    assert b0["signed_notional"] == pytest.approx(2000.0)
    assert b0["direction"] == 1
    assert b0["trade_flow_imbalance"] == pytest.approx(1.0)

    assert b1["num_trades"] == 1
    assert b1["signed_notional"] == pytest.approx(-400.0)
    assert b1["direction"] == -1


def test_z_score_is_prior_only_and_gated_by_min_baseline():
    # 5 quiet bins of tiny buy flow, then a big buy bin.
    params = DetectionParams(
        bin_seconds=1, min_baseline_bins=3, baseline_bins=100
    )
    ticks = []
    for s in range(5):
        qty = 1 if s % 2 == 0 else 3  # varied so the baseline has non-zero std
        ticks.append(tick(s + 0.1, SIDE_BUY, 100.0, qty))
    ticks.append(tick(5 + 0.1, SIDE_BUY, 100.0, 1000))  # big spike bin

    events = compute_event_features(ticks, params)
    bins = compute_bin_features(aggregate_bins(events, params), params)

    # First 3 bins have < min_baseline prior bins -> z is None.
    assert bins[0]["signed_notional_z"] is None
    assert bins[2]["signed_notional_z"] is None
    # Later bins get a real z; the spike is a large positive surprise.
    assert bins[-1]["signed_notional_z"] is not None
    assert bins[-1]["signed_notional_z"] > 3.0


# ---------------------------------------------------------------------------
# Tier 3 — flow clusters + episodes
# ---------------------------------------------------------------------------
def _sustained_buy_program(n_quiet=350, n_active=6):
    """A long quiet baseline of alternating tiny flow, then a sustained,
    one-sided, multi-trade buy burst across consecutive seconds."""
    ticks = []
    t = 0.0
    # Quiet baseline: alternate tiny buy/sell so mean flow ~0, std small.
    for i in range(n_quiet):
        side = SIDE_BUY if i % 2 == 0 else SIDE_SELL
        ticks.append(tick(t, side, 100.0, 1))
        t += 1.0
    # Active burst: several consecutive seconds of heavy, one-sided buying.
    for _ in range(n_active):
        for k in range(4):  # >= min_trades_per_bin
            ticks.append(tick(t + k * 0.1, SIDE_BUY, 100.0, 500))
        t += 1.0
    return ticks


def test_flow_cluster_and_episode_detected_for_sustained_program():
    # Disable large prints (min_prior far beyond the tape) so episodes are
    # driven purely by flow clusters here.
    params = DetectionParams(
        bin_seconds=1,
        min_baseline_bins=100,
        z_threshold=2.5,
        imbalance_threshold=0.70,
        min_trades_per_bin=3,
        run_length=2,
        episode_gap_bins=5,
        large_print_min_prior=10**9,
    )
    out = detect(_sustained_buy_program(), params)

    clusters = [b for b in out["bin_features"] if b["flow_cluster_candidate"]]
    assert clusters, "expected at least one flow-cluster bin"
    # All cluster bins are buy-side.
    assert all(b["direction"] == 1 for b in clusters)

    episodes = out["episodes"]
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["side"] == SIDE_BUY
    assert ep["candidate_type"] in (FLOW_CLUSTER, FLOW_CLUSTER_AND_LARGE_PRINT)
    assert ep["num_bins"] >= 2
    assert ep["signed_notional"] > 0
    assert ep["max_abs_imbalance"] == pytest.approx(1.0)
    assert ep["end_time"] >= ep["start_time"]


def test_run_length_requires_two_consecutive_bins():
    # A single isolated heavy buy second surrounded by a quiet baseline should
    # meet the per-bin gate but NOT form a flow cluster (run_length=2).
    params = DetectionParams(
        bin_seconds=1, min_baseline_bins=100, run_length=2, min_trades_per_bin=3
    )
    ticks = []
    t = 0.0
    for i in range(200):
        side = SIDE_BUY if i % 2 == 0 else SIDE_SELL
        ticks.append(tick(t, side, 100.0, 1))
        t += 1.0
    # One lone heavy buy second.
    for k in range(4):
        ticks.append(tick(t + k * 0.1, SIDE_BUY, 100.0, 500))

    out = detect(ticks, params)
    assert not any(b["flow_cluster_candidate"] for b in out["bin_features"])


def test_large_print_only_episode():
    # No flow cluster, but a single giant print in a bin -> LARGE_PRINT episode.
    params = DetectionParams(
        bin_seconds=1,
        large_print_min_prior=40,  # only the giant (index 40) is eligible
        min_baseline_bins=10_000,  # keep z invalid so no flow clusters
    )
    ticks = [tick(i * 0.01, SIDE_BUY, 100.0, 8 + (i % 5)) for i in range(40)]
    ticks.append(tick(1.0, SIDE_BUY, 100.0, 50_000))  # giant print, next second
    out = detect(ticks, params)

    assert any(f["is_large_print"] for f in out["event_features"])
    episodes = out["episodes"]
    assert len(episodes) == 1
    assert episodes[0]["candidate_type"] == LARGE_PRINT
    assert episodes[0]["large_print_count"] == 1


def test_multi_symbol_state_does_not_leak():
    ticks = _sustained_buy_program()
    # Same program relabelled to a second symbol, interleaved.
    other = [dict(t, symbol="HPG") for t in _sustained_buy_program()]
    out = detect(ticks + other, DetectionParams(min_baseline_bins=100))
    symbols = {ep["symbol"] for ep in out["episodes"]}
    assert symbols == {"FPT", "HPG"}


def test_stitch_breaks_on_direction_change():
    # Manually crafted candidate bins: two buys, then a sell -> two episodes.
    def cbin(sec, direction, ctype=FLOW_CLUSTER):
        return {
            "symbol": "FPT",
            "bin_time": BASE + timedelta(seconds=sec),
            "signed_notional": 1000.0 * direction,
            "abs_notional": 1000.0,
            "num_trades": 5,
            "large_print_count": 0,
            "trade_flow_imbalance": 1.0 * direction,
            "direction": direction,
            "signed_notional_z": 3.0 * direction,
            "flow_cluster_candidate": ctype == FLOW_CLUSTER,
            "candidate_type": ctype,
        }

    bins = [cbin(0, 1), cbin(1, 1), cbin(2, -1)]
    episodes = stitch_episodes(bins, DetectionParams(episode_gap_bins=5))
    assert len(episodes) == 2
    assert episodes[0]["side"] == SIDE_BUY and episodes[0]["num_bins"] == 2
    assert episodes[1]["side"] == SIDE_SELL and episodes[1]["num_bins"] == 1


def test_stitch_breaks_on_time_gap():
    def cbin(sec):
        return {
            "symbol": "FPT",
            "bin_time": BASE + timedelta(seconds=sec),
            "signed_notional": 1000.0,
            "abs_notional": 1000.0,
            "num_trades": 5,
            "large_print_count": 0,
            "trade_flow_imbalance": 1.0,
            "direction": 1,
            "signed_notional_z": 3.0,
            "flow_cluster_candidate": True,
            "candidate_type": FLOW_CLUSTER,
        }

    # Gap of 10 bins > episode_gap_bins=5 -> two separate episodes.
    bins = [cbin(0), cbin(1), cbin(11), cbin(12)]
    episodes = stitch_episodes(bins, DetectionParams(episode_gap_bins=5))
    assert len(episodes) == 2


# ---------------------------------------------------------------------------
# Streaming detector — must match the batch pipeline exactly
# ---------------------------------------------------------------------------
def _dedup_last_by_key(episodes):
    """Keep the last emitted snapshot per (symbol, start_time, side)."""
    by_key = {}
    for ep in episodes:
        by_key[(ep["symbol"], ep["start_time"], ep["side"])] = ep
    return by_key


def _compare_episode(a, b):
    keys = [
        "symbol", "side", "start_time", "end_time", "candidate_type",
        "num_bins", "num_trades", "large_print_count",
    ]
    for k in keys:
        assert a[k] == b[k], f"{k}: {a[k]!r} != {b[k]!r}"
    assert a["signed_notional"] == pytest.approx(b["signed_notional"])
    assert a["abs_notional"] == pytest.approx(b["abs_notional"])
    assert a["max_abs_z"] == pytest.approx(b["max_abs_z"])
    assert a["max_abs_imbalance"] == pytest.approx(b["max_abs_imbalance"])


def _run_streaming(ticks, params):
    det = SymbolDetector(params)
    emitted = []
    for t in ticks:
        emitted.extend(det.push(t))
    emitted.extend(det.flush())
    return emitted


def test_streaming_matches_batch_for_sustained_program():
    params = DetectionParams(
        min_baseline_bins=100, large_print_min_prior=10**9  # disable large prints
    )
    ticks = _sustained_buy_program()

    batch = detect(ticks, params)["episodes"]
    stream_final = _dedup_last_by_key(_run_streaming(ticks, params))

    assert len(stream_final) == len(batch) == 1
    b = batch[0]
    s = stream_final[(b["symbol"], b["start_time"], b["side"])]
    _compare_episode(s, b)


def test_streaming_matches_batch_with_large_prints():
    # Default-ish params where large prints also fire -> many episodes; the
    # final streaming snapshots must still equal the batch episode set.
    params = DetectionParams(min_baseline_bins=100, large_print_min_prior=30)
    ticks = _sustained_buy_program()

    batch = detect(ticks, params)["episodes"]
    stream_final = _dedup_last_by_key(_run_streaming(ticks, params))

    assert len(stream_final) == len(batch)
    batch_by_key = _dedup_last_by_key(batch)
    assert set(stream_final.keys()) == set(batch_by_key.keys())
    for key, b in batch_by_key.items():
        _compare_episode(stream_final[key], b)


def test_streaming_emits_growing_snapshots_then_converges():
    # The same episode key is emitted multiple times as it grows; the last
    # snapshot has the largest num_bins.
    params = DetectionParams(min_baseline_bins=100, large_print_min_prior=10**9)
    emitted = _run_streaming(_sustained_buy_program(), params)
    # More emissions than final episodes (growing snapshots re-emitted).
    assert len(emitted) >= 2
    by_key = {}
    for ep in emitted:
        by_key.setdefault((ep["start_time"], ep["side"]), []).append(ep["num_bins"])
    for _key, sizes in by_key.items():
        assert sizes == sorted(sizes)  # monotonically non-decreasing
        assert sizes[-1] == max(sizes)


def test_streaming_flush_closes_final_bin():
    params = DetectionParams(min_baseline_bins=100, large_print_min_prior=10**9)
    det = SymbolDetector(params)
    ticks = _sustained_buy_program()
    pre_flush = []
    for t in ticks:
        pre_flush.extend(det.push(t))
    # The very last burst bin is only scored on flush (no later trade closes it).
    post_flush = det.flush()
    assert isinstance(post_flush, list)
    combined = _dedup_last_by_key(pre_flush + post_flush)
    batch = _dedup_last_by_key(detect(ticks, params)["episodes"])
    assert set(combined.keys()) == set(batch.keys())
