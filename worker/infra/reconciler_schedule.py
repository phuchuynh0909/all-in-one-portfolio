"""Once-per-session scheduling state for tick reconciliation.

The scheduler runs on weekdays after the configured exchange-local trigger and
persists the completed session date so container restarts do not duplicate a
run.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# State file path: worker/state_dir/reconciler_run_state.json
STATE_FILE = Path(__file__).parent.parent / "state_dir" / "reconciler_run_state.json"


def _ensure_state_dir() -> None:
    """Create state_dir if it doesn't exist."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_state() -> dict:
    """Read the current state from the state file.

    Returns:
        dict with keys 'last_run_date' and 'last_run_at', or empty dict if file doesn't exist.
    """
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read state file: {e}")
        return {}

def _write_state(state: dict) -> None:
    """Atomically persist scheduler state."""
    _ensure_state_dir()
    temporary = STATE_FILE.with_suffix(".tmp")
    try:
        with open(temporary, "w") as file:
            json.dump(state, file, indent=2)
        temporary.replace(STATE_FILE)
    except IOError as exc:
        logger.error("Failed to write state file: %s", exc)
        raise


def should_run_today(
    tz_name: str = "Asia/Ho_Chi_Minh",
    trigger_hour: int = 15,
    trigger_minute: int = 5,
    force: bool = False,
    now: datetime | None = None,
) -> bool:
    """Return whether today's weekday session is due and not yet completed."""
    if not 0 <= trigger_hour <= 23:
        raise ValueError("trigger_hour must be between 0 and 23")
    if not 0 <= trigger_minute <= 59:
        raise ValueError("trigger_minute must be between 0 and 59")

    tz = ZoneInfo(tz_name)
    local_now = datetime.now(tz) if now is None else now.astimezone(tz)

    if force:
        logger.info("force=True: bypassing all guards")
        return True

    if local_now.weekday() >= 5:
        logger.debug("Current day is a weekend; no exchange session to reconcile")
        return False

    if (local_now.hour, local_now.minute) < (trigger_hour, trigger_minute):
        logger.debug(
            "Current time %02d:%02d is before trigger %02d:%02d",
            local_now.hour,
            local_now.minute,
            trigger_hour,
            trigger_minute,
        )
        return False

    today_str = local_now.date().isoformat()
    state = _read_state()
    if state.get("last_run_date") == today_str:
        logger.info("Already ran today (%s), skipping", today_str)
        return False

    logger.info("Ready to run reconciler for %s", today_str)
    return True


def mark_run_done(date_str: str) -> None:
    """Mark the daily scheduler run complete without losing repair state."""
    state = _read_state()
    state.update(
        {
            "last_run_date": date_str,
            "last_run_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }
    )
    _write_state(state)
    logger.info("Marked reconciler run done for %s", date_str)


def get_pending_repair_dates() -> tuple[str, ...]:
    """Return session dates whose derived tables still need rebuilding."""
    pending = _read_state().get("pending_repair_dates", [])
    return tuple(sorted(str(value) for value in pending))


def mark_repair_pending(date_str: str) -> None:
    """Durably record a repair before mutating that session's ticks."""
    state = _read_state()
    pending = {str(value) for value in state.get("pending_repair_dates", [])}
    pending.add(date_str)
    state["pending_repair_dates"] = sorted(pending)
    _write_state(state)


def mark_repair_done(date_str: str) -> None:
    """Clear a derived-table repair after every rebuild succeeds."""
    state = _read_state()
    pending = {str(value) for value in state.get("pending_repair_dates", [])}
    pending.discard(date_str)
    state["pending_repair_dates"] = sorted(pending)
    _write_state(state)


def get_last_run_date() -> Optional[str]:
    """Get the last date the reconciler ran.

    Returns:
        str: Date in YYYY-MM-DD format, or None if never run.
    """
    state = _read_state()
    return state.get("last_run_date")
