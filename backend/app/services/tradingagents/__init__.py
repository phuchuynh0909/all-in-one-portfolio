"""Integration of the vendored TradingAgents multi-agent framework.

The framework itself lives, unmodified, in ``vendor/TradingAgents`` at the repo
root. To keep the fork pristine (so it can be re-pulled from upstream) all
platform-specific wiring lives here:

  - ``vn_data``  — a "portfolio" data vendor backed by this platform's own
                   Vietnamese-market data (ClickHouse OHLCV + wichart reports).
  - ``runner``   — configures the graph for a local Ollama server, registers the
                   VN vendor into TradingAgents' dispatch, and streams a run.

Importing this package prepends the vendored source tree to ``sys.path`` so
``import tradingagents`` resolves without a separate pip install. When the
package is pip-installed instead (e.g. in a slim production image that does not
copy ``vendor/``), the existing install is used and the shim is a no-op.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Locate the vendored TradingAgents source tree. Candidate roots cover both
# layouts we run in:
#   * local/dev  — repo root is parents[4] of this file (…/vendor/TradingAgents)
#   * Docker     — the backend is bind-mounted at /app, so the repo-root vendor
#                  dir is mounted alongside it at /app/vendor (parents[3]) via a
#                  docker-compose volume; /vendor covers a container-root mount.
# TRADINGAGENTS_VENDOR_PATH overrides everything for non-standard layouts.
_HERE = Path(__file__).resolve()
_CANDIDATES: list[Path] = []

_env_override = os.getenv("TRADINGAGENTS_VENDOR_PATH")
if _env_override:
    _CANDIDATES.append(Path(_env_override))

_CANDIDATES += [
    _HERE.parents[4] / "vendor" / "TradingAgents",  # repo root (local dev)
    _HERE.parents[3] / "vendor" / "TradingAgents",  # /app/vendor (Docker mount)
    Path("/vendor/TradingAgents"),                  # container-root mount
]

for _candidate in _CANDIDATES:
    # A valid source tree contains the importable ``tradingagents`` package.
    if (_candidate / "tradingagents" / "__init__.py").is_file():
        vendor_path = str(_candidate)
        if vendor_path not in sys.path:
            # Prepend so the vendored copy wins over any stale global install.
            sys.path.insert(0, vendor_path)
        break


def _install_yfinance_stub(force: bool = False) -> None:
    """Satisfy the vendored framework's ``import yfinance`` without the package.

    This deployment analyzes the Vietnamese market and never calls Yahoo Finance
    (all data routes to the VN ``portfolio`` vendor; identity/reflection Yahoo
    lookups are bypassed in ``runner``). But several vendored modules import
    ``yfinance`` — and ``from yfinance.exceptions import YFRateLimitError`` — at
    module load, so the import must resolve.

    When the real ``yfinance`` is installed we leave it alone; otherwise we
    register a minimal stub. The stub exposes only the names touched at import
    time; any actual Yahoo *call* (``Ticker``/``download``) raises a clear error
    rather than silently returning bad data — which should never happen given
    the VN wiring, so it doubles as a guard.
    """
    import importlib.util
    import types

    if not force and importlib.util.find_spec("yfinance") is not None:
        return  # real yfinance available — prefer it
    if not force and "yfinance" in sys.modules:
        return

    def _unavailable(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(
            "yfinance is not installed in this deployment (Vietnamese-market "
            "only). A code path attempted a Yahoo Finance call — ensure the VN "
            "'portfolio' data vendor is registered and identity/reflection "
            "lookups are bypassed (see app/services/tradingagents/runner.py)."
        )

    exceptions = types.ModuleType("yfinance.exceptions")

    class YFRateLimitError(Exception):
        """Stub mirror of yfinance.exceptions.YFRateLimitError."""

    exceptions.YFRateLimitError = YFRateLimitError

    stub = types.ModuleType("yfinance")
    stub.__stub__ = True
    stub.Ticker = _unavailable
    stub.download = _unavailable
    stub.exceptions = exceptions

    sys.modules["yfinance"] = stub
    sys.modules["yfinance.exceptions"] = exceptions


_install_yfinance_stub()
