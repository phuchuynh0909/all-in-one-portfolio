# Experiment Analytics Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist vectorbt backtest runs as Parquet that notebooks query through a DuckDB view file and a React page queries directly with DuckDB-WASM, so a run can be drilled into and trades can be attributed across runs.

**Architecture:** `log_experiment(pf, ...)` extracts three tables from a vectorbt `Portfolio` and writes them as Parquet under `data/experiments/runs/<run_id>/`, plus a per-run `meta.json`. `catalog.json` is a full rebuild from those `meta.json` files, so it is always derived and never a shared mutable resource. `experiments.duckdb` holds views only. The browser fetches `catalog.json`, then runs SQL over the Parquet with DuckDB-WASM — no backend endpoint is involved for experiment data.

**Tech Stack:** Python 3.12, vectorbt 0.28.2, pandas, pyarrow, duckdb (new backend dep); React 18 + TypeScript + Vite 4, MUI 5, `@mui/x-data-grid` 5, TanStack Query 5, recharts 2, lightweight-charts 5, `@duckdb/duckdb-wasm` (new frontend dep).

**Spec:** `docs/superpowers/specs/2026-08-30-experiment-analytics-platform-design.md`

## Global Constraints

- **Repo rules:** `AGENTS.md` governs. Backend check is `cd backend && pytest tests` — never bare `pytest` from the repo root (it collects `testing/test_dnse_api.py`, which fires a live signed DNSE request at import time). Frontend checks are `cd frontend && npm run build` and `cd frontend && npm run lint` (lint runs `--max-warnings 0`).
- **No live calls in tests.** Every test in this plan is offline: synthetic `Portfolio` objects only. Never call `load_stocks`, DNSE, money24h, or wichart.
- **Python conventions:** loguru (`from loguru import logger`), Pydantic v2, SQLAlchemy 2.0 style, `from __future__ import annotations` at the top of new modules.
- **TypeScript conventions:** strict typing, MUI for UI, TanStack Query for data fetching. No raw SQL inside React components.
- **vectorbt is quarantined.** Only `backend/app/services/experiments/adapter.py` may import vectorbt.
- **Verified API facts (vectorbt 0.28.2, do not re-derive):**
  - `pf.trades.records` is a **pandas DataFrame** with columns `id, col, size, entry_idx, entry_price, entry_fees, exit_idx, exit_price, exit_fees, pnl, return, direction, status, parent_id`. `col` is an integer position into `pf.wrapper.columns`; `entry_idx`/`exit_idx` are integer positions into `pf.wrapper.index`.
  - `TradeDirection.Long == 0`, `TradeDirection.Short == 1`; `TradeStatus.Open == 0`, `TradeStatus.Closed == 1` (from `vectorbt.portfolio.enums`).
  - `pf.value()` returns a **DataFrame** (one column per symbol) when `pf.wrapper.grouper.is_grouped()` is False, and a **Series** when True.
  - `pf.sortino_ratio()` and `pf.trades.profit_factor()` can return `inf`. All floats must be cleaned to `None` before writing.
  - An empty-trade `Portfolio` still yields a 0-row `records` DataFrame with the full column set.
- **Storage constants:** `SCHEMA_VERSION = 1`; feature prefix is `feat_`; Parquet compression is `zstd`; run id format is `{name}__{%Y%m%dT%H%M%S}__{params_hash[:6]}`.
- **Outcome bucket labels (exact strings):** `1_catastrophic_loss`, `2_medium_loss`, `3_marginal`, `4_medium_win`, `5_big_win`. Default quantiles `[0.10, 0.30, 0.70, 0.90]`.
- **Deferred, do not build:** the R2 backend (protocol only), a pooled `trades.parquet`, per-symbol equity curves, per-bar signal series, strategy leaderboard, Optuna explorer.

## Deviations from the spec (deliberate, already validated)

1. **Extract from `pf.trades.records`, not `records_readable`.** `records` carries `entry_idx`/`exit_idx` (needed for `bars_held`) and uses stable snake_case names. `records_readable` is still used — as the independent cross-check in the binding test of Task 5.
2. **No `fcntl` lock on the catalog.** `catalog.json` is rebuilt in full from `runs/*/meta.json` after every write and written atomically via `os.replace`. A full rebuild is inherently correct under concurrency, so the lock the spec proposed is unnecessary.
3. **No frontend test runner is added.** The repo has none, and adding vitest for this work is out of scope. Instead the two risky analytical queries live in shared `.sql` files that TypeScript imports with Vite's `?raw` and the existing pytest suite executes against native DuckDB (same SQL engine as DuckDB-WASM). React components are covered by `npm run build` and `npm run lint`.

## File Structure

**Backend — `backend/app/services/experiments/`**

| File | Responsibility |
| --- | --- |
| `__init__.py` | Public surface: `log_experiment`, `ExperimentStore`, `RunHandle` |
| `schema.py` | Column constants, run-id construction, float/JSON cleaning. No I/O, no vectorbt. |
| `backends.py` | `StorageBackend` protocol + `LocalBackend`. Knows paths, not schemas. |
| `store.py` | `ExperimentStore`: write a run, rebuild catalog, rebuild DuckDB views. |
| `adapter.py` | The only vectorbt-aware file. Builds the three frames and calls the store. |

**Backend tests — `backend/tests/`**

| File | Responsibility |
| --- | --- |
| `experiments_fixtures.py` | Synthetic `Portfolio` builders (offline, deterministic) |
| `test_experiments_schema.py` | run-id and cleaning behaviour |
| `test_experiments_store.py` | write/rebuild/views round-trip |
| `test_experiments_adapter.py` | trade, stats, equity, feature extraction |
| `test_experiments_sql.py` | shared `.sql` files against native DuckDB |

**Frontend — `frontend/src/lib/experiments/`**

| File | Responsibility |
| --- | --- |
| `types.ts` | `RunMeta`, `Catalog`, row types |
| `catalog.ts` | Fetch + parse `catalog.json` (no DuckDB) |
| `db.ts` | Lazy DuckDB-WASM singleton, file registration |
| `queries.ts` | Typed query functions — the only place SQL is executed |
| `sql/outcome_buckets.sql` | Shared with pytest |
| `sql/feature_discrimination.sql` | Shared with pytest |

**Frontend — components**

| File | Responsibility |
| --- | --- |
| `frontend/src/pages/Experiments.tsx` | Route shell, run selection, tab routing |
| `frontend/src/components/experiments/RunList.tsx` | Run picker + multi-select |
| `frontend/src/components/experiments/OverviewTab.tsx` | Equity, metrics, params, symbol stats |
| `frontend/src/components/experiments/TradesTab.tsx` | Trade grid + filters |
| `frontend/src/components/experiments/AttributionTab.tsx` | Buckets + discrimination |
| `frontend/src/components/experiments/SymbolTab.tsx` | Price + entry/exit markers |

**Modified:** `backend/requirements.txt`, `.gitignore`, `frontend/package.json`, `frontend/src/vite-env.d.ts`, `frontend/src/App.tsx`, `notebooks/backtest_012.ipynb`.

---

### Task 1: Schema module

**Files:**
- Create: `backend/app/services/experiments/__init__.py`
- Create: `backend/app/services/experiments/schema.py`
- Test: `backend/tests/test_experiments_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SCHEMA_VERSION: int`, `FEATURE_PREFIX: str`, `CORE_TRADE_COLUMNS: list[str]`, `SYMBOL_STATS_COLUMNS: list[str]`, `EQUITY_COLUMNS: list[str]`, `OUTCOME_LABELS: list[str]`, `DEFAULT_QUANTILES: list[float]`, `make_run_id(name: str, params: Mapping[str, Any], created_at: datetime) -> str`, `params_hash(params: Mapping[str, Any]) -> str`, `clean_float(value: Any) -> float | None`, `json_safe(obj: Any) -> Any`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_experiments_schema.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.experiments'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/experiments/__init__.py
"""Experiment store: persist backtest runs as Parquet for DuckDB analysis."""
from __future__ import annotations

from app.services.experiments.schema import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
```

```python
# backend/app/services/experiments/schema.py
"""Column contracts and value cleaning for the experiment store.

Pure helpers: no I/O, no vectorbt. Everything here is safe to import from
tests and from notebooks.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from typing import Any, Mapping

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
FEATURE_PREFIX = "feat_"

CORE_TRADE_COLUMNS = [
    "run_id", "trade_id", "symbol",
    "entry_dt", "entry_price", "exit_dt", "exit_price",
    "size", "pnl", "ret", "net_return",
    "bars_held", "direction", "status", "exit_reason",
]

SYMBOL_STATS_COLUMNS = [
    "run_id", "symbol", "n_trades", "total_return", "sharpe", "sortino",
    "max_drawdown", "win_rate", "avg_win", "avg_loss", "profit_factor",
    "expectancy", "exposure",
]

EQUITY_COLUMNS = ["run_id", "dt", "value", "returns", "drawdown", "benchmark_value"]

OUTCOME_LABELS = [
    "1_catastrophic_loss", "2_medium_loss", "3_marginal",
    "4_medium_win", "5_big_win",
]
DEFAULT_QUANTILES = [0.10, 0.30, 0.70, 0.90]

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


def clean_float(value: Any) -> float | None:
    """Coerce to a JSON/Parquet-safe float, mapping NaN and +/-inf to None.

    vectorbt returns inf for zero-downside symbols (sortino) and zero-loss
    symbols (profit factor); those must not reach Parquet as inf, or SQL
    aggregates over them become inf too.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(out) else out


def json_safe(obj: Any) -> Any:
    """Recursively convert numpy/pandas values into JSON-serialisable ones."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return clean_float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(obj).isoformat()
    if isinstance(obj, np.ndarray):
        return [json_safe(v) for v in obj.tolist()]
    if isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


def params_hash(params: Mapping[str, Any] | None) -> str:
    """Six hex chars, stable across key ordering and numpy scalar types."""
    payload = json.dumps(json_safe(dict(params or {})), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:6]


def make_run_id(name: str, params: Mapping[str, Any] | None, created_at: datetime) -> str:
    safe_name = _UNSAFE_NAME.sub("-", name).strip("-") or "run"
    stamp = created_at.strftime("%Y%m%dT%H%M%S")
    return f"{safe_name}__{stamp}__{params_hash(params)}"
```

Then extend `__init__.py`'s `__all__` as later tasks add exports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_schema.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/experiments/__init__.py \
        backend/app/services/experiments/schema.py \
        backend/tests/test_experiments_schema.py
git commit -m "feat(experiments): schema constants and value cleaning"
```

---

### Task 2: Local storage backend

**Files:**
- Create: `backend/app/services/experiments/backends.py`
- Modify: `.gitignore`
- Test: `backend/tests/test_experiments_store.py` (created here, extended in Tasks 3–4)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `StorageBackend` protocol with `write_parquet(rel_path: str, df: pd.DataFrame) -> None`, `write_json(rel_path: str, obj: Any) -> None`, `read_json(rel_path: str) -> Any | None`, `list_run_ids() -> list[str]`, `base_uri() -> str`; and `LocalBackend(root: Path)` with an additional `root` attribute used by view generation.

Note: the spec listed four protocol methods. `list_run_ids()` is the fifth, required by `rebuild_catalog()` — the catalog cannot be derived without enumerating runs.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_experiments_store.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.experiments.backends'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/experiments/backends.py
"""Storage backends for the experiment store.

`LocalBackend` writes to a directory on disk. An `R2Backend` is deliberately
absent: the protocol exists so adding one later touches only this file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from app.services.experiments.schema import json_safe

PARQUET_COMPRESSION = "zstd"


