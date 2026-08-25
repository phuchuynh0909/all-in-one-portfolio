"""Tests for the worker's root-logger setup.

The bug these guard against is silent: with no handler on the root logger,
``logging.lastResort`` passes WARNING and above and drops everything below, so a
library module's ``log.info`` disappears while its ``log.warning`` gets through.
That is what hid ``infra.clickhouse_sink``'s insert counters in the container.
"""

from __future__ import annotations

import logging
import sys

import pytest

from infra.logging_setup import setup_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Save and restore root logging state — pytest shares it across tests."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    noisy = {name: logging.getLogger(name).level for name in ("websockets", "urllib3")}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, lvl in noisy.items():
        logging.getLogger(name).setLevel(lvl)


def _bare_root():
    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(logging.WARNING)
    return root


def test_info_is_dropped_without_setup():
    """Reproduces the reported symptom before asserting the fix."""
    _bare_root()
    log = logging.getLogger("infra.clickhouse_sink")
    assert log.getEffectiveLevel() == logging.WARNING
    assert not log.isEnabledFor(logging.INFO)  # the sink's counters never emit
    assert log.isEnabledFor(logging.WARNING)  # ...but its warnings do


def test_setup_lets_info_through(monkeypatch, capsys):
    _bare_root()
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    setup_logging()

    logging.getLogger("infra.clickhouse_sink").info("ticks: 1 inserts, 842 rows")
    out = capsys.readouterr().out
    assert "ticks: 1 inserts, 842 rows" in out
    assert "infra.clickhouse_sink" in out  # the logger name identifies the source


def test_logs_go_to_stdout_not_stderr(monkeypatch, capsys):
    _bare_root()
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    setup_logging()
    logging.getLogger("infra.x").info("on stdout")
    captured = capsys.readouterr()
    assert "on stdout" in captured.out
    assert "on stdout" not in captured.err


def test_repeated_calls_do_not_duplicate_handlers(monkeypatch):
    _bare_root()
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    setup_logging()
    setup_logging()
    setup_logging()
    assert len(logging.getLogger().handlers) == 1


def test_existing_handler_is_raised_to_our_level(capsys):
    """A handler pinned at WARNING would swallow INFO just as no handler did."""
    root = _bare_root()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)

    setup_logging("INFO")
    assert len(root.handlers) == 1  # kept, not replaced
    assert root.handlers[0] is handler
    logging.getLogger("infra.x").info("passes the pre-existing handler")
    assert "passes the pre-existing handler" in capsys.readouterr().out


def test_log_level_env_is_honoured(monkeypatch):
    _bare_root()
    monkeypatch.setenv("LOG_LEVEL", "debug")  # case-insensitive
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_debug_pins_the_noisy_libraries():
    """websockets at DEBUG traces every frame — every trade on the exchange."""
    _bare_root()
    setup_logging("DEBUG")
    assert logging.getLogger("websockets").level == logging.INFO
    assert logging.getLogger("urllib3").level == logging.INFO


def test_unknown_level_falls_back_to_info(monkeypatch, capsys):
    _bare_root()
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    setup_logging()
    assert logging.getLogger().level == logging.INFO
    assert "unknown LOG_LEVEL" in capsys.readouterr().err
