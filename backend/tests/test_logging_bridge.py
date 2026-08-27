"""Stdlib ``logging`` records have to reach loguru to be seen at all.

The app logs through loguru, but ``app.services.tradingagents.runner`` and the
whole vendored ``tradingagents`` package use ``logging.getLogger(__name__)``.
Uvicorn configures only its own loggers, so the root logger sits at WARNING with
no handlers and every one of those records is discarded — which is why none of
the runner's INFO lines ever appeared in the container logs.
"""

from __future__ import annotations

import logging

import pytest
from loguru import logger

from app.core.logging_bridge import InterceptHandler, install_logging_bridge


@pytest.fixture(autouse=True)
def restore_logging():
    """Put the bridged loggers back exactly as they were."""
    names = ("app", "tradingagents")
    saved = [
        (logging.getLogger(n), logging.getLogger(n).handlers[:],
         logging.getLogger(n).level, logging.getLogger(n).propagate)
        for n in names
    ]
    yield
    for lg, handlers, level, propagate in saved:
        lg.handlers = handlers
        lg.setLevel(level)
        lg.propagate = propagate


@pytest.fixture
def loguru_sink():
    """Captures what loguru was actually asked to emit."""
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="DEBUG", format="{level}|{message}")
    yield messages
    logger.remove(sink_id)


def test_vendor_info_records_reach_loguru(loguru_sink):
    install_logging_bridge()

    logging.getLogger("tradingagents.graph.setup").info("node %s ok in %d ms", "Trader", 12)

    assert any("node Trader ok in 12 ms" in m for m in loguru_sink), (
        "the vendor's %-style INFO record should arrive formatted"
    )


def test_runner_info_records_reach_loguru(loguru_sink):
    install_logging_bridge()

    logging.getLogger("app.services.tradingagents.runner").info("Registered VN vendor")

    assert any("Registered VN vendor" in m for m in loguru_sink)


def test_level_is_configurable(monkeypatch, loguru_sink):
    monkeypatch.setenv("TRADINGAGENTS_LOG_LEVEL", "WARNING")
    install_logging_bridge()

    log = logging.getLogger("tradingagents.graph.setup")
    log.info("chatty per-node line")
    log.warning("a node failed")

    joined = "\n".join(loguru_sink)
    assert "a node failed" in joined
    assert "chatty per-node line" not in joined, (
        "turning the vendor tree down is what keeps per-node logging affordable"
    )


def test_exception_info_is_preserved(loguru_sink):
    install_logging_bridge()

    try:
        raise ValueError("the node blew up")
    except ValueError:
        logging.getLogger("tradingagents.graph.setup").exception("node failed")

    joined = "\n".join(loguru_sink)
    assert "node failed" in joined
    assert "ValueError: the node blew up" in joined, "the traceback must survive"


def test_installing_twice_does_not_duplicate_records(loguru_sink):
    install_logging_bridge()
    install_logging_bridge()

    logging.getLogger("tradingagents.graph.setup").info("only once")

    assert sum("only once" in m for m in loguru_sink) == 1


def test_records_do_not_also_reach_the_root_logger():
    install_logging_bridge()
    root_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            root_records.append(record)

    handler = _Capture()
    logging.getLogger().addHandler(handler)
    try:
        logging.getLogger("tradingagents.graph.setup").info("no double print")
    finally:
        logging.getLogger().removeHandler(handler)

    assert not root_records, "propagation is off, so uvicorn cannot print it twice"


def test_bridge_is_installed_by_the_app(loguru_sink):
    # The bridge is worthless if nothing calls it: importing the app must be
    # enough for the vendor's records to start arriving.
    import app.main  # noqa: F401

    handlers = logging.getLogger("tradingagents").handlers
    assert any(isinstance(h, InterceptHandler) for h in handlers)


def test_records_are_attributed_to_their_real_source():
    # loguru reports the frame it is called from, which without unwinding is
    # always logging's own callHandlers. That would stamp every bridged line
    # with "logging:callHandlers:1706" and throw away the one field that says
    # where the message actually came from.
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        install_logging_bridge()
        logging.getLogger("tradingagents.graph.setup").info("who logged me")
    finally:
        logger.remove(sink_id)

    record = next(r for r in captured if "who logged me" in r["message"])
    assert record["name"] != "logging"
    assert record["function"] == "test_records_are_attributed_to_their_real_source"
