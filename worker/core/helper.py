from datetime import datetime, timedelta
from datetime import time as dtime
import math

def _num_bins(session_start: dtime, session_end: dtime, bin_minutes: int) -> int:
    base = datetime.now().date()
    start_dt = datetime.combine(base, session_start)
    end_dt = datetime.combine(base, session_end)
    total_minutes = int((end_dt - start_dt).total_seconds() // 60)
    # Use ceiling to include the final partial bin within the session window
    return max(1, math.ceil(total_minutes / bin_minutes))

def _bin_index(ts: datetime, session_start: dtime, session_end: dtime, bin_minutes: int) -> int:
    base_date = ts.date()
    start_dt = datetime.combine(base_date, session_start)
    end_dt = datetime.combine(base_date, session_end)

    if ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None:
        start_dt = start_dt.replace(tzinfo=ts.tzinfo)
        end_dt = end_dt.replace(tzinfo=ts.tzinfo)

    if ts < start_dt or ts >= end_dt:
        return -1
    minutes_since_start = int((ts - start_dt).total_seconds() // 60)
    return minutes_since_start // bin_minutes

def _normalize(vec):
    s = sum(vec)
    if s <= 0:
        n = len(vec) if len(vec) > 0 else 1
        return [1.0 / n] * n
    return [v / s for v in vec]