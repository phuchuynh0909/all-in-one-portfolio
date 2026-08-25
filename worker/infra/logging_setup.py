"""Root-logger setup for the worker entrypoints.

Every module here logs through ``logging.getLogger(__name__)``, which produces
**nothing** until something configures the root logger: with no handler attached,
Python falls back to ``logging.lastResort``, and that emits ``WARNING`` and above
only. So a library module's ``log.info(...)`` is silently dropped while its
``log.warning(...)`` gets through — which is why the ClickHouse sink's insert
counters never appeared in the container even though the DNSE reconnect warnings
did.

The one-shot scripts (``workers/reconciler.py``,
``workers/hawkes_signal_worker.py``, …) each call ``logging.basicConfig`` at
import. The Bytewax flows did not, because ``python -m bytewax.run`` owns
``__main__`` and there is no ``if __name__ == "__main__"`` block to hang it off —
so they call ``setup_logging()`` at module scope instead, which the import
performed by ``bytewax.run`` runs for us.

``LOG_LEVEL`` (default ``INFO``) sets the threshold.
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Libraries that log per-frame or per-request at DEBUG. Pinned to INFO so that
# LOG_LEVEL=DEBUG stays usable on a live tape — websockets in particular traces
# every frame, which on this feed is every trade on the exchange.
_NOISY = ("websockets", "urllib3", "clickhouse_connect", "asyncio")


def setup_logging(level: str | None = None) -> None:
    """Attach a stdout handler to the root logger, honouring ``LOG_LEVEL``.

    Safe to call more than once, and safe to call after something else has
    configured logging: an existing handler is kept (its format is not ours to
    override) but is raised to at least our level, since a handler pinned at
    WARNING would swallow INFO just as the missing-handler case did.

    Logs go to **stdout**, not stderr: ``docker logs`` shows both, but keeping
    them on stdout puts them in the same stream as the flows' own ``print``
    status lines so their relative order survives.
    """
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    numeric = logging.getLevelName(resolved)
    if not isinstance(numeric, int):  # e.g. LOG_LEVEL=verbose
        print(f"[logging] unknown LOG_LEVEL {resolved!r}; using INFO", file=sys.stderr)
        numeric = logging.INFO

    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            if handler.level > numeric:
                handler.setLevel(numeric)
    else:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        root.addHandler(handler)
    root.setLevel(numeric)

    if numeric < logging.INFO:
        for name in _NOISY:
            logging.getLogger(name).setLevel(logging.INFO)
