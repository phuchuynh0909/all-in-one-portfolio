"""TCBS tiers: present when TCBS answers, invisible when it does not."""
from __future__ import annotations

import pytest

from app.services import tcbs_mcp_client as client
from app.services.tradingagents import tcbs_tiers


@pytest.fixture
def tcbs(monkeypatch):
    """Route tcbs_tiers at a scripted set of tool responses.

    Keys are the bare tool names; the prefix the server actually uses is added
    by ``_try``, so the fixture asserts that prefixing happens.
    """

    def install(responses: dict):
        def fake_call(tool_name, **params):
            assert tool_name.startswith("tcinvest-"), tool_name
            bare = tool_name[len("tcinvest-"):]
            if bare not in responses:
                raise client.TcbsNoData(f"no fixture for {bare}")
            value = responses[bare]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(tcbs_tiers.tcbs, "call", fake_call)
        monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: True)

    return install


def test_rows_unwraps_the_documented_envelopes():
    # Each tool family wraps its list under a different key, so the unwrap is
    # generic: one key whose value is a list.
    assert tcbs_tiers._rows([{"a": 1}]) == [{"a": 1}]
    assert tcbs_tiers._rows({"listInsiderDealing": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"listVolumeForeignInfoDto": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"value": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"result": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"listActivityNews": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows(None) == []


def test_rows_treats_a_flat_record_as_one_row():
    assert tcbs_tiers._rows({"pe": 8.7, "pb": 1.3}) == [{"pe": 8.7, "pb": 1.3}]


def test_vn_date_normalizes_the_two_digit_year_form():
    assert tcbs_tiers._vn_date("04/11/25") == "2025-11-04"
    assert tcbs_tiers._vn_date("28/05/2026") == "2026-05-28"
    assert tcbs_tiers._vn_date("2026-07-23 17:39:48") == "2026-07-23"
    assert tcbs_tiers._vn_date("") == "-"


def test_insider_block_renders_the_deals(tcbs):
    tcbs({
        "getInsiderDealing": {"listInsiderDealing": [
            {"anDate": "04/11/25", "dealingAction": "0", "quantity": 800000.0,
             "price": 34279.0, "ratio": -0.026},
            {"anDate": "18/09/25", "dealingAction": "1", "quantity": -520000.0,
             "price": 37020.0, "ratio": -0.098},
        ]},
        "getVolumeAndForeign": {"listVolumeForeignInfoDto": [
            {"dateReport": "28/05/2026", "foreignBuy": 116365, "foreignSell": -1172747,
             "netForeignVol": -1056382, "totalVolume": 8077139, "rsRank": 38.0},
        ]},
    })

    block = tcbs_tiers.insider_transactions("TCB")

    assert block is not None
    assert "2025-11-04" in block      # date normalized out of dd/mm/yy
    assert "Buy" in block and "Sell" in block
    assert "800,000" in block
    assert "TCBS" in block


def test_insider_block_does_not_invent_a_dealer_name(tcbs):
    # The feed carries no person or position. A column of "-" would read as
    # missing data rather than absent-by-design, so the table must not have one.
    tcbs({"getInsiderDealing": {"listInsiderDealing": [
        {"anDate": "04/11/25", "dealingAction": "0", "quantity": 1000.0, "price": 100.0},
    ]}})
    block = tcbs_tiers.insider_transactions("TCB")
    assert "Person" not in block and "Position" not in block


def test_insider_block_reports_foreign_flow(tcbs):
    tcbs({
        "getInsiderDealing": {"listInsiderDealing": [
            {"anDate": "04/11/25", "dealingAction": "0", "quantity": 10.0},
        ]},
        "getVolumeAndForeign": {"listVolumeForeignInfoDto": [
            {"dateReport": "28/05/2026", "netForeignVol": -1056382, "rsRank": 38.0},
            {"dateReport": "29/05/2026", "netForeignVol": -791115, "rsRank": 31.0},
        ]},
    })
    block = tcbs_tiers.insider_transactions("TCB")
    assert "2026-05-29" in block   # the latest session, not the first row
    assert "RS rank" in block


def test_insider_block_is_none_when_tcbs_has_nothing(tcbs):
    tcbs({"getInsiderDealing": client.TcbsNoData("nothing")})
    assert tcbs_tiers.insider_transactions("ZZZZ") is None


def test_insider_block_is_none_when_tcbs_is_unavailable(tcbs):
    tcbs({"getInsiderDealing": client.TcbsUnavailable("no token")})
    assert tcbs_tiers.insider_transactions("TCB") is None


def test_insider_block_is_none_when_the_tier_is_disabled(monkeypatch):
    monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: False)
    assert tcbs_tiers.insider_transactions("TCB") is None


def test_insider_block_survives_a_partial_failure(tcbs):
    # Foreign flow is a bonus block; losing it must not lose the deals.
    tcbs({
        "getInsiderDealing": {"listInsiderDealing": [
            {"anDate": "04/11/25", "dealingAction": "1", "quantity": -1.0},
        ]},
        "getVolumeAndForeign": client.TcbsUnavailable("boom"),
    })
    block = tcbs_tiers.insider_transactions("TCB")
    assert block is not None and "Sell" in block


def test_vn_data_tool_uses_the_tcbs_block_when_present(monkeypatch):
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(
        vn_data.tcbs_tiers, "insider_transactions", lambda sym: f"# {sym} insider block"
    )
    assert vn_data.get_insider_transactions("TCB") == "# TCB insider block"


def test_vn_data_tool_keeps_the_sentinel_when_tcbs_is_absent(monkeypatch):
    # The regression guard: with no TCBS, behaviour is byte-identical to before.
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(vn_data.tcbs_tiers, "insider_transactions", lambda sym: None)
    result = vn_data.get_insider_transactions("TCB")
    assert result.startswith("INSIDER_DATA_UNAVAILABLE:")
    assert "TCB" in result
