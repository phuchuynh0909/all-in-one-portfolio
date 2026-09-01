"""The TCBS MCP client: loop bridge, enablement, tool listing."""
from __future__ import annotations

import asyncio

import pytest

from app.services import tcbs_mcp_client as client
from app.services.tcbs_token_store import TcbsCredentials


@pytest.fixture(autouse=True)
def _clean_client(monkeypatch):
    client.reset()
    client._cache.clear()
    monkeypatch.delenv("TCBS_ENABLED", raising=False)
    yield
    client.reset()
    client._cache.clear()


def _creds() -> TcbsCredentials:
    return TcbsCredentials(
        client_id="cid",
        client_secret="csec",
        access_token="tok",
        refresh_token="ref",
        expires_at=None,
    )


def test_run_sync_executes_a_coroutine_off_the_calling_thread():
    async def work():
        await asyncio.sleep(0)
        return 42

    assert client.run_sync(work(), timeout=5) == 42


def test_run_sync_reuses_one_loop_across_calls():
    async def loop_id():
        return id(asyncio.get_running_loop())

    assert client.run_sync(loop_id(), timeout=5) == client.run_sync(loop_id(), timeout=5)


def test_run_sync_propagates_the_coroutine_exception():
    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        client.run_sync(boom(), timeout=5)


def test_enabled_is_false_without_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: None)
    assert client.enabled() is False


def test_enabled_is_false_when_switched_off(monkeypatch):
    monkeypatch.setenv("TCBS_ENABLED", "0")
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    assert client.enabled() is False


def test_enabled_is_true_with_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    assert client.enabled() is True


def test_list_tools_normalizes_the_sdk_objects(monkeypatch):
    class _Tool:
        def __init__(self, name):
            self.name = name
            self.description = f"does {name}"
            self.inputSchema = {"type": "object", "properties": {"ticker": {}}}

    class _Result:
        tools = [_Tool("getTickerOverview"), _Tool("getInsiderDealing")]

    async def fake_session():
        class _S:
            async def list_tools(self):
                return _Result()

        return _S()

    monkeypatch.setattr(client, "_session", fake_session)
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())

    tools = client.list_tools()

    assert [t["name"] for t in tools] == ["getTickerOverview", "getInsiderDealing"]
    assert tools[0]["inputSchema"]["properties"] == {"ticker": {}}


def test_list_tools_raises_unavailable_without_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: None)
    with pytest.raises(client.TcbsUnavailable):
        client.list_tools()
