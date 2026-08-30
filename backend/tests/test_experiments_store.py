"""Store and storage-backend tests. Filesystem only, no network."""
from __future__ import annotations

import pandas as pd

from app.services.experiments.backends import LocalBackend


def test_local_backend_round_trips_parquet(tmp_path):
    backend = LocalBackend(root=tmp_path)
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    backend.write_parquet("runs/r1/trades.parquet", df)

    written = tmp_path / "runs" / "r1" / "trades.parquet"
    assert written.exists(), "backend must create parent directories"
    pd.testing.assert_frame_equal(pd.read_parquet(written), df)


def test_local_backend_round_trips_json(tmp_path):
    backend = LocalBackend(root=tmp_path)
    backend.write_json("runs/r1/meta.json", {"run_id": "r1", "n": 3})
    assert backend.read_json("runs/r1/meta.json") == {"run_id": "r1", "n": 3}


def test_local_backend_read_json_missing_returns_none(tmp_path):
    assert LocalBackend(root=tmp_path).read_json("nope.json") is None


def test_local_backend_lists_run_ids_sorted(tmp_path):
    backend = LocalBackend(root=tmp_path)
    for rid in ["b_run", "a_run"]:
        backend.write_json(f"runs/{rid}/meta.json", {"run_id": rid})
    # A directory with no meta.json is an incomplete write and must be skipped.
    (tmp_path / "runs" / "c_partial").mkdir(parents=True)
    assert backend.list_run_ids() == ["a_run", "b_run"]


def test_local_backend_json_write_is_atomic(tmp_path):
    backend = LocalBackend(root=tmp_path)
    backend.write_json("catalog.json", {"v": 1})
    backend.write_json("catalog.json", {"v": 2})
    assert backend.read_json("catalog.json") == {"v": 2}
    assert list((tmp_path).glob("*.tmp")) == [], "temp files must not be left behind"


import pytest

from app.services.experiments.store import ExperimentStore, RunHandle


def _frames():
    trades = pd.DataFrame({"run_id": ["r1"], "trade_id": [0], "symbol": ["AAA"],
                           "net_return": [0.1]})
    stats = pd.DataFrame({"run_id": ["r1"], "symbol": ["AAA"], "total_return": [0.1]})
    equity = pd.DataFrame({"run_id": ["r1"], "dt": pd.to_datetime(["2024-01-01"]),
                           "value": [100.0]})
    return trades, stats, equity


def _store(tmp_path) -> ExperimentStore:
    return ExperimentStore(backend=LocalBackend(root=tmp_path))


def test_write_run_persists_three_tables_and_meta(tmp_path):
    store = _store(tmp_path)
    trades, stats, equity = _frames()

    handle = store.write_run(run_id="r1", meta={"run_id": "r1", "name": "bt"},
                             trades=trades, symbol_stats=stats, equity=equity)

    assert isinstance(handle, RunHandle)
    assert handle.run_id == "r1"
    run_dir = tmp_path / "runs" / "r1"
    for fname in ["trades.parquet", "symbol_stats.parquet", "equity.parquet", "meta.json"]:
        assert (run_dir / fname).exists(), f"{fname} missing"


def test_write_run_records_schema_version_and_file_paths(tmp_path):
    store = _store(tmp_path)
    trades, stats, equity = _frames()
    handle = store.write_run(run_id="r1", meta={"run_id": "r1"},
                             trades=trades, symbol_stats=stats, equity=equity)

    assert handle.meta["schema_version"] == 1
    assert handle.meta["files"]["trades"] == "runs/r1/trades.parquet"
    assert handle.meta["files"]["symbol_stats"] == "runs/r1/symbol_stats.parquet"
    assert handle.meta["files"]["equity"] == "runs/r1/equity.parquet"


def test_write_run_refreshes_catalog(tmp_path):
    store = _store(tmp_path)
    trades, stats, equity = _frames()
    store.write_run(run_id="r1", meta={"run_id": "r1", "name": "bt"},
                    trades=trades, symbol_stats=stats, equity=equity)

    catalog = store.backend.read_json("catalog.json")
    assert catalog["schema_version"] == 1
    assert [r["run_id"] for r in catalog["runs"]] == ["r1"]


def test_rebuild_catalog_recovers_a_deleted_catalog(tmp_path):
    store = _store(tmp_path)
    trades, stats, equity = _frames()
    for rid in ["r1", "r2"]:
        store.write_run(run_id=rid, meta={"run_id": rid},
                        trades=trades, symbol_stats=stats, equity=equity)

    (tmp_path / "catalog.json").unlink()
    assert store.rebuild_catalog() == 2
    assert [r["run_id"] for r in store.backend.read_json("catalog.json")["runs"]] == ["r1", "r2"]


def test_rebuild_catalog_on_empty_store_writes_empty_list(tmp_path):
    store = _store(tmp_path)
    assert store.rebuild_catalog() == 0
    assert store.backend.read_json("catalog.json") == {"schema_version": 1, "runs": []}


def test_write_run_rejects_mismatched_run_id_in_meta(tmp_path):
    store = _store(tmp_path)
    trades, stats, equity = _frames()
    with pytest.raises(ValueError, match="run_id"):
        store.write_run(run_id="r1", meta={"run_id": "OTHER"},
                        trades=trades, symbol_stats=stats, equity=equity)


def test_from_env_uses_experiments_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENTS_BACKEND", "local")
    monkeypatch.setenv("EXPERIMENTS_DIR", str(tmp_path / "store"))
    store = ExperimentStore.from_env()
    assert store.backend.base_uri() == str(tmp_path / "store")


def test_from_env_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("EXPERIMENTS_BACKEND", "r2")
    with pytest.raises(NotImplementedError, match="r2"):
        ExperimentStore.from_env()
