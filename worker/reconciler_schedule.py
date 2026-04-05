"""Schedule management for the 15:00 reconciler job.

Manages once-per-day run state to prevent duplicate runs and support explicit rerun mode.
State is stored in state_dir/reconciler_run_state.json relative to this file's parent directory.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# State file path: worker/state_dir/reconciler_run_state.json
STATE_FILE = Path(__file__).parent / "state_dir" / "reconciler_run_state.json"


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


def should_run_today(
    tz_name: str = "Asia/Ho_Chi_Minh",
    trigger_hour: int = 15,
    force: bool = False,
) -> bool:
    """Determine if the reconciler should run today.

    Logic:
    1. Get current local datetime in tz_name timezone
    2. Return False if current time < trigger_hour:00 local
    3. Read state file; if today's date already marked done AND force=False → return False
    4. Return True otherwise

    Args:
        tz_name: Timezone name (e.g., "Asia/Ho_Chi_Minh"). Defaults to "Asia/Ho_Chi_Minh".
        trigger_hour: Hour (0-23) when reconciler should run. Defaults to 15 (3 PM).
        force: If True, bypass the already-ran-today guard. Defaults to False.

    Returns:
        bool: True if reconciler should run, False otherwise.
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    if force:
        logger.info("force=True: bypassing all guards")
        return True

    if now.hour < trigger_hour:
        logger.debug(
            "Current time %d:%02d is before trigger hour %d",
            now.hour,
            now.minute,
            trigger_hour,
        )
        return False

    # Check if we already ran today
    today_str = now.date().isoformat()
    state = _read_state()
    last_run_date = state.get("last_run_date")

    if last_run_date == today_str:
        logger.info(f"Already ran today ({today_str}), skipping")
        return False

    logger.info(f"Ready to run reconciler for {today_str}")
    return True


def mark_run_done(date_str: str) -> None:
    """Mark that the reconciler has run for the given date.

    Writes to state file with format:
    {"last_run_date": "YYYY-MM-DD", "last_run_at": "ISO8601_UTC"}

    Args:
        date_str: Date string in YYYY-MM-DD format (typically today's date).
    """
    _ensure_state_dir()

    # Get current UTC time in ISO8601 format
    now_utc = datetime.now(ZoneInfo("UTC"))

    state = {
        "last_run_date": date_str,
        "last_run_at": now_utc.isoformat(),
    }

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        logger.info(f"Marked reconciler run done for {date_str}")
    except IOError as e:
        logger.error(f"Failed to write state file: {e}")
        raise


def get_last_run_date() -> Optional[str]:
    """Get the last date the reconciler ran.

    Returns:
        str: Date in YYYY-MM-DD format, or None if never run.
    """
    state = _read_state()
    return state.get("last_run_date")
