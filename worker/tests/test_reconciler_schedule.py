"""Offline behavior checks for the after-session reconciliation job."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from infra.audit_queries import ReconcilerMetrics
from infra import reconciler_schedule
from workers import scheduled_backfill

ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def test_schedule_starts_at_1505_on_weekdays(monkeypatch, tmp_path):
    monkeypatch.setattr(
        reconciler_schedule, "STATE_FILE", tmp_path / "reconciler.json"
    )
    monday = datetime(2026, 9, 14, tzinfo=ICT)
    assert not reconciler_schedule.should_run_today(
        now=monday.replace(hour=15, minute=4)
    )
    assert reconciler_schedule.should_run_today(
        now=monday.replace(hour=15, minute=5)
    )
    assert not reconciler_schedule.should_run_today(
        now=monday.replace(day=19, hour=16, minute=0)
    )


def test_completed_session_does_not_run_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(
        reconciler_schedule, "STATE_FILE", tmp_path / "reconciler.json"
    )
    now = datetime(2026, 9, 14, 15, 5, tzinfo=ICT)
    reconciler_schedule.mark_run_done("2026-09-14")
    assert not reconciler_schedule.should_run_today(now=now)


def test_job_reconciles_every_discovered_symbol_and_date(monkeypatch, tmp_path):
    monkeypatch.setattr(
        reconciler_schedule, "STATE_FILE", tmp_path / "reconciler.json"
    )
    now = datetime(2026, 9, 14, 15, 5, tzinfo=ICT)
    monkeypatch.setattr(scheduled_backfill, "should_run_today", lambda **_kwargs: True)
    work = (
        (date(2026, 9, 14), "BCM"),
        (date(2026, 9, 14), "FPT"),
        (date(2026, 9, 11), "AAA"),
    )

    calls: list[tuple[str, bool, str | None]] = []
    repaired: list[date] = []
    marked: list[str] = []
    deleted: list[tuple[str, str]] = []

    def success(
        date_str: str,
        dry_run: bool,
        symbol: str | None,
        *,
        remove_legacy: bool,
    ):
        assert not remove_legacy
        calls.append((date_str, dry_run, symbol))
        return ReconcilerMetrics(run_date=date_str)

    result = scheduled_backfill.run_due_backfill(
        now=now,
        runner=success,
        repair=repaired.append,
        discover=lambda _cutoff: work,
        delete_legacy=lambda pairs: deleted.extend(pairs) or 0,
        mark_done=marked.append,
    )
    assert result is not None
    assert calls == [
        ("2026-09-14", False, "BCM"),
        ("2026-09-14", False, "FPT"),
        ("2026-09-11", False, "AAA"),
    ]
    assert deleted == [
        ("2026-09-14", "BCM"),
        ("2026-09-14", "FPT"),
        ("2026-09-11", "AAA"),
    ]
    assert repaired == [date(2026, 9, 14), date(2026, 9, 11)]
    assert marked == ["2026-09-14"]
    assert reconciler_schedule.get_pending_repair_dates() == ()


def test_failed_pair_is_repaired_but_daily_run_remains_due(monkeypatch, tmp_path):
    monkeypatch.setattr(
        reconciler_schedule, "STATE_FILE", tmp_path / "reconciler.json"
    )
    now = datetime(2026, 9, 14, 15, 5, tzinfo=ICT)
    monkeypatch.setattr(scheduled_backfill, "should_run_today", lambda **_kwargs: True)
    repaired: list[date] = []
    marked: list[str] = []

    result = scheduled_backfill.run_due_backfill(
        now=now,
        runner=lambda *_args, **_kwargs: ReconcilerMetrics(
            run_date="2026-09-11", failed_rows=1
        ),
        repair=repaired.append,
        discover=lambda _cutoff: ((date(2026, 9, 11), "AAA"),),
        delete_legacy=lambda _pairs: 0,
        mark_done=marked.append,
    )
    assert result is not None and result[0].failed_rows == 1
    assert repaired == [date(2026, 9, 11)]
    assert marked == []


def test_pending_repair_survives_daily_completion_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        reconciler_schedule, "STATE_FILE", tmp_path / "reconciler.json"
    )
    reconciler_schedule.mark_repair_pending("2026-09-11")
    reconciler_schedule.mark_run_done("2026-09-14")
    assert reconciler_schedule.get_pending_repair_dates() == ("2026-09-11",)

    reconciler_schedule.mark_repair_done("2026-09-11")
    assert reconciler_schedule.get_pending_repair_dates() == ()
