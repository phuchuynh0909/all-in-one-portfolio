from __future__ import annotations

from datetime import date, timedelta

# ── KRX format (2026+): 41I1[YEAR_CHAR][MONTH_CHAR]000 ──────────────────────
# Year chars: skip I, O, U  →  0-9 A-H J K L M N P Q R S T V W
YEAR_CHARS = {
    2010: "0",
    2011: "1",
    2012: "2",
    2013: "3",
    2014: "4",
    2015: "5",
    2016: "6",
    2017: "7",
    2018: "8",
    2019: "9",
    2020: "A",
    2021: "B",
    2022: "C",
    2023: "D",
    2024: "E",
    2025: "F",
    2026: "G",
    2027: "H",
    2028: "J",
    2029: "K",
    2030: "L",
    2031: "M",
    2032: "N",
    2033: "P",
    2034: "Q",
    2035: "R",
    2036: "S",
    2037: "T",
    2038: "V",
    2039: "W",
}
# Month chars: 1-9 digits, 10=A, 11=B, 12=C
MONTH_CHARS = {
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "A",
    11: "B",
    12: "C",
}

YEAR_CHARS_INV = {v: k for k, v in YEAR_CHARS.items()}
MONTH_CHARS_INV = {v: k for k, v in MONTH_CHARS.items()}

KRX_PREFIX = "41I1"
KRX_SUFFIX = "000"
KRX_SWITCH = (2025, 11)  # Nov 2025 was the first KRX-format contract


def _is_krx(year: int, month: int) -> bool:
    return (year, month) >= KRX_SWITCH


def encode(year: int, month: int) -> str:
    if _is_krx(year, month):
        return f"{KRX_PREFIX}{YEAR_CHARS[year]}{MONTH_CHARS[month]}{KRX_SUFFIX}"
    return f"VN30F{year % 100:02d}{month:02d}"


def decode(symbol: str) -> tuple[int, int]:
    if symbol.startswith(KRX_PREFIX):
        yc = symbol[4]
        mc = symbol[5]
        return YEAR_CHARS_INV[yc], MONTH_CHARS_INV[mc]
    if symbol.startswith("VN30F") and len(symbol) >= 9:
        yy = int(symbol[5:7])
        mm = int(symbol[7:9])
        year = 2000 + yy
        return year, mm
    raise ValueError(f"Unrecognised symbol format: {symbol!r}")


def third_thursday(year: int, month: int) -> date:
    first_day = date(year, month, 1)
    days_to_thu = (3 - first_day.weekday()) % 7
    first_thu = first_day + timedelta(days=days_to_thu)
    return first_thu + timedelta(weeks=2)


def front_month(ref: date | None = None) -> tuple[int, int]:
    if ref is None:
        ref = date.today()
    expiry = third_thursday(ref.year, ref.month)
    if ref <= expiry:
        return ref.year, ref.month
    if ref.month == 12:
        return ref.year + 1, 1
    return ref.year, ref.month + 1


def current_symbol(ref: date | None = None) -> str:
    year, month = front_month(ref)
    return encode(year, month)


def symbol_for_date(d: date) -> str:
    return current_symbol(d)