@runtime_checkable
class StorageBackend(Protocol):
    def write_parquet(self, rel_path: str, df: pd.DataFrame) -> None: ...
    def write_json(self, rel_path: str, obj: Any) -> None: ...
    def read_json(self, rel_path: str) -> Any | None: ...
    def list_run_ids(self) -> list[str]: ...
    def base_uri(self) -> str: ...


@dataclass
class LocalBackend:
    """Filesystem backend rooted at `root`."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def _abs(self, rel_path: str) -> Path:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_parquet(self, rel_path: str, df: pd.DataFrame) -> None:
        df.to_parquet(self._abs(rel_path), index=False, compression=PARQUET_COMPRESSION)

    def write_json(self, rel_path: str, obj: Any) -> None:
        # Write-then-rename so a reader never observes a half-written file.
        path = self._abs(rel_path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(json_safe(obj), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def read_json(self, rel_path: str) -> Any | None:
        path = self.root / rel_path
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_run_ids(self) -> list[str]:
        runs_dir = self.root / "runs"
        if not runs_dir.exists():
            return []
        return sorted(
            d.name for d in runs_dir.iterdir()
            if d.is_dir() and (d / "meta.json").exists()
        )

    def base_uri(self) -> str:
        return str(self.root)
```

- [ ] **Step 4: Add the gitignore entry**

Append to `.gitignore`:

```
# Experiment store output (Parquet + DuckDB views); never committed.
data/experiments/
frontend/public/experiments
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_store.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/experiments/backends.py \
        backend/tests/test_experiments_store.py .gitignore
git commit -m "feat(experiments): local storage backend"
```

---

### Task 3: ExperimentStore write + derived catalog

**Files:**
- Create: `backend/app/services/experiments/store.py`
- Modify: `backend/app/services/experiments/__init__.py`
- Test: `backend/tests/test_experiments_store.py:appended`

**Interfaces:**
- Consumes: `LocalBackend`, `SCHEMA_VERSION`, `json_safe` (Tasks 1–2).
- Produces: `RunHandle` dataclass with fields `run_id: str`, `meta: dict`, `base_uri: str`; `ExperimentStore(backend: StorageBackend)` with `from_env() -> ExperimentStore` (classmethod), `write_run(run_id: str, meta: dict, trades: pd.DataFrame, symbol_stats: pd.DataFrame, equity: pd.DataFrame) -> RunHandle`, `rebuild_catalog() -> int`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_experiments_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.experiments.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/experiments/store.py
"""Writes experiment runs and keeps the derived catalog in sync."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from app.services.experiments.backends import LocalBackend, StorageBackend
from app.services.experiments.schema import SCHEMA_VERSION

CATALOG_PATH = "catalog.json"
DEFAULT_DIR = Path(__file__).resolve().parents[4] / "data" / "experiments"


@dataclass
class RunHandle:
    run_id: str
    meta: dict[str, Any] = field(default_factory=dict)
    base_uri: str = ""


class ExperimentStore:
    """Owns the write path. Knows nothing about vectorbt."""

    def __init__(self, backend: StorageBackend) -> None:
        self.backend = backend

    @classmethod
    def from_env(cls) -> "ExperimentStore":
        kind = os.environ.get("EXPERIMENTS_BACKEND", "local").lower()
        if kind != "local":
            raise NotImplementedError(
                f"EXPERIMENTS_BACKEND={kind!r} is not implemented; only 'local' ships today"
            )
        root = Path(os.environ.get("EXPERIMENTS_DIR", str(DEFAULT_DIR)))
        return cls(backend=LocalBackend(root=root))

    def write_run(
        self,
        run_id: str,
        meta: dict[str, Any],
        trades: pd.DataFrame,
        symbol_stats: pd.DataFrame,
        equity: pd.DataFrame,
    ) -> RunHandle:
        if meta.get("run_id", run_id) != run_id:
            raise ValueError(
                f"meta run_id {meta.get('run_id')!r} does not match run_id {run_id!r}"
            )

        files = {
            "trades": f"runs/{run_id}/trades.parquet",
            "symbol_stats": f"runs/{run_id}/symbol_stats.parquet",
            "equity": f"runs/{run_id}/equity.parquet",
        }
        self.backend.write_parquet(files["trades"], trades)
        self.backend.write_parquet(files["symbol_stats"], symbol_stats)
        self.backend.write_parquet(files["equity"], equity)

        full_meta = {**meta, "run_id": run_id, "schema_version": SCHEMA_VERSION, "files": files}
        # meta.json is written last: a run directory without it is an incomplete
        # write and is skipped by list_run_ids(), so a crash mid-write is invisible.
        self.backend.write_json(f"runs/{run_id}/meta.json", full_meta)
        self.rebuild_catalog()

        logger.info("experiment run written run_id={} rows_trades={}", run_id, len(trades))
        return RunHandle(run_id=run_id, meta=full_meta, base_uri=self.backend.base_uri())

    def rebuild_catalog(self) -> int:
        """Regenerate catalog.json from every run's meta.json.

        A full rebuild rather than read-modify-write: the catalog is derived,
        so concurrent notebook writes cannot corrupt it — the loser of a race
        simply misses a run until the next rebuild.
        """
        runs = []
        for run_id in self.backend.list_run_ids():
            meta = self.backend.read_json(f"runs/{run_id}/meta.json")
            if meta is not None:
                runs.append(meta)
        self.backend.write_json(CATALOG_PATH, {"schema_version": SCHEMA_VERSION, "runs": runs})
        return len(runs)
```

Update `backend/app/services/experiments/__init__.py`:

```python
from app.services.experiments.schema import SCHEMA_VERSION
from app.services.experiments.store import ExperimentStore, RunHandle

__all__ = ["SCHEMA_VERSION", "ExperimentStore", "RunHandle"]
```

Note on `DEFAULT_DIR`: `store.py` is at `backend/app/services/experiments/store.py`, so `parents[4]` is the repo root. If the executor finds this resolves elsewhere, fix it by asserting `(DEFAULT_DIR.parents[0].name == "data")` and correcting the index — do not leave it wrong.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_store.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/experiments/store.py \
        backend/app/services/experiments/__init__.py \
        backend/tests/test_experiments_store.py
git commit -m "feat(experiments): run writer with derived catalog"
```

---

### Task 4: DuckDB view file

**Files:**
- Modify: `backend/app/services/experiments/store.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_experiments_store.py:appended`

**Interfaces:**
- Consumes: `ExperimentStore` (Task 3).
- Produces: `ExperimentStore.rebuild_views(db_path: Path | None = None) -> Path | None` — returns the path to `experiments.duckdb`, or `None` when the store holds no runs.

- [ ] **Step 1: Add the dependency**

Add to `backend/requirements.txt` (alphabetical neighbourhood of the existing entries):

```
duckdb>=1.1.0
```

Then: `cd backend && pip install 'duckdb>=1.1.0'`

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_experiments_store.py`:

```python
def test_rebuild_views_creates_queryable_views(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    store = _store(tmp_path)
    trades, stats, equity = _frames()
    store.write_run(run_id="r1", meta={"run_id": "r1", "name": "bt"},
                    trades=trades, symbol_stats=stats, equity=equity)

    db_path = store.rebuild_views()
    assert db_path is not None and db_path.exists()

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM symbol_stats").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM equity").fetchone()[0] == 1
        assert con.execute("SELECT run_id FROM runs").fetchone()[0] == "r1"
    finally:
        con.close()


