"""Tests for the sector-map preference in sector_analyst.

Covers the offline, pure logic added on top of the stock-API / DB mapping:
``sector_tags`` reading ``backend/app/sector_map.json`` and the label
preference order (map > stock-API industry > DB name > symbol). No live calls.
"""
from __future__ import annotations

from unittest import mock

from app.services.tradingagents import sector_analyst


# Shape-accurate slices of the three industry endpoints (one sector: Cao su).
_STRENGTH = {
    "data": [
        {"name": "Cao su", "rs_short": 2.2, "rs_mid": 3.4, "rs_relative": 76.1},
    ]
}
_CASHFLOW = [
    {
        "stock_list_name": "Cao su",
        "roc": 1.98,
        "cashflow": 123.0,
        "cashflow_change_percent": 10.0,
    },
]
_SPREAD = {
    "data": [
        {"name": "Cao su", "up_percent": 75.0, "down_percent": 25.0, "roc": 1.98},
    ]
}


def _fetch_stub(strength=_STRENGTH):
    def _fetch(endpoint, *, use_token):
        return {
            "industry_strength": strength,
            "industry_cashflow": _CASHFLOW,
            "industry_spread": _SPREAD,
        }.get(endpoint)

    return _fetch


def test_sector_tags_known_symbol_returns_tags():
    # AAA is a single-sector symbol in the committed map.
    assert sector_analyst.sector_tags("aaa") == ["Nước - Nhựa"]


def test_sector_tags_multi_tag_symbol():
    # KBC is a real multi-tag symbol in the committed map.
    tags = sector_analyst.sector_tags("KBC")
    assert "Bất động sản" in tags and "BĐS KCN" in tags


def test_sector_tags_unknown_symbol_returns_empty():
    assert sector_analyst.sector_tags("NOTASYMBOL") == []


def test_mapped_label_joins_multi_tags():
    assert (
        sector_analyst._mapped_label("KBC")
        == "Bất động sản / BĐS KCN"
    )


def test_mapped_label_unknown_symbol_is_none():
    assert sector_analyst._mapped_label("NOTASYMBOL") is None


def test_preferred_label_prefers_map_over_profile_industry():
    # Map wins even when the stock-API profile carries an industry.
    label = sector_analyst._preferred_label(
        "AAA", profile_industry="Plastics (Nhựa)", db_sector_name="Nhựa DB"
    )
    assert label == "Nước - Nhựa"


def test_preferred_label_falls_back_to_profile_then_db_then_symbol():
    assert (
        sector_analyst._preferred_label(
            "NOTASYMBOL", profile_industry="Plastics", db_sector_name="Nhựa DB"
        )
        == "Plastics"
    )
    assert (
        sector_analyst._preferred_label(
            "NOTASYMBOL", profile_industry=None, db_sector_name="Nhựa DB"
        )
        == "Nhựa DB"
    )
    assert (
        sector_analyst._preferred_label(
            "NOTASYMBOL", profile_industry=None, db_sector_name=None
        )
        == "NOTASYMBOL"
    )


def test_build_industry_metrics_merges_all_endpoints():
    with mock.patch.object(
        sector_analyst, "_fetch_industry_json", side_effect=_fetch_stub()
    ):
        metrics = sector_analyst._build_industry_metrics()
    cs = metrics["Cao su"]
    assert cs["rs_relative"] == 76.1
    assert cs["rs_short"] == 2.2
    assert cs["roc"] == 1.98
    assert cs["cashflow"] == 123.0
    assert cs["cashflow_change_percent"] == 10.0
    assert cs["up_percent"] == 75.0
    assert cs["down_percent"] == 25.0


def test_build_industry_metrics_skips_strength_when_no_token():
    # industry_strength returns None (missing/expired token) -> still merge the
    # public cashflow + spread fields.
    with mock.patch.object(
        sector_analyst, "_fetch_industry_json", side_effect=_fetch_stub(strength=None)
    ):
        metrics = sector_analyst._build_industry_metrics()
    cs = metrics["Cao su"]
    assert "rs_short" not in cs and "rs_mid" not in cs
    assert cs["roc"] == 1.98
    assert cs["up_percent"] == 75.0


def test_render_sector_metrics_block_lists_fields():
    block = sector_analyst._render_sector_metrics(
        "Cao su",
        {"rs_relative": 76.1, "roc": 1.98, "up_percent": 75.0},
    )
    assert "Cao su" in block
    assert "RS relative" in block and "76.10" in block
    assert "ROC" in block


def test_render_sector_metrics_block_missing_sector():
    block = sector_analyst._render_sector_metrics("Cao su", None)
    assert "SECTOR_METRICS_UNAVAILABLE" in block
    assert "Cao su" in block
