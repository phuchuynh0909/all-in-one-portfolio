"""Watchlist symbol loader.

Single source of truth for the set of symbols the Layer 3 large-order
pipeline tracks. Reads `watchlist.json` (``{"symbols": [...]}``) from the
worker directory, overridable via the ``LARGE_ORDER_WATCHLIST_FILE`` /
``MQTT_WATCHLIST_FILE`` env vars or an explicit path argument.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Worker root (parent of this `core/` package).
_WORKER_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_WATCHLIST = "watchlist.json"


def _resolve_path(path: str | None) -> Path:
    if path is None:
        path = (
            os.getenv("LARGE_ORDER_WATCHLIST_FILE")
            or os.getenv("MQTT_WATCHLIST_FILE")
            or _DEFAULT_WATCHLIST
        )
    p = Path(path)
    if not p.is_absolute():
        p = _WORKER_DIR / p
    return p


def load_symbols(path: str | None = None) -> list[str]:
    """Return the watchlist symbols, de-duplicated and order-preserved.

    Returns an empty list (and prints a warning) if the file is missing or
    malformed — callers decide whether that is fatal.
    """
    watchlist_path = _resolve_path(path)
    try:
        with open(watchlist_path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: could not load watchlist from {watchlist_path}: {e}")
        return []

    raw = data.get("symbols", []) if isinstance(data, dict) else []
    seen: set[str] = set()
    symbols: list[str] = []
    for sym in raw:
        sym = str(sym).strip()
        if sym and sym not in seen:
            seen.add(sym)
            symbols.append(sym)
    return symbols
