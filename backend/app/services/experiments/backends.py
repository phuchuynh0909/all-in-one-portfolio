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
