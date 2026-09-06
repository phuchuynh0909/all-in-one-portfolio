"""TradingAgents multi-agent analysis API.

Runs the vendored TauricResearch/TradingAgents multi-agent graph (market +
news analysts -> bull/bear debate -> trader -> risk management -> portfolio
manager) against this platform's Vietnamese-market data, driven by a local
Ollama server. Progress and section reports stream to the client via SSE.

See app/services/tradingagents/ for the data adapter, runner, and setup notes.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, Generator, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.services import tcbs_oauth
from app.services.tcbs_token_store import load as load_credentials

router = APIRouter(prefix="/trading-agents", tags=["trading-agents"])


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    trade_date: Optional[str] = Field(
        None, description="Analysis date YYYY-MM-DD; defaults to today"
    )
    analysts: Optional[List[str]] = Field(
        None,
        description=(
            "Analyst subset; defaults to runner.DEFAULT_ANALYSTS (the ones backed "
            "by VN data)"
        ),
    )
    # Model overrides for this run. Each accepts a bare model name (served by the
    # env-configured provider) or a "provider:model" spec, so roles can run on
    # different providers — e.g. deep on openai, quick on deepseek.
    quick_think_llm: Optional[str] = Field(
        None,
        description=(
            "Default analyst model (also researchers/trader/risk), optionally "
            "'provider:model'; defaults to TRADINGAGENTS_QUICK_THINK_LLM"
        ),
    )
    deep_think_llm: Optional[str] = Field(
        None,
        description=(
            "Research- and portfolio-manager model, optionally 'provider:model'; "
            "defaults to TRADINGAGENTS_DEEP_THINK_LLM"
        ),
    )
    analyst_models: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Per-analyst model, e.g. {\"market\": \"deepseek:deepseek-v4-flash\"}. "
            "Wins over quick_think_llm for that analyst only. Keys: "
            "market | news | fundamentals | social"
        ),
    )


def _validated_analyst_models(request: AnalyzeRequest) -> Dict[str, str]:
    """Per-analyst overrides, rejected up front rather than mid-stream.

    A bad key would otherwise be silently ignored (the runner only looks up the
    analysts it knows), so the frontend would show a run that quietly used the
    default model.
    """
    from app.services.tradingagents.runner import ANALYST_MODEL_KEYS

    models = {
        analyst: str(model).strip()
        for analyst, model in (request.analyst_models or {}).items()
        if str(model).strip()
    }
    unknown = sorted(set(models) - set(ANALYST_MODEL_KEYS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown analyst(s) in analyst_models: {', '.join(unknown)}. "
                f"Valid keys: {', '.join(ANALYST_MODEL_KEYS)}."
            ),
        )
    return models


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/health")
def health() -> dict:
    """Report whether every configured LLM provider is ready, and which models."""
    from app.services.tradingagents.runner import (
        build_config,
        check_backend,
        providers_in_use,
    )
    from app.services.tradingagents import web_search

    cfg = build_config()
    ok, message = check_backend(cfg)
    return {
        "backend_ready": ok,
        # Back-compat with the frontend, which reads `ollama_reachable`.
        "ollama_reachable": ok,
        "message": message,
        # Default provider for bare model names; a run may use several.
        "provider": cfg["llm_provider"],
        "providers": list(providers_in_use(cfg)),
        "deep_think_llm": cfg["deep_think_llm"],
        "quick_think_llm": cfg["quick_think_llm"],
        # Analysts left out of this map run on quick_think_llm.
        "analyst_llms": cfg.get("analyst_llms") or {},
        # role -> {provider, model}, the full assignment (base_url withheld).
        "llm_roles": {
            role: {"provider": spec["provider"], "model": spec["model"]}
            for role, spec in (cfg.get("llm_roles") or {}).items()
        },
        "web_search_backend": web_search.active_backend(),
    }


@router.get("/models")
def list_models() -> dict:
    """Model choices per provider, for the frontend's pickers.

    Catalog entries are a convenience, not a whitelist: any model a provider
    serves is accepted by ``/analyze/stream`` (Ollama, OpenRouter and the like are
    open-ended, so the catalog offers a "custom" entry rather than a complete
    list). ``ready`` says whether that provider's API key is present — an
    unqualified pick still goes to ``provider``, so a picker can offer
    ``provider:model`` specs from any ready provider and mix them across roles.
    """
    from app.services.tradingagents.runner import (
        ANALYST_MODEL_KEYS,
        DEFAULT_ANALYSTS,
        build_config,
        is_local_provider,
    )

    cfg = build_config()
    provider = str(cfg["llm_provider"])

    providers: dict[str, dict] = {}
    try:
        from tradingagents.llm_clients.api_key_env import get_api_key_env
        from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

        for name, modes in MODEL_OPTIONS.items():
            key_env = get_api_key_env(name)
            providers[name] = {
                "quick": [m for _label, m in modes.get("quick", ()) if m != "custom"],
                "deep": [m for _label, m in modes.get("deep", ()) if m != "custom"],
                "key_env": key_env,
                # Local runtimes and keyless relays authenticate with nothing.
                "ready": bool(
                    is_local_provider(name) or not key_env or os.getenv(key_env)
                ),
            }
    except Exception as exc:  # noqa: BLE001 — catalog drift must not break the page
        logger.warning("Could not read the model catalog: {}", exc)

    # The configured models are the ones this deployment actually runs, and they
    # are routinely newer than the vendored catalog — offer them as picks instead
    # of leaving the operator to retype them.
    for role, spec in (cfg.get("llm_roles") or {}).items():
        entry = providers.setdefault(
            str(spec["provider"]),
            {"quick": [], "deep": [], "key_env": None, "ready": True},
        )
        mode = "deep" if role == "deep" else "quick"
        if spec["model"] not in entry[mode]:
            entry[mode].insert(0, spec["model"])

    entry = providers.get(provider, {})
    return {
        "provider": provider,
        "providers": providers,
        # Back-compat: the default provider's own catalog.
        "options": {"quick": entry.get("quick", []), "deep": entry.get("deep", [])},
        "defaults": {
            "deep_think_llm": cfg["deep_think_llm"],
            "quick_think_llm": cfg["quick_think_llm"],
            "analyst_llms": cfg.get("analyst_llms") or {},
            "llm_roles": {
                role: {"provider": spec["provider"], "model": spec["model"]}
                for role, spec in (cfg.get("llm_roles") or {}).items()
            },
        },
        "analyst_keys": list(ANALYST_MODEL_KEYS),
        "default_analysts": list(DEFAULT_ANALYSTS),
    }


@router.get("/analyses")
def list_analyses(
    symbol: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """List saved analyses (metadata + snippet), newest first."""
    from app.services.tradingagents import store

    return {"analyses": store.list_analyses(symbol=symbol, limit=limit)}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str) -> dict:
    """Fetch one saved analysis with its full per-agent reports."""
    from app.services.tradingagents import store

    record = store.get_analysis(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return record


@router.post("/analyze/stream")
def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    """Start a multi-agent analysis and stream its progress via SSE.

    The response streams the run but no longer drives it: the job outlives this
    request, so closing the connection detaches this viewer and leaves the
    analysis to finish and save itself. There is deliberately no cancel — a
    started run always completes, and the concurrency cap is what bounds the cost.
    """
    # Imported here so a heavy/broken TradingAgents install can't crash app
    # startup — only requests to this endpoint pay the import cost.
    from app.services.tradingagents import jobs
    from app.services.tradingagents.runner import (
        DEFAULT_ANALYSTS,
        check_backend,
        run_analysis_stream,
    )

    symbol = request.symbol.strip().upper()
    trade_date = request.trade_date or date.today().strftime("%Y-%m-%d")
    # Fall back to the runner's list rather than a second hardcoded copy, so
    # enabling an analyst there actually reaches this endpoint.
    analysts = tuple(request.analysts) if request.analysts else DEFAULT_ANALYSTS
    # Raised before the StreamingResponse so a bad key is a 400, not an SSE
    # error event the caller has to dig out of the stream.
    analyst_models = _validated_analyst_models(request)

    logger.info("TradingAgents analyze: {} on {}", symbol, trade_date)

    # Checked before the job is created, for the same reason: an unreachable LLM
    # backend is a 503 the caller can act on, not an error event mid-stream, and
    # a run that cannot start must not occupy one of the concurrency slots.
    ok, message = check_backend()
    if not ok:
        raise HTTPException(status_code=503, detail=message)

    def make_events():
        return run_analysis_stream(
            symbol,
            trade_date,
            analysts,
            deep_think_llm=request.deep_think_llm,
            quick_think_llm=request.quick_think_llm,
            analyst_llms=analyst_models,
        )

    try:
        job = jobs.start(symbol, trade_date, make_events)
    except jobs.TooManyRuns as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    def event_generator() -> Generator[str, None, None]:
        # No try/finally closing anything: this generator owns the subscription,
        # not the run. A disconnect closes it at the yield and the job carries on.
        for event_type, data in jobs.subscribe(job):
            yield _sse(event_type, data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers a proxied response by default, which holds each
            # event — heartbeats included — until its buffer fills. That defeats
            # both the keepalive and the live progress the stream exists for.
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# TCBS connector login
#
# The MCP tier that serves fundamentals, statements, news and insider dealing
# authorizes over OAuth 2.0 with an iOTP confirmation, so connecting it is
# inherently a browser round trip. ``backend/scripts/tcbs_login.py`` does the
# same handshake from a terminal; these routes do it from the app, sharing the
# protocol in ``app.services.tcbs_oauth``.
# ---------------------------------------------------------------------------

#: In-flight logins, keyed by the ``state`` we minted: the PKCE verifier and the
#: client credentials the callback needs to finish. Process-local, which is
#: sound because the app runs as a single uvicorn process; a multi-worker
#: deployment would have to move this to the database.
_PENDING: Dict[str, dict] = {}

#: How long a user has to get through the TCBS login and iOTP prompt.
_PENDING_TTL_SECONDS = 900

#: Where the callback sends the browser when it has no safe return URL.
_DONE_HTML = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>TCBS connected</title>"
    "<body style='font-family:system-ui;padding:3rem;max-width:32rem'>"
    "<h2>TCBS connected.</h2>"
    "<p>You can close this tab and return to the app.</p>"
)


def _expire_pending() -> None:
    """Drop flows nobody completed. Keeps a stale verifier from lingering."""
    import time

    now = time.monotonic()
    for state in [s for s, f in _PENDING.items() if now - f["started"] > _PENDING_TTL_SECONDS]:
        _PENDING.pop(state, None)


def _safe_return_to(candidate: Optional[str]) -> Optional[str]:
    """Allow a post-login redirect only back to a configured app origin.

    ``return_to`` arrives from the browser, so an unchecked value would turn the
    callback into an open redirect.
    """
    if not candidate:
        return None
    from urllib.parse import urlsplit

    from app.core.settings import settings

    origin = urlsplit(candidate)
    if not origin.scheme or not origin.netloc:
        return None
    allowed = {
        urlsplit(str(o)).netloc for o in (settings.backend_cors_origins or []) if o
    }
    return candidate if origin.netloc in allowed else None


@router.get("/tcbs/status")
def tcbs_status() -> dict:
    """Whether the TCBS connector is connected, and whether its token is spent."""
    try:
        return tcbs_oauth.describe(load_credentials())
    except Exception as exc:  # noqa: BLE001 -- a store failure is "not connected"
        logger.warning("TCBS status lookup failed: {}", exc)
        return {"connected": False, "expired": False, "expires_at": None}


@router.get("/tcbs/authorize")
def tcbs_authorize(request: Request, return_to: Optional[str] = None) -> dict:
    """Start a login and hand back the URL to send the user to.

    Deliberately returns the URL rather than redirecting: the caller reaches
    this over XHR with its bearer token, then navigates itself.
    """
    _expire_pending()

    redirect_uri = _redirect_uri()
    try:
        meta = tcbs_oauth.discover_auth_server()
        if "registration_endpoint" not in meta:
            raise tcbs_oauth.TcbsOAuthError(
                "the authorization server advertises no registration endpoint"
            )
        client_id, client_secret = tcbs_oauth.register_client(
            meta["registration_endpoint"], redirect_uri
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the user as a 502
        logger.warning("TCBS authorize failed: {}", exc)
        raise HTTPException(status_code=502, detail=f"TCBS login unavailable: {exc}")

    import secrets
    import time

    verifier, challenge = tcbs_oauth.pkce_pair()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = {
        "verifier": verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "meta": meta,
        "return_to": _safe_return_to(return_to),
        "started": time.monotonic(),
    }
    return {
        "authorization_url": tcbs_oauth.authorization_url(
            meta,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            challenge=challenge,
        ),
        # Handed back so the UI can name the address that is about to fail to
        # load, and say that failing is the expected outcome.
        "redirect_uri": redirect_uri,
    }


#: Port in the loopback redirect_uri. Nothing listens on it -- see
#: ``_redirect_uri`` for why that is deliberate rather than broken.
_LOOPBACK_PORT = os.getenv("TCBS_LOOPBACK_PORT", "8765")


def _redirect_uri() -> str:
    """Use TCBS's loopback-only native-app callback shape.

    TCBS rejects hosted callback origins at its authorization endpoint. Nothing
    listens on this loopback port: after consent, the browser exposes the code
    in its failed navigation URL, which the user pastes into ``/tcbs/complete``.
    """
    return f"http://127.0.0.1:{_LOOPBACK_PORT}/callback"


class TcbsCompleteRequest(BaseModel):
    """Whatever the user copied out of the address bar."""

    pasted: str = Field(..., min_length=1, max_length=4096)


def _parse_pasted(pasted: str) -> dict[str, str]:
    """Pull the OAuth parameters out of a pasted URL.

    Tolerant on purpose: people paste the whole address, sometimes only the
    query string, occasionally with surrounding whitespace or quotes. Anything
    with a recognisable query is accepted; anything else is a 400 telling them
    what to copy.
    """
    import urllib.parse

    text = pasted.strip().strip('"\'')
    query = text.split("?", 1)[1] if "?" in text else text
    query = query.lstrip("#")
    params = {
        key: values[0]
        for key, values in urllib.parse.parse_qs(query, keep_blank_values=False).items()
        if values
    }
    if not params:
        raise HTTPException(
            status_code=400,
            detail=(
                "That does not look like the redirect URL. Copy the whole "
                "address from the browser bar after authorizing -- it contains "
                "'?code=' and '&state='."
            ),
        )
    return params


@router.post("/tcbs/complete")
def tcbs_complete(payload: TcbsCompleteRequest) -> dict:
    """Finish a login from the URL the user pasted.

    The counterpart to ``tcbs_callback`` for the loopback flow: TCBS redirects
    the browser to an address nothing is serving, so the code never reaches us
    on its own and the user carries it here by hand. Guarded normally -- unlike
    the callback, this is called from inside the app with a bearer token.
    """
    _expire_pending()
    params = _parse_pasted(payload.pasted)

    state = params.get("state")
    # Popped, not read: a pasted URL is a credential, and a replay must miss.
    flow = _PENDING.pop(state, None) if state else None
    if flow is None:
        raise HTTPException(
            status_code=400,
            detail="Unknown or expired login state. Start the login again.",
        )

    if params.get("error"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"TCBS refused the login: {params['error']} "
                f"({params.get('error_description', '')})".strip()
            ),
        )
    if not params.get("code"):
        raise HTTPException(
            status_code=400,
            detail="That URL carries no authorization code. Copy the whole address.",
        )

    _complete_flow(flow, params["code"])
    return tcbs_oauth.describe(load_credentials())


def _complete_flow(flow: dict, code: str) -> None:
    """Trade ``code`` for tokens and store them.

    Shared by the redirect callback and the paste endpoint so the exchange,
    the store, and the session reset exist once rather than twice.
    """
    try:
        payload = tcbs_oauth.exchange_code(
            flow["meta"],
            code=code,
            redirect_uri=flow["redirect_uri"],
            client_id=flow["client_id"],
            client_secret=flow["client_secret"],
            verifier=flow["verifier"],
        )
        tcbs_oauth.store_tokens(flow["client_id"], flow["client_secret"], payload)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the user as a 502
        logger.warning("TCBS token exchange failed: {}", exc)
        raise HTTPException(status_code=502, detail=f"TCBS token exchange failed: {exc}")

    # A new token means the client is holding a session authorized by the old
    # one; drop it so the next call reconnects with the new credentials.
    try:
        from app.services import tcbs_mcp_client

        tcbs_mcp_client.reset()
    except Exception as exc:  # noqa: BLE001 -- a stale session is not fatal
        logger.debug("TCBS session reset after login failed (ignored): {}", exc)

    logger.info("TCBS connector authorized")


@router.get("/tcbs/callback")
def tcbs_callback(
    state: Optional[str] = None,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Finish a login. Exempt from the auth guard: TCBS redirects a bare browser
    here, with no Authorization header to carry. The unguessable ``state`` we
    minted is what makes the route safe to leave open -- without a matching
    in-flight flow there is nothing to complete.
    """
    _expire_pending()

    # Popped, not read: a code may be redeemed once, and a replay must miss.
    flow = _PENDING.pop(state, None) if state else None
    if flow is None:
        raise HTTPException(
            status_code=400, detail="Unknown or expired login state. Start the login again."
        )
    if error:
        raise HTTPException(
            status_code=400, detail=f"TCBS refused the login: {error} ({error_description or ''})"
        )
    if not code:
        raise HTTPException(status_code=400, detail="TCBS returned no authorization code.")

    _complete_flow(flow, code)
    if flow["return_to"]:
        return RedirectResponse(flow["return_to"], status_code=302)
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_DONE_HTML)
