"""Writes experiment runs and keeps the derived catalog in sync."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
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

    VIEWS_DB_NAME = "experiments.duckdb"

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
        self.rebuild_views()

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
            # DuckDB rejects prepared parameters in CREATE VIEW, so the globs are
            # inlined as literals with single quotes escaped.
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
