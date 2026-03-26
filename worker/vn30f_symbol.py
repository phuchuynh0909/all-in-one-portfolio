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
KRX_SWITCH = (2026, 1)  # Full KRX from Jan 2026

SPECIAL_CASES: dict[tuple[int, int], str] = {
    (2025, 7): "41I1F7000",
    (2025, 8): "41I1F8000",
    (2025, 11): "41I1FB000",
}
SPECIAL_CASES_INV: dict[str, tuple[int, int]] = {v: k for k, v in SPECIAL_CASES.items()}


def encode(year: int, month: int) -> str:
    if (year, month) in SPECIAL_CASES:
        return SPECIAL_CASES[(year, month)]
    if (year, month) >= KRX_SWITCH:
        return f"{KRX_PREFIX}{YEAR_CHARS[year]}{MONTH_CHARS[month]}{KRX_SUFFIX}"
    return f"VN30F{year % 100:02d}{month:02d}"


def decode(symbol: str) -> tuple[int, int]:
    if symbol in SPECIAL_CASES_INV:
        return SPECIAL_CASES_INV[symbol]
    if symbol.startswith(KRX_PREFIX):
        return YEAR_CHARS_INV[symbol[4]], MONTH_CHARS_INV[symbol[5]]
    if symbol.startswith("VN30F") and len(symbol) >= 9:
        return 2000 + int(symbol[5:7]), int(symbol[7:9])
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
