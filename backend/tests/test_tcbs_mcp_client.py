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
            # The 2.x SDK model field. Reading the ``inputSchema`` alias off the
            # object instead returns an empty default with no error, which
            # silently dumps 54 argument-less tools.
            self.input_schema = {"type": "object", "properties": {"ticker": {}}}

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


class _FakeResult:
    """Mimics the SDK's CallToolResult shape."""

    def __init__(self, payload=None, text=None, is_error=False):
        self.structuredContent = payload
        self.isError = is_error
        self.content = []
        if text is not None:
            class _Block:
                type = "text"

            block = _Block()
            block.text = text
            self.content = [block]


def _install_session(monkeypatch, handler):
    """Point the client at a fake session whose call_tool runs ``handler``."""

    class _S:
        async def call_tool(self, name, arguments):
            return handler(name, arguments)

    async def fake_session():
        return _S()

    monkeypatch.setattr(client, "_session", fake_session)
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())


def test_call_returns_the_structured_payload(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(payload={"ticker": "TCB", "pe": 9.1})
    )
    assert client.call("getStockRatio", ticker="TCB") == {"ticker": "TCB", "pe": 9.1}


def test_call_falls_back_to_parsing_the_text_block(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(text='{"ticker": "HPG"}')
    )
    assert client.call("getTickerOverview", ticker="HPG") == {"ticker": "HPG"}


def test_call_raises_no_data_on_an_empty_payload(monkeypatch):
    _install_session(monkeypatch, lambda name, args: _FakeResult(payload={}))
    with pytest.raises(client.TcbsNoData):
        client.call("getStockRatio", ticker="ZZZZ")


def test_call_raises_no_data_when_the_tool_reports_an_error(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(text="not found", is_error=True)
    )
    with pytest.raises(client.TcbsNoData):
        client.call("getStockRatio", ticker="ZZZZ")


def test_call_caches_by_tool_and_arguments(monkeypatch):
    calls = []

    def handler(name, args):
        calls.append((name, tuple(sorted(args.items()))))
        return _FakeResult(payload={"n": len(calls)})

    _install_session(monkeypatch, handler)

    assert client.call("getStockRatio", ticker="TCB") == {"n": 1}
    assert client.call("getStockRatio", ticker="TCB") == {"n": 1}  # cached
    assert client.call("getStockRatio", ticker="HPG") == {"n": 2}  # different args
    assert len(calls) == 2


def test_call_ignores_a_stale_cache_entry(monkeypatch):
    monkeypatch.setattr(client, "CACHE_TTL_SECONDS", 0.0)
    seen = []

    def handler(name, args):
        seen.append(name)
        return _FakeResult(payload={"n": len(seen)})

    _install_session(monkeypatch, handler)
    client.call("getStockRatio", ticker="TCB")
    client.call("getStockRatio", ticker="TCB")
    assert len(seen) == 2


def test_call_refreshes_once_on_unauthorized_then_succeeds(monkeypatch):
    attempts = []

    def handler(name, args):
        attempts.append(name)
        if len(attempts) == 1:
            raise RuntimeError("HTTP 401 Unauthorized")
        return _FakeResult(payload={"ok": True})

    _install_session(monkeypatch, handler)
    refreshed = []
    monkeypatch.setattr(client, "_refresh", lambda: refreshed.append(1) or True)
    monkeypatch.setattr(client, "reset", lambda: None)

    assert client.call("getStockRatio", ticker="TCB") == {"ok": True}
    assert len(refreshed) == 1
    assert len(attempts) == 2


def test_call_gives_up_when_the_refresh_fails(monkeypatch):
    def handler(name, args):
        raise RuntimeError("HTTP 401 Unauthorized")

    _install_session(monkeypatch, handler)
    monkeypatch.setattr(client, "_refresh", lambda: False)
    monkeypatch.setattr(client, "reset", lambda: None)

    with pytest.raises(client.TcbsUnavailable, match="re-authorize"):
        client.call("getStockRatio", ticker="TCB")


def test_call_does_not_retry_a_non_auth_failure(monkeypatch):
    attempts = []

    def handler(name, args):
        attempts.append(name)
        raise RuntimeError("connection reset")

    _install_session(monkeypatch, handler)
    monkeypatch.setattr(client, "reset", lambda: None)

    with pytest.raises(client.TcbsUnavailable):
        client.call("getStockRatio", ticker="TCB")
    assert len(attempts) == 1


def test_call_raises_unavailable_when_disabled(monkeypatch):
    monkeypatch.setenv("TCBS_ENABLED", "0")
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    with pytest.raises(client.TcbsUnavailable):
        client.call("getStockRatio", ticker="TCB")
