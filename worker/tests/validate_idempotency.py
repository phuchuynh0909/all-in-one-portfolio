from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _session_window_utc(date_str: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(date_str)
    session_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    session_start_local = datetime.combine(day, time(9, 0), tzinfo=session_tz)
    session_end_local = datetime.combine(day, time(15, 0), tzinfo=session_tz)
    return (
        session_start_local.astimezone(timezone.utc),
        session_end_local.astimezone(timezone.utc),
    )


def _to_utc_rounded_microseconds(value: datetime | str) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return datetime.fromtimestamp(round(dt.timestamp(), 6), tz=timezone.utc)


def _tick_key(row: dict) -> tuple[str, str, float, int, int]:
    return (
        str(row["symbol"]),
        _to_utc_rounded_microseconds(row["sending_time"]).isoformat(
            timespec="microseconds"
        ),
        float(row["match_price"]),
        int(row["match_qty"]),
        int(row["side"]),
    )


def _diff_ticks(
    api_rows: list[dict], ch_rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    ch_keys = {_tick_key(row) for row in ch_rows}
    missing = [row for row in api_rows if _tick_key(row) not in ch_keys]
    return missing, []


def _canonical_tick(
    *,
    symbol: str,
    sending_time: datetime,
    match_price: float,
    match_qty: int,
    side: int,
) -> dict:
    return {
        "symbol": symbol,
        "sending_time": sending_time,
        "match_price": float(match_price),
        "match_qty": int(match_qty),
        "side": int(side),
        "received_at": datetime.now(timezone.utc),
    }


def test_idempotency(date_str: str) -> bool:
    try:
        session_start_utc, _ = _session_window_utc(date_str)

        tick_a = _canonical_tick(
            symbol="FPT",
            sending_time=session_start_utc + timedelta(seconds=1),
            match_price=123.4,
            match_qty=100,
            side=1,
        )
        tick_b = _canonical_tick(
            symbol="FPT",
            sending_time=session_start_utc + timedelta(seconds=2),
            match_price=123.5,
            match_qty=200,
            side=2,
        )

        api_rows = [tick_a, tick_b]
        ch_rows = [dict(tick_a), dict(tick_b)]

        missing, _ = _diff_ticks(api_rows, ch_rows)
        assert len(missing) == 0

        extra_tick = _canonical_tick(
            symbol="FPT",
            sending_time=session_start_utc + timedelta(seconds=3),
            match_price=123.6,
            match_qty=300,
            side=1,
        )

        api_rows_with_extra = [*api_rows, extra_tick]
        missing_first_run, _ = _diff_ticks(api_rows_with_extra, ch_rows)
        assert len(missing_first_run) == 1

        ch_rows_updated = [*ch_rows, missing_first_run[0]]
        missing_second_run, _ = _diff_ticks(api_rows_with_extra, ch_rows_updated)
        assert len(missing_second_run) == 0

        print("test_idempotency: PASS")
        return True
    except Exception as exc:
        print(f"test_idempotency: FAIL ({exc})")
        return False


def test_boundary_ticks(date_str: str) -> bool:
    try:
        session_start_utc, session_end_utc = _session_window_utc(date_str)

        ticks = {
            "start_inclusive": _canonical_tick(
                symbol="FPT",
                sending_time=session_start_utc,
                match_price=100.0,
                match_qty=1,
                side=1,
            ),
            "end_inclusive": _canonical_tick(
                symbol="FPT",
                sending_time=session_end_utc,
                match_price=101.0,
                match_qty=1,
                side=2,
            ),
            "before_start_excluded": _canonical_tick(
                symbol="FPT",
                sending_time=session_start_utc - timedelta(microseconds=1),
                match_price=99.0,
                match_qty=1,
                side=1,
            ),
            "after_end_excluded": _canonical_tick(
                symbol="FPT",
                sending_time=session_end_utc + timedelta(microseconds=1),
                match_price=102.0,
                match_qty=1,
                side=2,
            ),
        }

        included = {
            name
            for name, row in ticks.items()
            if session_start_utc <= row["sending_time"] <= session_end_utc
        }

        assert "start_inclusive" in included
        assert "end_inclusive" in included
        assert "before_start_excluded" not in included
        assert "after_end_excluded" not in included

        print("test_boundary_ticks: PASS")
        return True
    except Exception as exc:
        print(f"test_boundary_ticks: FAIL ({exc})")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate reconciler idempotency and boundary-time behavior"
    )
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    results = [
        test_idempotency(args.date),
        test_boundary_ticks(args.date),
    ]

    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
