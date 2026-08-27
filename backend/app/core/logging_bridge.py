"""Route stdlib ``logging`` into loguru, so half the app stops logging into a void.

The app logs through loguru, but two substantial pieces of it do not:
``app.services.tradingagents.runner`` and the whole vendored ``tradingagents``
package use ``logging.getLogger(__name__)``. Uvicorn configures only its own
``uvicorn*`` loggers and leaves the root logger at WARNING with no handlers, so
every INFO record from either was being dropped on the floor — the runner's
"Registered VN vendor", its LangSmith trace links and its client-disconnect
notice have never once appeared in the container logs.

Rather than convert those call sites (the vendor is a submodule on its own fork,
and stdlib ``logging`` is the right dependency-free choice for it), this points
the two logger trees at loguru: one handler, records forwarded with their level,
their %-formatted message and any traceback intact.
"""

from __future__ import annotations

import inspect
import logging
import os

from loguru import logger

# The two trees that log through stdlib: the app's own tradingagents service and
# the vendored package it drives. Everything else already uses loguru directly.
_BRIDGED_LOGGERS = ("app", "tradingagents")

_DEFAULT_LEVEL = "INFO"


class InterceptHandler(logging.Handler):
    """A stdlib handler that re-emits each record through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # loguru names its levels the same way, but an unknown one (a library
        # with custom levels) must not lose the record — fall back to the number.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk back out of the logging machinery so loguru attributes the line to
        # the module that actually logged it. Starting from this frame and always
        # stepping at least once (depth == 0) leaves the handler behind; the rest
        # of the walk skips logging's own callHandlers/handle/_log chain, which
        # would otherwise be credited with every bridged record.
        frame, depth = inspect.currentframe(), 0
        while frame is not None and (
            depth == 0 or frame.f_code.co_filename == logging.__file__
        ):
            frame = frame.f_back
            depth += 1

        # getMessage() applies the record's own %-style args. The result is passed
        # with no further arguments, so loguru leaves any braces in it alone.
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def install_logging_bridge(level: str | None = None) -> None:
    """Point the stdlib logger trees at loguru. Idempotent.

    The level applies to the bridged trees only and comes from
    ``TRADINGAGENTS_LOG_LEVEL`` when not passed explicitly. That knob is what
    makes per-node instrumentation affordable: the vendor logs a line as each
    graph node starts and finishes, which is worth having while a run is being
    diagnosed and is just noise the rest of the time. Set it to ``WARNING`` to
    keep only the failures.

    Propagation is switched off on the bridged loggers so a root handler — one
    uvicorn or a library installs — cannot print every record a second time.
    """
    resolved = (level or os.getenv("TRADINGAGENTS_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    handler = InterceptHandler()

    for name in _BRIDGED_LOGGERS:
        bridged = logging.getLogger(name)
        bridged.setLevel(resolved)
        # Re-installing must not double every record, so only attach once. The
        # level above is still refreshed, which makes a second call with a new
        # level do the useful half of its job.
        if not any(isinstance(h, InterceptHandler) for h in bridged.handlers):
            bridged.addHandler(handler)
        bridged.propagate = False