def test_rebuild_views_unions_runs_with_different_feature_columns(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    store = _store(tmp_path)
    trades, stats, equity = _frames()

    a = trades.assign(run_id="r1", feat_rsi=[55.0])
    b = trades.assign(run_id="r2", feat_atr=[1.5])
    store.write_run(run_id="r1", meta={"run_id": "r1"}, trades=a,
                    symbol_stats=stats, equity=equity)
    store.write_run(run_id="r2", meta={"run_id": "r2"}, trades=b,
                    symbol_stats=stats, equity=equity)

    con = duckdb.connect(str(store.rebuild_views()), read_only=True)
    try:
        rows = con.execute(
            "SELECT run_id, feat_rsi, feat_atr FROM trades ORDER BY run_id"
        ).fetchall()
    finally:
        con.close()
    # Disjoint feature sets must union to NULL, not raise.
    assert rows == [("r1", 55.0, None), ("r2", None, 1.5)]


def test_rebuild_views_on_empty_store_returns_none(tmp_path):
    pytest.importorskip("duckdb")
    assert _store(tmp_path).rebuild_views() is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_store.py -k views -v`
Expected: FAIL — `AttributeError: 'ExperimentStore' object has no attribute 'rebuild_views'`

- [ ] **Step 4: Write minimal implementation**

Add to `store.py` (imports at top: `import duckdb`):

```python
    VIEWS_DB_NAME = "experiments.duckdb"

    def rebuild_views(self, db_path: Path | None = None) -> Path | None:
        """Regenerate experiments.duckdb as views over the Parquet files.

        The database holds no data — only views — so it is disposable and can
        be regenerated at any time. Requires a filesystem-backed store because
        the views use globs, which need directory listing.
        """
        backend = self.backend
        if not isinstance(backend, LocalBackend):
            raise NotImplementedError("rebuild_views requires a filesystem-backed store")
        if not backend.list_run_ids():
            return None

        root = backend.root
        target = Path(db_path) if db_path else root / self.VIEWS_DB_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        con = duckdb.connect(str(target))
        try:
            # DuckDB rejects prepared parameters in CREATE VIEW ("Unexpected
            # prepared parameter"), so the globs are inlined as literals.
            def _lit(path: Path) -> str:
                return "'" + str(path).replace("'", "''") + "'"

            con.execute(
                "CREATE OR REPLACE VIEW runs AS SELECT * FROM read_json_auto("
                f"{_lit(root / 'runs' / '*' / 'meta.json')}, union_by_name=true)"
            )
            for view, fname in [
                ("trades", "trades.parquet"),
                ("symbol_stats", "symbol_stats.parquet"),
                ("equity", "equity.parquet"),
            ]:
                con.execute(
                    f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet("
                    f"{_lit(root / 'runs' / '*' / fname)}, union_by_name=true)"
                )
        finally:
            con.close()

        logger.info("experiment views rebuilt at {}", target)
        return target
```

Call it from `write_run`, immediately after `self.rebuild_catalog()`:

```python
        self.rebuild_catalog()
        self.rebuild_views()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_store.py -v`
Expected: PASS — 16 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/experiments/store.py backend/requirements.txt \
        backend/tests/test_experiments_store.py
git commit -m "feat(experiments): duckdb view file over run parquet"
```

---

### Task 5: Adapter — trade extraction

**Files:**
- Create: `backend/app/services/experiments/adapter.py`
- Create: `backend/tests/experiments_fixtures.py`
- Test: `backend/tests/test_experiments_adapter.py`

**Interfaces:**
- Consumes: `CORE_TRADE_COLUMNS`, `clean_float` (Task 1).
- Produces: `UnmappedVectorbtColumns(RuntimeError)`; `build_trades(pf, run_id: str) -> pd.DataFrame` returning exactly `CORE_TRADE_COLUMNS` in order.

- [ ] **Step 1: Write the fixture module**

```python
# backend/tests/experiments_fixtures.py
"""Deterministic synthetic vectorbt portfolios. Offline: no data loader, no network."""
from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt


def make_portfolio(*, n_bars: int = 30, grouped: bool = False, no_trades: bool = False):
    """Two symbols, one rising and one falling, with two round trips each."""
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    close = pd.DataFrame(
        {"AAA": np.linspace(10, 15, n_bars), "BBB": np.linspace(20, 18, n_bars)},
        index=idx,
    )
    entries = pd.DataFrame(False, index=idx, columns=close.columns)
    exits = pd.DataFrame(False, index=idx, columns=close.columns)
    if not no_trades:
        entries.iloc[[2, 15], :] = True
        exits.iloc[[8, 22], :] = True

    kwargs = dict(close=close, entries=entries, exits=exits, freq="1d", init_cash=100)
    if grouped:
        return vbt.Portfolio.from_signals(**kwargs, group_by=True, cash_sharing=True)
    return vbt.Portfolio.from_signals(**kwargs, cash_sharing=False)


def make_open_trade_portfolio():
    """One entry with no exit, so the final trade is still open."""
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    close = pd.DataFrame({"AAA": np.linspace(10, 15, 10)}, index=idx)
    entries = pd.DataFrame(False, index=idx, columns=close.columns)
    entries.iloc[1, 0] = True
    exits = pd.DataFrame(False, index=idx, columns=close.columns)
    return vbt.Portfolio.from_signals(
        close=close, entries=entries, exits=exits, freq="1d",
        cash_sharing=False, init_cash=100,
    )
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_experiments_adapter.py
"""Adapter tests: vectorbt Portfolio -> store frames. Offline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.experiments.adapter import UnmappedVectorbtColumns, build_trades
from app.services.experiments.schema import CORE_TRADE_COLUMNS
from tests.experiments_fixtures import make_open_trade_portfolio, make_portfolio


def test_build_trades_returns_core_columns_in_order():
    df = build_trades(make_portfolio(), run_id="r1")
    assert list(df.columns) == CORE_TRADE_COLUMNS


def test_build_trades_maps_symbol_and_timestamps():
    pf = make_portfolio()
    df = build_trades(pf, run_id="r1")

    assert set(df["symbol"]) == {"AAA", "BBB"}
    assert df["run_id"].unique().tolist() == ["r1"]
    # Entry index 2 on a 2024-01-01 daily index is 2024-01-03.
    first = df[df["symbol"] == "AAA"].iloc[0]
    assert first["entry_dt"] == pd.Timestamp("2024-01-03")
    assert first["exit_dt"] == pd.Timestamp("2024-01-09")
    assert first["bars_held"] == 6


def test_build_trades_net_return_matches_vectorbt_readable_records():
    """The binding assertion: if extraction drifts, this fails.

    Cross-checked against records_readable, an independent vectorbt API from
    the records DataFrame the adapter actually reads.
    """
    pf = make_portfolio()
    df = build_trades(pf, run_id="r1").sort_values("trade_id").reset_index(drop=True)
    readable = pf.trades.records_readable.sort_values("Exit Trade Id").reset_index(drop=True)

    assert len(df) == len(readable) > 0
    np.testing.assert_allclose(df["net_return"].to_numpy(),
                               readable["Return"].to_numpy(), rtol=1e-12)
    np.testing.assert_allclose(df["pnl"].to_numpy(),
                               readable["PnL"].to_numpy(), rtol=1e-12)
    assert df["symbol"].tolist() == readable["Column"].tolist()


def test_build_trades_decodes_direction_and_status():
    df = build_trades(make_portfolio(), run_id="r1")
    assert set(df["direction"]) == {"long"}
    assert set(df["status"]) == {"closed"}


def test_build_trades_nulls_exit_fields_for_open_trades():
    df = build_trades(make_open_trade_portfolio(), run_id="r1")
    open_rows = df[df["status"] == "open"]
    assert len(open_rows) == 1
    assert pd.isna(open_rows.iloc[0]["exit_dt"])
    assert pd.isna(open_rows.iloc[0]["exit_price"])
    assert pd.isna(open_rows.iloc[0]["bars_held"])


def test_build_trades_exit_reason_defaults_to_null():
    df = build_trades(make_portfolio(), run_id="r1")
    assert df["exit_reason"].isna().all()


def test_build_trades_gross_ret_is_at_least_net_return_without_fees():
    df = build_trades(make_portfolio(), run_id="r1")
    # Fixture has zero fees, so gross and net coincide.
    np.testing.assert_allclose(df["ret"].to_numpy(), df["net_return"].to_numpy(), rtol=1e-12)


def test_build_trades_on_empty_portfolio_returns_empty_typed_frame():
    df = build_trades(make_portfolio(no_trades=True), run_id="r1")
    assert len(df) == 0
    assert list(df.columns) == CORE_TRADE_COLUMNS


def test_build_trades_raises_on_missing_vectorbt_column():
    class FakeTrades:
        records = pd.DataFrame({"id": [0], "col": [0]})  # missing everything else

    class FakePf:
        trades = FakeTrades()

    with pytest.raises(UnmappedVectorbtColumns, match="entry_idx"):
        build_trades(FakePf(), run_id="r1")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.experiments.adapter'`

- [ ] **Step 4: Write minimal implementation**

```python
# backend/app/services/experiments/adapter.py
"""The only vectorbt-aware module in the experiment store.

vectorbt renames record columns between releases. Every column this module
depends on is listed in REQUIRED_RECORD_COLUMNS and checked up front, so an
incompatible version fails loudly at log time instead of writing NULLs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.experiments.schema import CORE_TRADE_COLUMNS

REQUIRED_RECORD_COLUMNS = [
    "id", "col", "size", "entry_idx", "entry_price", "entry_fees",
    "exit_idx", "exit_price", "exit_fees", "pnl", "return", "direction", "status",
]

# vectorbt.portfolio.enums.TradeDirection / TradeStatus, inlined so the module
# does not depend on the enum import path surviving upgrades.
_DIRECTION = {0: "long", 1: "short"}
_STATUS = {0: "open", 1: "closed"}
_STATUS_CLOSED = 1


class UnmappedVectorbtColumns(RuntimeError):
    """Raised when the installed vectorbt exposes an unexpected record schema."""


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in CORE_TRADE_COLUMNS})


def build_trades(pf, run_id: str) -> pd.DataFrame:
    """Extract one row per exit trade from a vectorbt Portfolio."""
    rec = pf.trades.records
    missing = [c for c in REQUIRED_RECORD_COLUMNS if c not in rec.columns]
    if missing:
        raise UnmappedVectorbtColumns(
            f"vectorbt trade records are missing {missing}; "
            f"got {list(rec.columns)}. Update REQUIRED_RECORD_COLUMNS and the "
            f"extraction in adapter.py for this vectorbt version."
        )
    if len(rec) == 0:
        return _empty_trades()

    columns = pd.Index(pf.wrapper.columns)
    index = pd.DatetimeIndex(pf.wrapper.index)

    entry_idx = rec["entry_idx"].to_numpy(dtype="int64")
    exit_idx = rec["exit_idx"].to_numpy(dtype="int64")
    is_closed = rec["status"].to_numpy() == _STATUS_CLOSED

    exit_dt = pd.Series(index[exit_idx], dtype="datetime64[ns]")
    exit_dt[~is_closed] = pd.NaT
    exit_price = rec["exit_price"].astype(float).to_numpy()
    exit_price = np.where(is_closed, exit_price, np.nan)
    bars_held = np.where(is_closed, (exit_idx - entry_idx).astype(float), np.nan)

    size = rec["size"].astype(float).to_numpy()
    entry_price = rec["entry_price"].astype(float).to_numpy()
    fees = rec["entry_fees"].astype(float).to_numpy() + rec["exit_fees"].astype(float).to_numpy()
    cost = entry_price * size
    with np.errstate(divide="ignore", invalid="ignore"):
        gross = np.where(cost != 0, (rec["pnl"].astype(float).to_numpy() + fees) / cost, np.nan)

    out = pd.DataFrame({
        "run_id": run_id,
        "trade_id": rec["id"].astype("int64").to_numpy(),
        "symbol": columns[rec["col"].to_numpy(dtype="int64")].astype(str),
        "entry_dt": index[entry_idx],
        "entry_price": entry_price,
        "exit_dt": exit_dt.to_numpy(),
        "exit_price": exit_price,
        "size": size,
        "pnl": rec["pnl"].astype(float).to_numpy(),
        "ret": gross,
        "net_return": rec["return"].astype(float).to_numpy(),
        "bars_held": bars_held,
        "direction": [_DIRECTION.get(int(d), "unknown") for d in rec["direction"]],
        "status": [_STATUS.get(int(s), "unknown") for s in rec["status"]],
        # vectorbt does not record why a position closed; callers supply it.
        "exit_reason": pd.Series([None] * len(rec), dtype="object"),
    })
    return out[CORE_TRADE_COLUMNS].reset_index(drop=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_adapter.py -v`
Expected: PASS — 9 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/experiments/adapter.py \
        backend/tests/experiments_fixtures.py \
        backend/tests/test_experiments_adapter.py
git commit -m "feat(experiments): vectorbt trade extraction"
```

---

### Task 6: Adapter — symbol stats and composite equity

**Files:**
- Modify: `backend/app/services/experiments/adapter.py`
- Test: `backend/tests/test_experiments_adapter.py:appended`

**Interfaces:**
- Consumes: `build_trades` (Task 5), `clean_float`, `SYMBOL_STATS_COLUMNS`, `EQUITY_COLUMNS` (Task 1).
- Produces: `build_symbol_stats(pf, run_id: str, trades: pd.DataFrame) -> pd.DataFrame`; `build_equity(pf, run_id: str, benchmark: pd.Series | None = None) -> tuple[pd.DataFrame, str]` where the second element is `"mean"` or `"portfolio"`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_experiments_adapter.py`:

```python
from app.services.experiments.adapter import build_equity, build_symbol_stats
from app.services.experiments.schema import EQUITY_COLUMNS, SYMBOL_STATS_COLUMNS


def test_build_symbol_stats_has_one_row_per_symbol():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    assert list(stats.columns) == SYMBOL_STATS_COLUMNS
    assert sorted(stats["symbol"]) == ["AAA", "BBB"]
    assert stats["run_id"].unique().tolist() == ["r1"]


def test_build_symbol_stats_matches_vectorbt_total_return():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    expected = pf.total_return()
    got = stats.set_index("symbol")["total_return"]
    for symbol, value in expected.items():
        np.testing.assert_allclose(got[symbol], value, rtol=1e-12)


def test_build_symbol_stats_replaces_infinities_with_null():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    numeric = stats.drop(columns=["run_id", "symbol"])
    assert not np.isinf(numeric.to_numpy(dtype="float64")).any(), "inf must be cleaned to NULL"
    # AAA has no losing trades, so vectorbt reports inf profit factor.
    assert pd.isna(stats.set_index("symbol").loc["AAA", "profit_factor"])


def test_build_symbol_stats_derives_exposure_and_trade_counts():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    stats = build_symbol_stats(pf, run_id="r1", trades=trades).set_index("symbol")
    assert stats.loc["AAA", "n_trades"] == 2
    # Two trades of 6 and 7 bars over 30 bars.
    expected = trades[trades.symbol == "AAA"]["bars_held"].sum() / len(pf.wrapper.index)
    np.testing.assert_allclose(stats.loc["AAA", "exposure"], expected, rtol=1e-12)


def test_build_equity_ungrouped_is_equal_weight_composite():
    pf = make_portfolio()
    equity, agg = build_equity(pf, run_id="r1")
    assert agg == "mean"
    assert list(equity.columns) == EQUITY_COLUMNS
    np.testing.assert_allclose(equity["value"].to_numpy(),
                               pf.value().mean(axis=1).to_numpy(), rtol=1e-12)


def test_build_equity_grouped_uses_the_portfolio_curve():
    pf = make_portfolio(grouped=True)
    equity, agg = build_equity(pf, run_id="r1")
    assert agg == "portfolio"
    np.testing.assert_allclose(equity["value"].to_numpy(),
                               np.asarray(pf.value()), rtol=1e-12)


def test_build_equity_drawdown_is_non_positive_and_starts_at_zero():
    equity, _ = build_equity(make_portfolio(), run_id="r1")
    dd = equity["drawdown"].to_numpy()
    assert dd[0] == 0
    assert (dd <= 1e-12).all()


def test_build_equity_attaches_benchmark_when_given():
    pf = make_portfolio()
    bench = pd.Series(100.0, index=pf.wrapper.index)
    equity, _ = build_equity(pf, run_id="r1", benchmark=bench)
    assert equity["benchmark_value"].notna().all()


def test_build_equity_benchmark_is_null_when_absent():
    equity, _ = build_equity(make_portfolio(), run_id="r1")
    assert equity["benchmark_value"].isna().all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_adapter.py -k "symbol_stats or equity" -v`
Expected: FAIL — `ImportError: cannot import name 'build_equity'`

- [ ] **Step 3: Write minimal implementation**

Add to `adapter.py` (extend imports with `SYMBOL_STATS_COLUMNS`, `EQUITY_COLUMNS`, `clean_float`):

```python
def _as_symbol_series(value, columns: pd.Index) -> pd.Series:
    """Normalise a vectorbt metric into a Series indexed by symbol."""
    if isinstance(value, pd.Series):
        return value.reindex(columns)
    return pd.Series(np.repeat(np.asarray(value), len(columns))[: len(columns)], index=columns)


def build_symbol_stats(pf, run_id: str, trades: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol. Non-finite metrics are cleaned to NULL."""
    columns = pd.Index(pf.wrapper.columns).astype(str)
    n_bars = len(pf.wrapper.index)

    metrics = {
        "total_return": pf.total_return(),
        "sharpe": pf.sharpe_ratio(),
        "sortino": pf.sortino_ratio(),
        "max_drawdown": pf.max_drawdown(),
        "win_rate": pf.trades.win_rate(),
        "profit_factor": pf.trades.profit_factor(),
        "expectancy": pf.trades.expectancy(),
    }
    frame = pd.DataFrame(
        {name: _as_symbol_series(value, columns).to_numpy() for name, value in metrics.items()},
        index=columns,
    )

    # Derived from the trade frame rather than more vectorbt API surface.
    by_symbol = trades.groupby("symbol", dropna=False)
    n_trades = by_symbol.size().reindex(columns).fillna(0).astype("int64")
    wins = trades[trades["net_return"] > 0].groupby("symbol")["net_return"].mean()
    losses = trades[trades["net_return"] <= 0].groupby("symbol")["net_return"].mean()
    frame["avg_win"] = wins.reindex(columns).to_numpy()
    frame["avg_loss"] = losses.reindex(columns).to_numpy()
    held = by_symbol["bars_held"].sum().reindex(columns).fillna(0)
    frame["exposure"] = (held / n_bars).to_numpy() if n_bars else np.nan

    # DataFrame.map (pandas >= 2.1); the repo is on 2.2.2. applymap is deprecated.
    frame = frame.map(clean_float)
    frame["n_trades"] = n_trades
    frame.insert(0, "symbol", columns)
    frame.insert(0, "run_id", run_id)
    return frame.reset_index(drop=True)[SYMBOL_STATS_COLUMNS]


def build_equity(pf, run_id: str, benchmark: pd.Series | None = None) -> tuple[pd.DataFrame, str]:
    """Portfolio equity curve.

    With cash_sharing=False every symbol is an independent book, so there is
    no single traded curve; the equal-weight mean across symbols is stored and
    labelled agg="mean" so the UI never implies a real portfolio.
    """
    value = pf.value()
    if isinstance(value, pd.DataFrame):
        series, agg = value.mean(axis=1), "mean"
    else:
        series, agg = pd.Series(np.asarray(value), index=pf.wrapper.index), "portfolio"

    running_max = series.cummax()
    drawdown = (series / running_max - 1.0).where(running_max != 0, 0.0)

    bench = (
        pd.Series(np.asarray(benchmark), index=pd.DatetimeIndex(benchmark.index)).reindex(series.index)
        if benchmark is not None
        else pd.Series(np.nan, index=series.index)
    )

    frame = pd.DataFrame({
        "run_id": run_id,
        "dt": pd.DatetimeIndex(series.index),
        "value": series.to_numpy(dtype="float64"),
        "returns": series.pct_change().to_numpy(dtype="float64"),
        "drawdown": drawdown.to_numpy(dtype="float64"),
        "benchmark_value": bench.to_numpy(dtype="float64"),
    })
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame[EQUITY_COLUMNS].reset_index(drop=True), agg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_adapter.py -v`
Expected: PASS — 18 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/experiments/adapter.py backend/tests/test_experiments_adapter.py
git commit -m "feat(experiments): symbol stats and composite equity extraction"
```

---

### Task 7: Adapter — feature join and `log_experiment`

**Files:**
- Modify: `backend/app/services/experiments/adapter.py`
- Modify: `backend/app/services/experiments/__init__.py`
- Test: `backend/tests/test_experiments_adapter.py:appended`

**Interfaces:**
- Consumes: `build_trades`, `build_symbol_stats`, `build_equity` (Tasks 5–6); `ExperimentStore`, `RunHandle` (Task 3); `make_run_id`, `json_safe`, `clean_float`, `FEATURE_PREFIX` (Task 1).
- Produces: `FeatureCollisionError(ValueError)`; `attach_features(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame`; `log_experiment(pf, name: str, params: Mapping | None = None, tags: Sequence[str] | None = None, features: pd.DataFrame | None = None, benchmark: pd.Series | None = None, exit_reasons: pd.DataFrame | None = None, notes: str | None = None, notebook: str | None = None, store: ExperimentStore | None = None) -> RunHandle`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_experiments_adapter.py`:

```python
from app.services.experiments.adapter import (
    FeatureCollisionError,
    attach_features,
    log_experiment,
)
from app.services.experiments.backends import LocalBackend
from app.services.experiments.store import ExperimentStore


def _features_for(pf):
    trades = build_trades(pf, run_id="r1")
    return pd.DataFrame({
        "symbol": trades["symbol"],
        "entry_dt": trades["entry_dt"],
        "rsi": np.arange(len(trades), dtype="float64"),
    })


def test_attach_features_prefixes_columns():
    pf = make_portfolio()
    out = attach_features(build_trades(pf, run_id="r1"), _features_for(pf))
    assert "feat_rsi" in out.columns
    assert "rsi" not in out.columns
    assert out["feat_rsi"].notna().all()


def test_attach_features_joins_on_symbol_and_entry_dt():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    features = _features_for(pf).iloc[[0]]  # only the first trade has a feature
    out = attach_features(trades, features)
    assert out["feat_rsi"].notna().sum() == 1
    assert len(out) == len(trades), "join must not drop trades"


def test_attach_features_rejects_a_core_column_collision():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    bad = _features_for(pf).rename(columns={"rsi": "pnl"})
    # 'pnl' would become 'feat_pnl', which is fine; a literal 'feat_' name that
    # collides with an existing trade column is the failure case.
    ok = attach_features(trades, bad)
    assert "feat_pnl" in ok.columns

    trades2 = trades.assign(feat_rsi=1.0)
    with pytest.raises(FeatureCollisionError, match="feat_rsi"):
        attach_features(trades2, _features_for(pf))


def test_attach_features_requires_join_keys():
    pf = make_portfolio()
    with pytest.raises(ValueError, match="entry_dt"):
        attach_features(build_trades(pf, run_id="r1"), pd.DataFrame({"symbol": ["AAA"]}))


def test_log_experiment_writes_a_queryable_run(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pf = make_portfolio()
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))

    handle = log_experiment(pf, name="bt 012", params={"a": 1}, tags=["oos"],
                            features=_features_for(pf), notes="hello", store=store)

    assert handle.run_id.startswith("bt-012__")
    assert handle.meta["tags"] == ["oos"]
    assert handle.meta["equity_agg"] == "mean"
    assert handle.meta["n_symbols"] == 2
    assert handle.meta["n_trades"] == 4
    assert handle.meta["metrics"]["mean_total_return"] is not None

    con = duckdb.connect(str(tmp_path / "experiments.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM symbol_stats").fetchone()[0] == 2
        assert con.execute("SELECT count(DISTINCT feat_rsi) FROM trades").fetchone()[0] == 4
    finally:
        con.close()


def test_log_experiment_records_source_metadata(tmp_path):
    pf = make_portfolio()
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))
    handle = log_experiment(pf, name="bt", store=store, notebook="notebooks/backtest_012.ipynb")
    assert handle.meta["source"]["notebook"] == "notebooks/backtest_012.ipynb"
    assert "git_sha" in handle.meta["source"]


def test_log_experiment_applies_exit_reasons(tmp_path):
    pf = make_portfolio()
    trades = build_trades(pf, run_id="x")
    reasons = pd.DataFrame({
        "symbol": trades["symbol"].iloc[:1],
        "entry_dt": trades["entry_dt"].iloc[:1],
        "exit_reason": ["stop_loss"],
    })
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))
    handle = log_experiment(pf, name="bt", exit_reasons=reasons, store=store)

    written = pd.read_parquet(tmp_path / "runs" / handle.run_id / "trades.parquet")
    assert written["exit_reason"].notna().sum() == 1
    assert set(written["exit_reason"].dropna()) == {"stop_loss"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_adapter.py -k "features or log_experiment" -v`
Expected: FAIL — `ImportError: cannot import name 'attach_features'`

- [ ] **Step 3: Write minimal implementation**

Add to `adapter.py`. Extend the imports with exactly:

```python
import subprocess
from datetime import datetime, timezone
from typing import Mapping, Sequence

from loguru import logger

from app.services.experiments.schema import FEATURE_PREFIX, clean_float, json_safe, make_run_id
from app.services.experiments.store import ExperimentStore, RunHandle
```

Then add:

```python
class FeatureCollisionError(ValueError):
    """A supplied feature column would overwrite an existing trade column."""


_JOIN_KEYS = ["symbol", "entry_dt"]


def attach_features(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Left-join per-trade features, prefixing every value column with feat_."""
    missing = [k for k in _JOIN_KEYS if k not in features.columns]
    if missing:
        raise ValueError(f"features must contain join keys {_JOIN_KEYS}; missing {missing}")

    value_cols = [c for c in features.columns if c not in _JOIN_KEYS]
    renamed = features.rename(columns={c: f"{FEATURE_PREFIX}{c}" for c in value_cols})
    clashing = [c for c in renamed.columns if c not in _JOIN_KEYS and c in trades.columns]
    if clashing:
        raise FeatureCollisionError(
            f"feature columns {clashing} already exist on the trade frame; "
            "rename them before logging"
        )

    out = trades.merge(renamed, on=_JOIN_KEYS, how="left", validate="many_to_one")
    if len(out) != len(trades):
        raise ValueError("feature join changed the trade row count; keys are not unique")
    return out


def _apply_exit_reasons(trades: pd.DataFrame, exit_reasons: pd.DataFrame) -> pd.DataFrame:
    missing = [k for k in _JOIN_KEYS + ["exit_reason"] if k not in exit_reasons.columns]
    if missing:
        raise ValueError(f"exit_reasons must contain {_JOIN_KEYS + ['exit_reason']}; missing {missing}")
    merged = trades.drop(columns=["exit_reason"]).merge(
        exit_reasons[_JOIN_KEYS + ["exit_reason"]], on=_JOIN_KEYS, how="left", validate="many_to_one"
    )
    return merged


def _git_source(notebook: str | None) -> dict:
    def _git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except Exception:  # git absent, not a repo, or timed out — never fatal
            return None

    return {
        "notebook": notebook,
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")) or None,
    }


def log_experiment(
    pf,
    name: str,
    params: Mapping[str, object] | None = None,
    tags: Sequence[str] | None = None,
    features: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    exit_reasons: pd.DataFrame | None = None,
    notes: str | None = None,
    notebook: str | None = None,
    store: ExperimentStore | None = None,
) -> RunHandle:
    """Persist a vectorbt Portfolio as an experiment run."""
    store = store or ExperimentStore.from_env()
    created_at = datetime.now(timezone.utc)
    run_id = make_run_id(name, params, created_at)

    trades = build_trades(pf, run_id=run_id)
    if exit_reasons is not None and len(trades):
        trades = _apply_exit_reasons(trades, exit_reasons)
    if features is not None and len(trades):
        trades = attach_features(trades, features)

    symbol_stats = build_symbol_stats(pf, run_id=run_id, trades=trades)
    equity, equity_agg = build_equity(pf, run_id=run_id, benchmark=benchmark)

    total_return = symbol_stats["total_return"].dropna()
    meta = {
        "run_id": run_id,
        "name": name,
        "created_at": created_at.isoformat(),
        "tags": list(tags or []),
        "params": json_safe(dict(params or {})),
        "notes": notes,
        "data_start": str(pd.Timestamp(pf.wrapper.index[0]).date()),
        "data_end": str(pd.Timestamp(pf.wrapper.index[-1]).date()),
        "n_symbols": int(len(pd.Index(pf.wrapper.columns))),
        "n_trades": int(len(trades)),
        "equity_agg": equity_agg,
        "metrics": {
            "mean_total_return": clean_float(total_return.mean()),
            "mean_sharpe": clean_float(symbol_stats["sharpe"].dropna().mean()),
            "pct_symbols_positive": clean_float((total_return > 0).mean()),
        },
        "source": _git_source(notebook),
        "feature_columns": [c for c in trades.columns if c.startswith(FEATURE_PREFIX)],
    }

    logger.info("logging experiment name={} run_id={} trades={}", name, run_id, len(trades))
    return store.write_run(run_id=run_id, meta=meta, trades=trades,
                           symbol_stats=symbol_stats, equity=equity)
```

Update `__init__.py`:

```python
from app.services.experiments.adapter import log_experiment
from app.services.experiments.schema import SCHEMA_VERSION
from app.services.experiments.store import ExperimentStore, RunHandle

__all__ = ["SCHEMA_VERSION", "ExperimentStore", "RunHandle", "log_experiment"]
```

- [ ] **Step 4: Run the whole backend suite**

Run: `cd backend && pytest tests -q`
Expected: PASS — all experiment tests green, no pre-existing test broken.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/experiments/ backend/tests/test_experiments_adapter.py
git commit -m "feat(experiments): feature join and log_experiment entry point"
```

---

### Task 8: Shared analytical SQL

**Files:**
- Create: `frontend/src/lib/experiments/sql/outcome_buckets.sql`
- Create: `frontend/src/lib/experiments/sql/feature_discrimination.sql`
- Test: `backend/tests/test_experiments_sql.py`

**Interfaces:**
- Consumes: nothing.
- Produces: two SQL files, each expecting a relation named `trades_src` to exist in the session and taking one parameter — a `DOUBLE[]` of four quantile cut points. `outcome_buckets.sql` returns every `trades_src` column plus `outcome`. `feature_discrimination.sql` returns `feature, n_obs, coverage, loser_mean, winner_mean, sd, separation`.

Both statements were validated against DuckDB 1.5.5 before this plan was written. Two findings are baked into the SQL below and must not be "simplified" away:
- DuckDB list indexing is **1-based**, so the cut points are `cuts[1]`..`cuts[4]`.
- `UNPIVOT` **drops NULL values** and does not accept `INCLUDE NULLS` in the dynamic `COLUMNS(...)` form. Coverage therefore divides by a separately computed total trade count, not by `count(*)` over the unpivoted rows — otherwise coverage always reads 1.0 and a 10%-populated feature looks fully populated.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_experiments_sql.py
"""The analytical SQL shared with the frontend, run against native DuckDB.

DuckDB-WASM uses the same SQL engine, so proving these here proves the
queries the browser runs. The .sql files live under frontend/ because
TypeScript imports them with Vite's `?raw`; this test is the only reason
the backend reaches across.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

SQL_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "experiments" / "sql"
QUANTILES = [0.10, 0.30, 0.70, 0.90]


def _read(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


@pytest.fixture()
def con():
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({
        "run_id": "r1",
        "trade_id": np.arange(n),
        "symbol": rng.choice(list("ABC"), n),
        "net_return": rng.normal(0.01, 0.08, n),
        "feat_rsi": rng.normal(50, 10, n),
        "feat_atr": rng.normal(2, 0.5, n),
    })
    df.loc[df.net_return < -0.08, "feat_rsi"] += 25   # inject a real signal
    df.loc[:19, "feat_atr"] = np.nan                  # 180/200 coverage
    connection = duckdb.connect()
    connection.register("trades_src", df)
    yield connection
    connection.close()


def test_outcome_buckets_partition_all_trades(con):
    out = con.execute(_read("outcome_buckets.sql"), [QUANTILES]).df()
    assert len(out) == 200
    assert set(out["outcome"]) == {
        "1_catastrophic_loss", "2_medium_loss", "3_marginal", "4_medium_win", "5_big_win",
    }
    counts = out["outcome"].value_counts()
    assert counts["1_catastrophic_loss"] == 20   # 10% quantile
    assert counts["5_big_win"] == 20             # top 10%
    assert counts["3_marginal"] == 80            # 30%-70%


def test_outcome_buckets_respect_custom_quantiles(con):
    # Collapsing each pair of cut points makes the two intermediate buckets
    # unreachable, leaving a 25/50/25 split across the outer three.
    out = con.execute(_read("outcome_buckets.sql"), [[0.25, 0.25, 0.75, 0.75]]).df()
    counts = out["outcome"].value_counts()
    assert counts.get("2_medium_loss", 0) == 0
    assert counts.get("4_medium_win", 0) == 0
    assert counts["1_catastrophic_loss"] == 50
    assert counts["3_marginal"] == 100
    assert counts["5_big_win"] == 50


def test_outcome_buckets_preserve_source_columns(con):
    out = con.execute(_read("outcome_buckets.sql"), [QUANTILES]).df()
    for column in ["run_id", "trade_id", "symbol", "net_return", "feat_rsi"]:
        assert column in out.columns


def test_feature_discrimination_ranks_the_injected_signal_first(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df()
    assert out.iloc[0]["feature"] == "feat_rsi"
    assert abs(out.iloc[0]["separation"]) > abs(out.iloc[1]["separation"])


def test_feature_discrimination_reports_true_coverage(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df().set_index("feature")
    assert out.loc["feat_rsi", "coverage"] == pytest.approx(1.0)
    # 180 of 200 trades have feat_atr. A coverage of 1.0 here means UNPIVOT
    # silently dropped the NULLs -- the bug this assertion exists to catch.
    assert out.loc["feat_atr", "coverage"] == pytest.approx(0.90)
    assert out.loc["feat_atr", "n_obs"] == 180


def test_feature_discrimination_returns_one_row_per_feature(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df()
    assert sorted(out["feature"]) == ["feat_atr", "feat_rsi"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_experiments_sql.py -v`
Expected: FAIL — `FileNotFoundError: .../sql/outcome_buckets.sql`

- [ ] **Step 3: Write the SQL**

```sql
-- frontend/src/lib/experiments/sql/outcome_buckets.sql
-- Segments trades into outcome buckets by net_return quantiles.
--
-- Expects a relation named `trades_src` in the session.
-- Parameter 1: DOUBLE[4] cut points, ascending (default [0.10,0.30,0.70,0.90]).
-- DuckDB lists are 1-indexed.
WITH q AS (
    SELECT quantile_cont(net_return, ?::DOUBLE[]) AS cuts
    FROM trades_src
    WHERE net_return IS NOT NULL
)
SELECT
    t.*,
    CASE
        WHEN t.net_return <= q.cuts[1] THEN '1_catastrophic_loss'
        WHEN t.net_return <= q.cuts[2] THEN '2_medium_loss'
        WHEN t.net_return <= q.cuts[3] THEN '3_marginal'
        WHEN t.net_return <= q.cuts[4] THEN '4_medium_win'
        ELSE '5_big_win'
    END AS outcome
FROM trades_src t
CROSS JOIN q
```

```sql
-- frontend/src/lib/experiments/sql/feature_discrimination.sql
-- Ranks feat_* columns by how well they separate the worst trades from the best.
--
-- Expects a relation named `trades_src` in the session.
-- Parameter 1: DOUBLE[4] quantile cut points, ascending.
--
-- UNPIVOT drops NULL values and rejects INCLUDE NULLS in this dynamic
-- COLUMNS(...) form, so coverage divides by the trade count from n_total.
-- Using count(*) over the unpivoted rows would always yield 1.0.
WITH q AS (
    SELECT quantile_cont(net_return, ?::DOUBLE[]) AS cuts
    FROM trades_src
    WHERE net_return IS NOT NULL
),
bucketed AS (
    SELECT
        t.*,
        CASE
            WHEN t.net_return <= q.cuts[1] THEN '1_catastrophic_loss'
            WHEN t.net_return <= q.cuts[2] THEN '2_medium_loss'
            WHEN t.net_return <= q.cuts[3] THEN '3_marginal'
            WHEN t.net_return <= q.cuts[4] THEN '4_medium_win'
            ELSE '5_big_win'
        END AS outcome
    FROM trades_src t
    CROSS JOIN q
),
n_total AS (
    SELECT count(*) AS n FROM bucketed
),
long AS (
    UNPIVOT bucketed
    ON COLUMNS('^feat_')
    INTO NAME feature VALUE value
),
agg AS (
    SELECT
        l.feature,
        count(l.value) AS n_obs,
        count(l.value)::DOUBLE / n_total.n AS coverage,
        avg(l.value) FILTER (WHERE l.outcome = '1_catastrophic_loss') AS loser_mean,
        avg(l.value) FILTER (WHERE l.outcome = '5_big_win') AS winner_mean,
        stddev_samp(l.value) AS sd
    FROM long l
    CROSS JOIN n_total
    GROUP BY l.feature, n_total.n
)
SELECT
    feature, n_obs, coverage, loser_mean, winner_mean, sd,
    (winner_mean - loser_mean) / nullif(sd, 0) AS separation
FROM agg
ORDER BY abs((winner_mean - loser_mean) / nullif(sd, 0)) DESC NULLS LAST
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_experiments_sql.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/experiments/sql/ backend/tests/test_experiments_sql.py
git commit -m "feat(experiments): shared outcome-bucket and discrimination SQL"
```

---

### Task 9: Frontend types, catalog client, and route

**Files:**
- Create: `frontend/src/lib/experiments/types.ts`
- Create: `frontend/src/lib/experiments/catalog.ts`
- Create: `frontend/src/pages/Experiments.tsx` (placeholder shell, filled in Task 11)
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/vite-env.d.ts`

**Interfaces:**
- Consumes: `catalog.json` written by Task 3.
- Produces: types `RunMeta`, `Catalog`, `TradeRow`, `SymbolStatRow`, `EquityRow`, `OutcomeRow`, `DiscriminationRow`; `EXPERIMENTS_BASE_URL: string`; `fetchCatalog(): Promise<Catalog>`; `useCatalog()` TanStack Query hook returning `UseQueryResult<Catalog>`.

- [ ] **Step 1: Declare the `?raw` module type**

Append to `frontend/src/vite-env.d.ts`:

```ts
declare module '*.sql?raw' {
  const content: string;
  export default content;
}
```

- [ ] **Step 2: Write the types**

```ts
// frontend/src/lib/experiments/types.ts
export interface RunMeta {
  run_id: string;
  name: string;
  created_at: string;
  tags: string[];
  params: Record<string, unknown>;
  notes: string | null;
  data_start: string;
  data_end: string;
  n_symbols: number;
  n_trades: number;
  /** 'mean' = equal-weight composite across independent per-symbol books. */
  equity_agg: 'mean' | 'portfolio';
  metrics: {
    mean_total_return: number | null;
    mean_sharpe: number | null;
    pct_symbols_positive: number | null;
  };
  source: { notebook: string | null; git_sha: string | null; dirty: boolean | null };
  feature_columns: string[];
  files: { trades: string; symbol_stats: string; equity: string };
  schema_version: number;
}

export interface Catalog {
  schema_version: number;
  runs: RunMeta[];
}

export interface TradeRow {
  run_id: string;
  trade_id: number;
  symbol: string;
  entry_dt: string;
  entry_price: number;
  exit_dt: string | null;
  exit_price: number | null;
  size: number;
  pnl: number;
  ret: number | null;
  net_return: number;
  bars_held: number | null;
  direction: string;
  status: string;
  exit_reason: string | null;
  outcome?: string;
  [feature: string]: unknown;
}

export interface SymbolStatRow {
  run_id: string;
  symbol: string;
  n_trades: number;
  total_return: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  exposure: number | null;
}

export interface EquityRow {
  run_id: string;
  dt: string;
  value: number;
  returns: number | null;
  drawdown: number | null;
  benchmark_value: number | null;
}

export interface OutcomeRow {
  outcome: string;
  n: number;
  mean_net_return: number | null;
}

export interface DiscriminationRow {
  feature: string;
  n_obs: number;
  coverage: number;
  loser_mean: number | null;
  winner_mean: number | null;
  sd: number | null;
  separation: number | null;
}
```

- [ ] **Step 3: Write the catalog client**

```ts
// frontend/src/lib/experiments/catalog.ts
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { Catalog } from './types';

/** Served as static files; see the symlink step in the plan's Task 12. */
export const EXPERIMENTS_BASE_URL =
  (import.meta.env.VITE_EXPERIMENTS_BASE_URL as string | undefined) ?? '/experiments';

export function experimentFileUrl(relPath: string): string {
  return `${EXPERIMENTS_BASE_URL}/${relPath}`;
}

export async function fetchCatalog(): Promise<Catalog> {
  const res = await fetch(`${EXPERIMENTS_BASE_URL}/catalog.json`, { cache: 'no-store' });
  if (res.status === 404) {
    // An empty store is a normal state, not an error.
    return { schema_version: 1, runs: [] };
  }
  if (!res.ok) {
    throw new Error(`Failed to load experiment catalog: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as Catalog;
}

export function useCatalog(): UseQueryResult<Catalog> {
  return useQuery({ queryKey: ['experiments', 'catalog'], queryFn: fetchCatalog });
}
```

- [ ] **Step 4: Add the placeholder page and route**

```tsx
// frontend/src/pages/Experiments.tsx
import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import { useCatalog } from '../lib/experiments/catalog';

export default function Experiments() {
  const { data, isLoading, error } = useCatalog();

  if (isLoading) return <CircularProgress />;
  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;
  if (!data?.runs.length) {
    return (
      <Alert severity="info">
        No experiments yet. Run <code>log_experiment(pf, name=...)</code> in a notebook,
        or repair a missing catalog with <code>ExperimentStore.from_env().rebuild_catalog()</code>.
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="h5">Experiments</Typography>
      <Typography variant="body2">{data.runs.length} runs</Typography>
    </Box>
  );
}
```

In `frontend/src/App.tsx`, import the page alongside the existing page imports and add the route next to `/backtest-viz`:

```tsx
<Route path="/experiments" element={<Experiments />} />
```

Add a matching nav entry wherever `/backtest-viz` appears in the nav list in `App.tsx`, following the existing entry's exact shape.

- [ ] **Step 5: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/experiments/types.ts frontend/src/lib/experiments/catalog.ts \
        frontend/src/pages/Experiments.tsx frontend/src/App.tsx frontend/src/vite-env.d.ts
git commit -m "feat(experiments): catalog client and route"
```

---

### Task 10: DuckDB-WASM query layer

**Files:**
- Create: `frontend/src/lib/experiments/db.ts`
- Create: `frontend/src/lib/experiments/queries.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: `RunMeta`, row types (Task 9); the two `.sql` files (Task 8).
- Produces: `getConnection(): Promise<AsyncDuckDBConnection>`; `registerRunFiles(runs: RunMeta[]): Promise<void>`; and query functions `getEquity(run: RunMeta): Promise<EquityRow[]>`, `getSymbolStats(run: RunMeta): Promise<SymbolStatRow[]>`, `getTrades(run: RunMeta): Promise<TradeRow[]>`, `getOutcomeBuckets(runs: RunMeta[], quantiles?: number[]): Promise<TradeRow[]>`, `getFeatureDiscrimination(runs: RunMeta[], quantiles?: number[]): Promise<DiscriminationRow[]>`.

- [ ] **Step 1: Add the dependency**

Run: `cd frontend && npm install @duckdb/duckdb-wasm`

- [ ] **Step 2: Write the lazy DuckDB singleton**

```ts
// frontend/src/lib/experiments/db.ts
import type { AsyncDuckDB, AsyncDuckDBConnection } from '@duckdb/duckdb-wasm';
import { experimentFileUrl } from './catalog';
import type { RunMeta } from './types';

let dbPromise: Promise<AsyncDuckDB> | null = null;
let connPromise: Promise<AsyncDuckDBConnection> | null = null;
const registered = new Set<string>();

/**
 * The WASM bundle is ~3 MB, so it is imported dynamically: pages other than
 * Experiments never pay for it.
 */
async function createDb(): Promise<AsyncDuckDB> {
  const duckdb = await import('@duckdb/duckdb-wasm');
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker!}");`], { type: 'text/javascript' }),
  );
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  return db;
}

export async function getConnection(): Promise<AsyncDuckDBConnection> {
  if (!dbPromise) dbPromise = createDb();
  if (!connPromise) connPromise = dbPromise.then((db) => db.connect());
  return connPromise;
}

/**
 * Registers each run's Parquet by URL so DuckDB fetches byte ranges rather
 * than whole files. HTTP has no directory listing, so DuckDB cannot glob —
 * the file list always comes from the catalog.
 */
export async function registerRunFiles(runs: RunMeta[]): Promise<void> {
  const duckdb = await import('@duckdb/duckdb-wasm');
  const db = await (dbPromise ?? (dbPromise = createDb()));
  for (const run of runs) {
    for (const rel of Object.values(run.files)) {
      if (registered.has(rel)) continue;
      await db.registerFileURL(rel, experimentFileUrl(rel), duckdb.DuckDBDataProtocol.HTTP, false);
      registered.add(rel);
    }
  }
}

export function parquetList(runs: RunMeta[], table: keyof RunMeta['files']): string {
  const files = runs.map((r) => `'${r.files[table]}'`).join(', ');
  return `read_parquet([${files}], union_by_name=true)`;
}
```

- [ ] **Step 3: Write the query surface**

```ts
// frontend/src/lib/experiments/queries.ts
import outcomeBucketsSql from './sql/outcome_buckets.sql?raw';
import featureDiscriminationSql from './sql/feature_discrimination.sql?raw';
import { getConnection, parquetList, registerRunFiles } from './db';
import type {
  DiscriminationRow, EquityRow, RunMeta, SymbolStatRow, TradeRow,
} from './types';

export const DEFAULT_QUANTILES = [0.1, 0.3, 0.7, 0.9];

async function run<T>(sql: string, runs: RunMeta[], params?: unknown[]): Promise<T[]> {
  await registerRunFiles(runs);
  const conn = await getConnection();
  if (!params?.length) {
    return (await conn.query(sql)).toArray().map((r) => r.toJSON() as T);
  }
  const stmt = await conn.prepare(sql);
  try {
    return (await stmt.query(...params)).toArray().map((r) => r.toJSON() as T);
  } finally {
    await stmt.close();
  }
}

export function getEquity(runMeta: RunMeta): Promise<EquityRow[]> {
  return run<EquityRow>(
    `SELECT * FROM ${parquetList([runMeta], 'equity')} ORDER BY dt`, [runMeta],
  );
}

export function getSymbolStats(runMeta: RunMeta): Promise<SymbolStatRow[]> {
  return run<SymbolStatRow>(
    `SELECT * FROM ${parquetList([runMeta], 'symbol_stats')} ORDER BY total_return DESC NULLS LAST`,
    [runMeta],
  );
}

export function getTrades(runMeta: RunMeta): Promise<TradeRow[]> {
  return run<TradeRow>(
    `SELECT * FROM ${parquetList([runMeta], 'trades')} ORDER BY entry_dt`, [runMeta],
  );
}

/** Trades with an `outcome` column, bucketed by net_return quantiles. */
export function getOutcomeBuckets(
  runs: RunMeta[], quantiles: number[] = DEFAULT_QUANTILES,
): Promise<TradeRow[]> {
  const sql = outcomeBucketsSql.replace(/trades_src/g, parquetList(runs, 'trades'));
  return run<TradeRow>(sql, runs, [quantiles]);
}

export function getFeatureDiscrimination(
  runs: RunMeta[], quantiles: number[] = DEFAULT_QUANTILES,
): Promise<DiscriminationRow[]> {
  const sql = featureDiscriminationSql.replace(/trades_src/g, parquetList(runs, 'trades'));
  return run<DiscriminationRow>(sql, runs, [quantiles]);
}
```

If the WASM bundle fails to load (blocked worker, unsupported browser), the rejection from `createDb()` propagates through every query function into the TanStack Query `error` state, which each tab renders as an MUI `Alert` — so a blocked runtime shows a message rather than a blank tab.

Note the `trades_src` substitution: the `.sql` files are written against a relation of that name so pytest can `register()` a DataFrame under it, while the browser swaps in a `read_parquet([...])` expression. Keep the placeholder name exactly `trades_src` in both files.

- [ ] **Step 4: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/experiments/db.ts frontend/src/lib/experiments/queries.ts \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(experiments): duckdb-wasm query layer"
```

---

### Task 11: Run list and Overview tab

**Files:**
- Create: `frontend/src/components/experiments/RunList.tsx`
- Create: `frontend/src/components/experiments/OverviewTab.tsx`
- Modify: `frontend/src/pages/Experiments.tsx`

**Interfaces:**
- Consumes: `useCatalog` (Task 9), `getEquity`, `getSymbolStats` (Task 10).
- Produces: `RunList` props `{ runs: RunMeta[]; selectedId: string | null; onSelect: (id: string) => void; comparedIds: string[]; onToggleCompare: (id: string) => void }`; `OverviewTab` props `{ run: RunMeta }`.

- [ ] **Step 1: Write the run list**

```tsx
// frontend/src/components/experiments/RunList.tsx
import { useMemo, useState } from 'react';
import {
  Checkbox, Chip, List, ListItemButton, ListItemText, Stack, TextField, Typography,
} from '@mui/material';
import type { RunMeta } from '../../lib/experiments/types';

interface Props {
  runs: RunMeta[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  comparedIds: string[];
  onToggleCompare: (id: string) => void;
}

export default function RunList({
  runs, selectedId, onSelect, comparedIds, onToggleCompare,
}: Props) {
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter(
      (r) => r.name.toLowerCase().includes(q) || r.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [runs, search]);

  return (
    <Stack spacing={1}>
      <TextField
        size="small" label="Search runs or tags" value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <List dense disablePadding>
        {filtered.map((run) => (
          <ListItemButton
            key={run.run_id}
            selected={run.run_id === selectedId}
            onClick={() => onSelect(run.run_id)}
          >
            <Checkbox
              edge="start" size="small"
              checked={comparedIds.includes(run.run_id)}
              onClick={(e) => { e.stopPropagation(); onToggleCompare(run.run_id); }}
            />
            <ListItemText
              primary={run.name}
              secondary={
                <>
                  <Typography variant="caption" component="span">
                    {new Date(run.created_at).toLocaleDateString()} · {run.n_trades} trades
                  </Typography>
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
                    {run.tags.map((t) => <Chip key={t} label={t} size="small" />)}
                  </Stack>
                </>
              }
              secondaryTypographyProps={{ component: 'div' }}
            />
          </ListItemButton>
        ))}
      </List>
    </Stack>
  );
}
```

- [ ] **Step 2: Write the Overview tab**

```tsx
// frontend/src/components/experiments/OverviewTab.tsx
import { useQuery } from '@tanstack/react-query';
import {
  Alert, Box, Card, CardContent, CircularProgress, Grid, Stack, Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { getEquity, getSymbolStats } from '../../lib/experiments/queries';
import type { RunMeta } from '../../lib/experiments/types';

const pct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null) => (v == null ? '—' : v.toFixed(3));

const STAT_COLUMNS: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 110 },
  { field: 'n_trades', headerName: 'Trades', width: 90, type: 'number' },
  { field: 'total_return', headerName: 'Total Return', width: 130, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'sharpe', headerName: 'Sharpe', width: 100, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'sortino', headerName: 'Sortino', width: 100, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'max_drawdown', headerName: 'Max DD', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'win_rate', headerName: 'Win Rate', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'exposure', headerName: 'Exposure', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
];

export default function OverviewTab({ run }: { run: RunMeta }) {
  const equity = useQuery({
    queryKey: ['experiments', run.run_id, 'equity'],
    queryFn: () => getEquity(run),
  });
  const stats = useQuery({
    queryKey: ['experiments', run.run_id, 'symbol_stats'],
    queryFn: () => getSymbolStats(run),
  });

  return (
    <Stack spacing={2}>
      <Grid container spacing={2}>
        {[
          ['Mean total return', pct(run.metrics.mean_total_return)],
          ['Mean Sharpe', num(run.metrics.mean_sharpe)],
          ['Symbols positive', pct(run.metrics.pct_symbols_positive)],
          ['Trades', String(run.n_trades)],
        ].map(([label, value]) => (
          <Grid item xs={6} md={3} key={label}>
            <Card><CardContent>
              <Typography variant="caption" color="text.secondary">{label}</Typography>
              <Typography variant="h6">{value}</Typography>
            </CardContent></Card>
          </Grid>
        ))}
      </Grid>

      <Card><CardContent>
        <Typography variant="subtitle2">
          Equity —{' '}
          {run.equity_agg === 'mean'
            ? 'equal-weight composite of independent per-symbol books'
            : 'cash-shared portfolio'}
        </Typography>
        {equity.isLoading && <CircularProgress size={20} />}
        {equity.error && <Alert severity="error">{(equity.error as Error).message}</Alert>}
        {equity.data && (
          <Box sx={{ height: 320 }}>
            <ResponsiveContainer>
              <LineChart data={equity.data}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="dt" tickFormatter={(v) => String(v).slice(0, 10)} minTickGap={40} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="value" name="Strategy" dot={false} />
                <Line type="monotone" dataKey="benchmark_value" name="Benchmark" dot={false}
                      strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </Box>
        )}
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Parameters</Typography>
        <Box component="pre" sx={{ m: 0, fontSize: 12, overflowX: 'auto' }}>
          {JSON.stringify(run.params, null, 2)}
        </Box>
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Per-symbol stats</Typography>
        {stats.error && <Alert severity="error">{(stats.error as Error).message}</Alert>}
        <Box sx={{ height: 420 }}>
          <DataGrid
            rows={(stats.data ?? []).map((r, i) => ({ id: i, ...r }))}
            columns={STAT_COLUMNS}
            loading={stats.isLoading}
            density="compact"
          />
        </Box>
      </CardContent></Card>
    </Stack>
  );
}
```

- [ ] **Step 3: Write the page shell**

```tsx
// frontend/src/pages/Experiments.tsx
import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, CircularProgress, Grid, Paper, Tab, Tabs, Typography,
} from '@mui/material';
import RunList from '../components/experiments/RunList';
import OverviewTab from '../components/experiments/OverviewTab';
import { useCatalog } from '../lib/experiments/catalog';

export default function Experiments() {
  const { data, isLoading, error } = useCatalog();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  const [tab, setTab] = useState(0);

  const runs = useMemo(
    () => [...(data?.runs ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [data],
  );

  useEffect(() => {
    if (!selectedId && runs.length) setSelectedId(runs[0].run_id);
  }, [runs, selectedId]);

  const selectedRun = runs.find((r) => r.run_id === selectedId) ?? null;

  const toggleCompare = (id: string) =>
    setComparedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  if (isLoading) return <CircularProgress />;
  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;
  if (!runs.length) {
    return (
      <Alert severity="info">
        No experiments yet. Run <code>log_experiment(pf, name=...)</code> in a notebook,
        or repair a missing catalog with <code>ExperimentStore.from_env().rebuild_catalog()</code>.
      </Alert>
    );
  }

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={3}>
        <Paper sx={{ p: 1, maxHeight: '80vh', overflowY: 'auto' }}>
          <RunList
            runs={runs}
            selectedId={selectedId}
            onSelect={setSelectedId}
            comparedIds={comparedIds}
            onToggleCompare={toggleCompare}
          />
        </Paper>
      </Grid>
      <Grid item xs={12} md={9}>
        {selectedRun && (
          <>
            <Typography variant="h6">{selectedRun.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {selectedRun.data_start} to {selectedRun.data_end} ·{' '}
              {selectedRun.n_symbols} symbols · {selectedRun.n_trades} trades
            </Typography>
            <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
              <Tab label="Overview" />
              <Tab label="Trades" />
              <Tab label="Attribution" />
              <Tab label="Symbol" />
            </Tabs>
            <Box>{tab === 0 && <OverviewTab run={selectedRun} />}</Box>
            {/* Tabs 1-3 are filled in by Tasks 12, 13 and 14. */}
          </>
        )}
      </Grid>
    </Grid>
  );
}
```

Tasks 12–14 add `selectedSymbol` state and a `pooledRuns` computation to this file. They are
deliberately absent here because `npm run lint` runs with `--max-warnings 0`, and an unused
binding would fail the check in this task.

- [ ] **Step 4: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/experiments/ frontend/src/pages/Experiments.tsx
git commit -m "feat(experiments): run list and overview tab"
```

---

### Task 12: Trades tab

**Files:**
- Create: `frontend/src/components/experiments/TradesTab.tsx`
- Modify: `frontend/src/pages/Experiments.tsx`

**Interfaces:**
- Consumes: `getOutcomeBuckets`, `DEFAULT_QUANTILES` (Task 10).
- Produces: `TradesTab` props `{ run: RunMeta; onSelectSymbol: (symbol: string) => void }`.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/components/experiments/TradesTab.tsx
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Box, MenuItem, Stack, TextField } from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { DEFAULT_QUANTILES, getOutcomeBuckets } from '../../lib/experiments/queries';
import type { RunMeta, TradeRow } from '../../lib/experiments/types';

const OUTCOMES = [
  '1_catastrophic_loss', '2_medium_loss', '3_marginal', '4_medium_win', '5_big_win',
];

const COLUMNS: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 100 },
  { field: 'entry_dt', headerName: 'Entry', width: 120,
    valueFormatter: ({ value }) => String(value ?? '').slice(0, 10) },
  { field: 'exit_dt', headerName: 'Exit', width: 120,
    valueFormatter: ({ value }) => String(value ?? '').slice(0, 10) },
  { field: 'net_return', headerName: 'Net Return', width: 120, type: 'number',
    valueFormatter: ({ value }) =>
      value == null ? '—' : `${((value as number) * 100).toFixed(2)}%` },
  { field: 'bars_held', headerName: 'Bars', width: 80, type: 'number' },
  { field: 'exit_reason', headerName: 'Exit Reason', width: 140,
    valueFormatter: ({ value }) => (value as string | null) ?? '—' },
  { field: 'outcome', headerName: 'Outcome', width: 170 },
];

export default function TradesTab({
  run, onSelectSymbol,
}: { run: RunMeta; onSelectSymbol: (symbol: string) => void }) {
  const [outcome, setOutcome] = useState('');
  const [symbol, setSymbol] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['experiments', run.run_id, 'buckets', DEFAULT_QUANTILES],
    queryFn: () => getOutcomeBuckets([run], DEFAULT_QUANTILES),
  });

  const rows = useMemo(() => {
    const all: TradeRow[] = data ?? [];
    return all
      .filter((t) => (outcome ? t.outcome === outcome : true))
      .filter((t) => (symbol ? t.symbol.toUpperCase().includes(symbol.toUpperCase()) : true))
      .map((t, i) => ({ id: i, ...t }));
  }, [data, outcome, symbol]);

  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2}>
        <TextField size="small" label="Symbol" value={symbol}
                   onChange={(e) => setSymbol(e.target.value)} />
        <TextField select size="small" label="Outcome" value={outcome} sx={{ minWidth: 200 }}
                   onChange={(e) => setOutcome(e.target.value)}>
          <MenuItem value="">All</MenuItem>
          {OUTCOMES.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
        </TextField>
      </Stack>
      <Box sx={{ height: 560 }}>
        <DataGrid
          rows={rows} columns={COLUMNS} loading={isLoading} density="compact"
          onRowClick={(p) => onSelectSymbol(String((p.row as TradeRow).symbol))}
        />
      </Box>
    </Stack>
  );
}
```

- [ ] **Step 2: Wire it into the page**

In `frontend/src/pages/Experiments.tsx`, add the symbol state next to the existing `useState` calls:

```tsx
const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
```

Import the tab and render it beneath the Overview line:

```tsx
import TradesTab from '../components/experiments/TradesTab';
// ...
<Box>{tab === 0 && <OverviewTab run={selectedRun} />}</Box>
<Box>
  {tab === 1 && (
    <TradesTab
      run={selectedRun}
      onSelectSymbol={(symbol) => { setSelectedSymbol(symbol); setTab(3); }}
    />
  )}
</Box>
```

`selectedSymbol` is read by `SymbolTab` in Task 14. Until then it is written but not read,
which `npm run lint` (`--max-warnings 0`) accepts — assignment counts as use for
`@typescript-eslint/no-unused-vars`, unlike an unused binding.

- [ ] **Step 3: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/experiments/TradesTab.tsx frontend/src/pages/Experiments.tsx
git commit -m "feat(experiments): trades tab"
```

---

### Task 13: Attribution tab

**Files:**
- Create: `frontend/src/components/experiments/AttributionTab.tsx`
- Modify: `frontend/src/pages/Experiments.tsx`

**Interfaces:**
- Consumes: `getOutcomeBuckets`, `getFeatureDiscrimination` (Task 10).
- Produces: `AttributionTab` props `{ runs: RunMeta[] }` — the pooled multi-select from `RunList`, falling back to the selected run when nothing is checked.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/components/experiments/AttributionTab.tsx
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert, Box, Card, CardContent, Chip, Stack, Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  DEFAULT_QUANTILES, getFeatureDiscrimination, getOutcomeBuckets,
} from '../../lib/experiments/queries';
import type { DiscriminationRow, RunMeta, TradeRow } from '../../lib/experiments/types';

const num = (v: number | null) => (v == null ? '—' : v.toFixed(3));

const DISC_COLUMNS: GridColDef[] = [
  { field: 'feature', headerName: 'Feature', width: 200 },
  { field: 'separation', headerName: 'Separation (σ)', width: 150, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'loser_mean', headerName: 'Worst 10% mean', width: 160, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'winner_mean', headerName: 'Best 10% mean', width: 160, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'coverage', headerName: 'Coverage', width: 120, type: 'number',
    valueFormatter: ({ value }) =>
      value == null ? '—' : `${((value as number) * 100).toFixed(0)}%` },
  { field: 'n_obs', headerName: 'N', width: 90, type: 'number' },
];

export default function AttributionTab({ runs }: { runs: RunMeta[] }) {
  const key = runs.map((r) => r.run_id).sort().join('|');

  const buckets = useQuery({
    queryKey: ['experiments', 'pooled-buckets', key],
    queryFn: () => getOutcomeBuckets(runs, DEFAULT_QUANTILES),
    enabled: runs.length > 0,
  });
  const disc = useQuery({
    queryKey: ['experiments', 'pooled-discrimination', key],
    queryFn: () => getFeatureDiscrimination(runs, DEFAULT_QUANTILES),
    enabled: runs.length > 0,
  });

  const bucketCounts = useMemo(() => {
    const counts = new Map<string, { outcome: string; n: number; sum: number }>();
    for (const t of (buckets.data ?? []) as TradeRow[]) {
      const o = String(t.outcome);
      const prev = counts.get(o) ?? { outcome: o, n: 0, sum: 0 };
      counts.set(o, { outcome: o, n: prev.n + 1, sum: prev.sum + Number(t.net_return ?? 0) });
    }
    return [...counts.values()]
      .sort((a, b) => a.outcome.localeCompare(b.outcome))
      .map((r) => ({ ...r, mean_net_return: r.n ? r.sum / r.n : 0 }));
  }, [buckets.data]);

  const lowCoverage = ((disc.data ?? []) as DiscriminationRow[]).filter((d) => d.coverage < 0.5);

  if (!runs.length) return <Alert severity="info">Select at least one run.</Alert>;
  if (buckets.error) return <Alert severity="error">{(buckets.error as Error).message}</Alert>;

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
        {runs.map((r) => <Chip key={r.run_id} label={r.name} size="small" />)}
      </Stack>

      <Card><CardContent>
        <Typography variant="subtitle2">Outcome distribution</Typography>
        <Box sx={{ height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={bucketCounts}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="outcome" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="n" name="Trades" />
            </BarChart>
          </ResponsiveContainer>
        </Box>
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Feature discrimination</Typography>
        {lowCoverage.length > 0 && (
          <Alert severity="warning" sx={{ mb: 1 }}>
            {lowCoverage.length} feature(s) are present on under half the pooled trades;
            read their separation with care.
          </Alert>
        )}
        {disc.error && <Alert severity="error">{(disc.error as Error).message}</Alert>}
        <Box sx={{ height: 420 }}>
          <DataGrid
            rows={((disc.data ?? []) as DiscriminationRow[]).map((r, i) => ({ id: i, ...r }))}
            columns={DISC_COLUMNS}
            loading={disc.isLoading}
            density="compact"
          />
        </Box>
      </CardContent></Card>
    </Stack>
  );
}
```

- [ ] **Step 2: Wire it into the page**

In `frontend/src/pages/Experiments.tsx`, add the pooled-run computation just after `selectedRun`:

```tsx
const pooledRuns = comparedIds.length
  ? runs.filter((r) => comparedIds.includes(r.run_id))
  : selectedRun ? [selectedRun] : [];
```

Import and render the tab:

```tsx
import AttributionTab from '../components/experiments/AttributionTab';
// ...
<Box>{tab === 2 && <AttributionTab runs={pooledRuns} />}</Box>
```

- [ ] **Step 3: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/experiments/AttributionTab.tsx frontend/src/pages/Experiments.tsx
git commit -m "feat(experiments): attribution tab"
```

---

### Task 14: Symbol tab with entry/exit markers

**Files:**
- Create: `frontend/src/components/experiments/SymbolTab.tsx`
- Modify: `frontend/src/pages/Experiments.tsx`

**Interfaces:**
- Consumes: `getTrades` (Task 10); the existing price API used by `frontend/src/pages/Chart.tsx`.
- Produces: `SymbolTab` props `{ run: RunMeta; symbol: string | null }`.

- [ ] **Step 1: Note the APIs this reuses (already verified against the repo)**

- OHLC comes from `fetchTimeseries(symbol, { interval: '1d', start_date, end_date })` in
  `frontend/src/lib/services/timeseries.ts`. It returns a `TimeseriesResponse` holding parallel
  arrays: `timestamps: string[]` and `timeseries.{open,high,low,close,volume}: number[]` — not
  ready-made candle objects. Convert timestamps with `formatChartTime` from the same module.
- This repo is on **lightweight-charts v5**: series are added with
  `chart.addSeries(CandlestickSeries, {...})` and markers attached with
  `createSeriesMarkers(series, markers)`. The v4 `addCandlestickSeries()` and
  `series.setMarkers()` do **not** exist here.
- `frontend/src/components/backtest/BacktestChart.tsx` already renders candles with trade
  markers and is the reference implementation; mirror its idiom.

- [ ] **Step 2: Write the component**

```tsx
// frontend/src/components/experiments/SymbolTab.tsx
import { useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Box, Stack, Typography } from '@mui/material';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { fetchTimeseries, formatChartTime } from '../../lib/services/timeseries';
import { getTrades } from '../../lib/experiments/queries';
import type { RunMeta, TradeRow } from '../../lib/experiments/types';

export default function SymbolTab({ run, symbol }: { run: RunMeta; symbol: string | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null);

  const bars = useQuery({
    queryKey: ['experiments', 'ohlc', symbol, run.data_start, run.data_end],
    queryFn: () =>
      fetchTimeseries(symbol as string, {
        interval: '1d',
        start_date: run.data_start,
        end_date: run.data_end,
      }),
    enabled: Boolean(symbol),
  });

  const trades = useQuery({
    queryKey: ['experiments', run.run_id, 'trades'],
    queryFn: () => getTrades(run),
    enabled: Boolean(symbol),
  });

  const candles = useMemo(() => {
    if (!bars.data) return [];
    const { timestamps, timeseries } = bars.data;
    return timestamps.map((ts, i) => ({
      time: formatChartTime(ts),
      open: timeseries.open[i],
      high: timeseries.high[i],
      low: timeseries.low[i],
      close: timeseries.close[i],
    }));
  }, [bars.data]);

  const markers = useMemo<SeriesMarker<UTCTimestamp>[]>(() => {
    const rows = ((trades.data ?? []) as TradeRow[]).filter((t) => t.symbol === symbol);
    return rows
      .flatMap((t) => {
        const entry: SeriesMarker<UTCTimestamp> = {
          time: formatChartTime(String(t.entry_dt)),
          position: 'belowBar',
          color: '#22c55e',
          shape: 'arrowUp',
          text: `BUY @${Number(t.entry_price).toFixed(2)}`,
        };
        if (!t.exit_dt) return [entry];
        const net = Number(t.net_return ?? 0);
        const exit: SeriesMarker<UTCTimestamp> = {
          time: formatChartTime(String(t.exit_dt)),
          position: 'aboveBar',
          color: net >= 0 ? '#3b82f6' : '#ef4444',
          shape: 'arrowDown',
          text: `${(net * 100).toFixed(1)}%`,
        };
        return [entry, exit];
      })
      .sort((a, b) => Number(a.time) - Number(b.time));
  }, [trades.data, symbol]);

  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;
    const chart = createChart(containerRef.current, { height: 460 });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !candles.length) return;
    series.setData(candles);
    markersRef.current = createSeriesMarkers(series, markers);
    chartRef.current?.timeScale().fitContent();
  }, [candles, markers]);

  if (!symbol) return <Alert severity="info">Pick a symbol from Overview or Trades.</Alert>;
  if (bars.error) return <Alert severity="error">{(bars.error as Error).message}</Alert>;
  if (trades.error) return <Alert severity="error">{(trades.error as Error).message}</Alert>;

  return (
    <Stack spacing={1}>
      <Typography variant="subtitle2">
        {symbol} — {markers.length} markers across {candles.length} bars
      </Typography>
      <Box ref={containerRef} />
    </Stack>
  );
}
```

- [ ] **Step 3: Wire it into the page**

In `frontend/src/pages/Experiments.tsx`:

```tsx
import SymbolTab from '../components/experiments/SymbolTab';
// ...
<Box>{tab === 3 && <SymbolTab run={selectedRun} symbol={selectedSymbol} />}</Box>
```

Also make the Overview per-symbol grid navigate here: add an `onSelectSymbol` prop to
`OverviewTab` with the same signature `TradesTab` uses, wire it to the `DataGrid`'s
`onRowClick={(p) => onSelectSymbol(String((p.row as SymbolStatRow).symbol))}`, and pass
`onSelectSymbol={(symbol) => { setSelectedSymbol(symbol); setTab(3); }}` from the page.

- [ ] **Step 4: Verify build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint reports 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/experiments/SymbolTab.tsx frontend/src/pages/Experiments.tsx
git commit -m "feat(experiments): symbol tab with trade markers"
```

