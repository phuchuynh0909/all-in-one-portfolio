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
