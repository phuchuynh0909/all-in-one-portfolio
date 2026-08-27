"""Tests for the dropped-row tally used by the ingest filters.

A filter that drops silently is indistinguishable from a feed that never sent
the row; one that logs every drop is worse, because the dropped classes are
ordinary traffic at several thousand a second. These pin the middle: name each
distinct thing dropped once, then report totals on a slow cadence.
"""

from __future__ import annotations

import logging

from infra.drop_tally import DropTally

LOGGER = "test.drop_tally"


def _tally(**kw):
    return DropTally(logging.getLogger(LOGGER), "tick_ingest", **kw)


def test_the_first_of_each_kind_says_what_was_dropped(caplog):
    tally = _tally()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        tally.note("symbol", "ABC")
        tally.note("board", "T3")

    assert "symbol='ABC'" in caplog.text
    assert "board='T3'" in caplog.text
    assert caplog.text.count("first of this kind") == 2


def test_repeats_of_a_known_kind_are_silent(caplog):
    tally = _tally(summary_every=10_000)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(500):
            tally.note("symbol", "ABC")

    # One line for the first sighting, and nothing for the other 499.
    assert len(caplog.records) == 1
    assert tally.totals == {"symbol": 500}


def test_a_new_detail_of_a_known_reason_is_still_named(caplog):
    """"Which symbol" is the question; the reason alone does not answer it."""
    tally = _tally()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        tally.note("symbol", "ABC")
        tally.note("symbol", "XYZ")

    assert "symbol='ABC'" in caplog.text and "symbol='XYZ'" in caplog.text


def test_totals_are_reported_on_a_slow_cadence(caplog):
    tally = _tally(summary_every=100)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for i in range(250):
            tally.note("board" if i % 5 else "symbol", "T3" if i % 5 else "ABC")

    summaries = [r.message for r in caplog.records if "rows so far" in r.message]
    assert len(summaries) == 2  # at 100 and at 200; the last 50 wait for 300
    assert "board=160" in summaries[-1] and "symbol=40" in summaries[-1]
    # Counted regardless of whether a summary has been printed for them yet.
    assert tally.totals == {"board": 200, "symbol": 50}


def test_an_unbounded_feed_cannot_grow_the_detail_set_without_limit(caplog):
    """A garbage feed must not turn this into a memory leak or a log flood."""
    tally = _tally(max_details=3, summary_every=10_000)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for i in range(50):
            tally.note("symbol", f"SYM{i}")

    assert len(caplog.records) == 4  # 3 named, then one line saying it stopped
    assert "no longer naming" in caplog.text
    assert tally.totals == {"symbol": 50}  # counting continues regardless


def test_a_quiet_pipeline_logs_nothing(caplog):
    tally = _tally()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        pass
    assert caplog.records == []
    assert tally.totals == {}


def test_the_pipeline_name_prefixes_every_line(caplog):
    """So the drops are greppable, and attributable when two flows share a log."""
    tally = _tally(summary_every=1)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        tally.note("symbol", "ABC")
    assert all(r.message.startswith("tick_ingest: ") for r in caplog.records)


def test_a_bytes_detail_is_truncated_rather_than_logged_whole(caplog):
    """An unparseable payload is the one detail with no bound on its size."""
    tally = _tally()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        tally.note("unparseable", b"x" * 5000)
    assert len(caplog.text) < 500
    assert "…" in caplog.text