---

### Task 15: End-to-end proof from a notebook

**Files:**
- Modify: `notebooks/backtest_012.ipynb`
- Create: `docs/experiments.md`

**Interfaces:**
- Consumes: `log_experiment` (Task 7), the Experiments page (Tasks 11–14).
- Produces: at least one real run in `data/experiments/`, viewable in the browser.

- [ ] **Step 1: Create the dev symlink**

```bash
mkdir -p data/experiments
ln -sfn ../../data/experiments frontend/public/experiments
ls -l frontend/public/experiments
```

Expected: the symlink resolves to the repo's `data/experiments`. Both paths are already gitignored (Task 2).

- [ ] **Step 2: Add the logging cell to `backtest_012.ipynb`**

Append a new code cell at the end of the notebook:

```python
from backend.app.services.experiments import log_experiment

run = log_experiment(
    pf_oos,
    name="backtest_012_gframa_hbo",
    params=best_params,
    tags=["oos", "optuna", "balanced"],
    benchmark=pf_base.value(),
    notebook="notebooks/backtest_012.ipynb",
    notes="balanced selection from the NSGA-II study",
)
print(run.run_id, run.base_uri)
```

- [ ] **Step 3: Run the notebook cells and verify the store**

```bash
python -c "
import duckdb
con = duckdb.connect('data/experiments/experiments.duckdb', read_only=True)
print(con.execute('SELECT run_id, count(*) FROM trades GROUP BY 1').fetchall())
print(con.execute('SELECT count(*) FROM symbol_stats').fetchone())
"
```

