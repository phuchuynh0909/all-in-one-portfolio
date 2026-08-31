"""Sector level 5: the sieucophieu.vn ``stock_lists`` taxonomy.

A flatter, more tradeable cut of the market than the ICB levels 1-4 — "Chứng
khoán", "Thép", "Đầu tư công" rather than ICB's formal hierarchy. Two pieces:

* the id → name table below, which is the ``stock_list`` id from the upstream
  API (VN30 is id 23 and is deliberately absent: it is an index basket, not a
  sector, and ``scripts/build_sector_map.py`` drops it for the same reason);
* the constituents, read from ``app/sector_map.json``, which
  ``build_sector_map.py`` writes as ``{symbol: [sector name, ...]}``.

The mapping is **multi-tag** — 40 of its 368 symbols belong to more than one
list — which is why level 5 constituents live in that file rather than in a
``stock_symbol.id_sector_level_5`` column: a single foreign key cannot hold
them, and the levels 1-4 columns silently would not either.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

SECTOR_LEVEL_5 = 5

# ``stock_list`` id → name, from the upstream daily payload. VN30 (23) excluded.
LEVEL5_SECTORS: dict[int, str] = {
    1: "Bán lẻ",
    2: "Bảo hiểm",
    3: "BĐS KCN",
    4: "Bất động sản",
    5: "Chứng khoán",
    6: "Công nghệ - Truyền thông",
    7: "Dầu khí",
    8: "Điện - Năng lượng",
    9: "Hàng không-Du lịch",
    10: "Dược - Y tế",
    11: "Dệt may",
    12: "Cảng biển-Vận tải",
    13: "Hóa chất-Phân bón",
    14: "Ngân hàng",
    15: "Ôtô-Phụ tùng",
    16: "Nước - Nhựa",
    17: "Thực phẩm",
    18: "Đường - Gỗ - Giấy",
    19: "VLXD",
    20: "Thép",
    21: "Đầu tư công",
    22: "Thủy sản",
    24: "Cao su",
    25: "Khoáng sản",
}

SECTOR_MAP_PATH = os.getenv(
    "SECTOR_MAP_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sector_map.json"),
)


@lru_cache(maxsize=1)
def _sector_map() -> dict[str, list[str]]:
    """``{symbol: [sector name, ...]}`` as written by ``build_sector_map.py``."""
    with open(SECTOR_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def level5_constituents() -> dict[int, list[str]]:
    """``{sector id: [symbol, ...]}`` for level 5, inverted from the sector map.

    A name in the file with no id here (VN30) is dropped; an id with no members
    comes back as an empty list rather than missing, so callers can report the
    zero instead of tripping over a KeyError.
    """
    by_name = {name: sector_id for sector_id, name in LEVEL5_SECTORS.items()}
    members: dict[int, list[str]] = {sector_id: [] for sector_id in LEVEL5_SECTORS}

    for symbol, names in _sector_map().items():
        for name in names:
            sector_id = by_name.get(name)
            if sector_id is not None:
                members[sector_id].append(str(symbol))

    return {sector_id: sorted(symbols) for sector_id, symbols in members.items()}


def level5_symbols() -> list[str]:
    """Every symbol tagged into any level-5 sector, deduplicated."""
    return sorted({s for group in level5_constituents().values() for s in group})
