"""Unit tests for experiment schema helpers (pure, no I/O)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.services.experiments.schema import (
    FEATURE_PREFIX,
    clean_float,
    json_safe,
    make_run_id,
    params_hash,
)


def test_run_id_is_stable_for_same_params():
    created = datetime(2026, 8, 30, 14, 22, 33, tzinfo=timezone.utc)
    a = make_run_id("backtest_012", {"x": 1, "y": 2}, created)
    b = make_run_id("backtest_012", {"y": 2, "x": 1}, created)
    assert a == b, "param key order must not change the hash"
    assert a.startswith("backtest_012__20260830T142233__")
    assert len(a.rsplit("__", 1)[1]) == 6


def test_run_id_changes_when_params_change():
    created = datetime(2026, 8, 30, 14, 22, 33, tzinfo=timezone.utc)
    a = make_run_id("bt", {"x": 1}, created)
    b = make_run_id("bt", {"x": 2}, created)
    assert a != b


def test_run_id_sanitises_unsafe_name_characters():
    created = datetime(2026, 8, 30, 14, 22, 33, tzinfo=timezone.utc)
    assert make_run_id("bt 012/oos", {}, created).startswith("bt-012-oos__")


def test_params_hash_handles_non_json_values():
    # numpy scalars and Timestamps appear in Optuna params; must not raise.
    assert len(params_hash({"a": np.float64(1.5), "b": pd.Timestamp("2024-01-01")})) == 6


def test_clean_float_maps_non_finite_to_none():
    assert clean_float(np.inf) is None
    assert clean_float(-np.inf) is None
    assert clean_float(np.nan) is None
    assert clean_float(None) is None
    assert clean_float(np.float64(1.5)) == 1.5
    assert isinstance(clean_float(np.float64(1.5)), float)


def test_json_safe_recurses_and_normalises():
    out = json_safe({"a": np.int64(3), "b": [np.inf, math.nan], "c": pd.Timestamp("2024-01-02")})
    assert out == {"a": 3, "b": [None, None], "c": "2024-01-02T00:00:00"}


def test_feature_prefix_is_feat():
    assert FEATURE_PREFIX == "feat_"