Expected: one run id with a non-zero trade count, and a symbol-stats count matching the run's symbol count.

- [ ] **Step 4: Verify in the browser**

```bash
cd frontend && npm run dev
```

Open `http://localhost:5173/experiments`. Confirm: the run appears in the list; Overview renders the equity curve and per-symbol grid; Trades lists trades with outcome buckets; Attribution renders (the discrimination table will be empty until a run is logged with `features=`, which is expected); Symbol renders markers for a chosen symbol.

- [ ] **Step 5: Write the usage doc**

Create `docs/experiments.md` covering: the one-call logging API with the full signature; the `EXPERIMENTS_BACKEND` / `EXPERIMENTS_DIR` environment variables; how to query `experiments.duckdb` from a notebook; `rebuild_catalog()` and `rebuild_views()` as the recovery commands; the meaning of `equity_agg="mean"`; and the note that `exit_reason` is NULL unless supplied. Link it from `docs/README.md` alongside the existing entries.

- [ ] **Step 6: Run every check**

```bash
cd backend && pytest tests -q
cd ../frontend && npm run build && npm run lint
```

Expected: backend suite passes with no pre-existing failures introduced; frontend builds and lints clean.

- [ ] **Step 7: Commit**

```bash
git add notebooks/backtest_012.ipynb docs/experiments.md docs/README.md
git commit -m "feat(experiments): log backtest_012 and document the store"
```
