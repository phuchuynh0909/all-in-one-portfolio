"""Client for the ruatichsan.com financial-statement API.

Two things make this API awkward enough to want a dedicated client:

  * **Encrypted responses.** When a response carries ``X-Encrypted: 1`` the body
    is not JSON but raw AES-256-GCM: ``iv (12 bytes) || ciphertext || tag (16
    bytes)``. That layout is what the browser's WebCrypto ``decrypt`` consumes
    (it expects the tag appended to the ciphertext); PyCryptodome wants the two
    split, hence the slicing in :func:`decrypt_payload`.
  * **A two-device login cap.** The server fingerprints a device from the
    ``User-Agent``: logging in with no UA registers a *new* device (observed:
    ``python-requests`` → device 133) while a stable UA re-uses the existing row
    (a Chrome/macOS UA → device 131, the browser's own). The access token carries
    the device in its ``did`` claim. Two rules follow, both implemented here:

      1. Always send the same :data:`USER_AGENT`, so every login lands on one
         device instead of consuming a slot per run.
      2. Prefer ``POST /auth/refresh`` over re-logging in — it mints a new access
         token on the *same* device and never touches the cap. Login is the last
         resort.

    Refresh tokens rotate on every use (the response sets a fresh cookie), so the
    new value has to be persisted or the next refresh fails; the token cache
    handles that and falls back to login if the chain is ever broken.

Access tokens live 15 minutes, refresh tokens 30 days. None of that applies to
:func:`fetch_financial_statements`, which goes through the public route and so
needs no credentials at all; the login machinery below is for callers that pass
a token of their own, and as a fallback if that route ever closes.

Configuration (all optional except credentials, when logging in):

    RUATICHSAN_EMAIL          account email / phone
    RUATICHSAN_PASSWORD       account password
    RUATICHSAN_TOKEN          pre-obtained access token; skips login entirely
    RUATICHSAN_REFRESH_TOKEN  seed refresh token (e.g. copied from a browser)
    RUATICHSAN_USER_AGENT     device identity; keep it stable (see above)
    RUATICHSAN_BASE_URL       default https://api.ruatichsan.com/api/v1
    RUATICHSAN_ENC_KEY        64-hex-char AES-256 key override
    RUATICHSAN_TOKEN_CACHE    token cache path (default: <tmp>/ruatichsan_token.json)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("RUATICHSAN_BASE_URL", "https://api.ruatichsan.com/api/v1").rstrip("/")
TIMEOUT = float(os.getenv("RUATICHSAN_TIMEOUT", "30"))

# The client-side key is split across two constants in the site's bundle (Hl, Vl);
# concatenated they form the 32-byte AES-256 key.
_KEY_HI = "19dd3af428f4cf7d68864cd4c87d8d1c"
_KEY_LO = "5b489932e84b93ac6528a0dd403a5725"

_IV_LEN = 12
_TAG_LEN = 16

# Path segments the API accepts for the reporting period.
PERIODS = ("quarter", "annual")

# Two routes serve the same statements. The public one needs no Authorization
# header at all and returns the identical encrypted payload (same AES key, same
# `fiscalDates`/`cdkt`/`kqkd`/`lctt` shape, 34 quarters / 8-20 years observed),
# so the default path costs no token, no login and no device slot. The private
# one is kept for callers who hand us a token of their own.
PUBLIC_STATEMENTS_PATH = "data/public/financial-statements"
PRIVATE_STATEMENTS_PATH = "data/financial-statements"

# The server derives the device from this string, so it must not vary between
# runs — see the module docstring. Point it at your browser's exact UA to share
# that device (costing zero extra slots); leave it as-is to own one dedicated
# slot that never multiplies.
USER_AGENT = os.getenv(
    "RUATICHSAN_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# Refresh a little before the 15-minute expiry so a token can't die mid-request.
_EXPIRY_SKEW_SECONDS = 60

_token_cache: dict[str, Any] = {}  # process-local mirror of the on-disk cache


class RuatichsanError(RuntimeError):
    """Any failure talking to the API."""


class DeviceLimitError(RuatichsanError):
    """Login refused because the account's device slots are full.

    Carries the device list the API returns so a caller can show the user which
    session to revoke.
    """

    def __init__(self, message: str, devices: list[dict[str, Any]]):
        super().__init__(message)
        self.devices = devices


def _enc_key() -> bytes:
    hex_key = os.getenv("RUATICHSAN_ENC_KEY") or (_KEY_HI + _KEY_LO)
    hex_key = hex_key.strip()
    if len(hex_key) != 64:
        raise RuatichsanError(
            f"encryption key must be 64 hex chars (32 bytes), got {len(hex_key)}"
        )
    return bytes.fromhex(hex_key)


def decrypt_payload(blob: bytes) -> Any:
    """Decrypt an ``X-Encrypted: 1`` body into the JSON value it holds.

    Layout is ``iv(12) || ciphertext || tag(16)`` — the browser treats the trailing
    tag as part of the ciphertext, PyCryptodome takes it separately.
    """
    if len(blob) <= _IV_LEN + _TAG_LEN:
        raise RuatichsanError(f"encrypted body too short ({len(blob)} bytes)")

    from Cryptodome.Cipher import AES

    iv, body, tag = blob[:_IV_LEN], blob[_IV_LEN:-_TAG_LEN], blob[-_TAG_LEN:]
    cipher = AES.new(_enc_key(), AES.MODE_GCM, nonce=iv)
    try:
        plaintext = cipher.decrypt_and_verify(body, tag)
    except ValueError as exc:  # bad tag → wrong key or corrupted body
        raise RuatichsanError(f"decryption failed (bad key or corrupt body): {exc}") from exc
    return json.loads(plaintext.decode("utf-8"))


def _read_response(resp) -> Any:
    """Return a response's JSON, decrypting first when the API says it's encrypted."""
    if resp.headers.get("X-Encrypted") == "1":
        return decrypt_payload(resp.content)
    return resp.json()


# ── token handling ──────────────────────────────────────────────────────────
def _cache_path() -> Path:
    return Path(
        os.getenv("RUATICHSAN_TOKEN_CACHE")
        or Path(tempfile.gettempdir()) / "ruatichsan_token.json"
    )


def _load_cache() -> dict[str, Any]:
    global _token_cache
    if _token_cache:
        return _token_cache
    try:
        data = json.loads(_cache_path().read_text())
        if isinstance(data, dict):
            _token_cache = data
    except Exception:  # noqa: BLE001 — a missing/corrupt cache just means "log in"
        pass
    return _token_cache


def _store_cache(**fields: Any) -> None:
    """Merge ``fields`` into the token cache and persist atomically.

    Atomic because a half-written file would lose the rotated refresh token and
    force a fresh login (i.e. burn the device slot this module exists to protect).
    """
    global _token_cache
    _token_cache = {**_load_cache(), **{k: v for k, v in fields.items() if v}}
    try:
        path = _cache_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_token_cache))
        tmp.chmod(0o600)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 — caching is an optimization
        logger.warning("Could not cache ruatichsan token: %s", exc)


def token_claims(token: str) -> dict[str, Any]:
    """Decode a JWT's payload without verifying it (we only read exp / did)."""
    import base64

    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001 — an opaque token just means "can't introspect"
        return {}


def _token_is_fresh(token: str) -> bool:
    exp = token_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return True  # can't tell — let the API be the judge
    return time.time() < exp - _EXPIRY_SKEW_SECONDS


def device_id(token: str) -> Optional[int]:
    """The device ("did") an access token is bound to, if the JWT exposes it."""
    did = token_claims(token).get("did")
    return int(did) if isinstance(did, (int, float)) else None


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Base headers — always pins the User-Agent, which is the device identity."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def refresh_token() -> str:
    """Mint a new access token on the *same* device via ``POST /auth/refresh``.

    Does not touch the device cap. The refresh token rotates, so the replacement
    from the ``Set-Cookie`` header is persisted before returning.
    """
    import requests

    current = _load_cache().get("refresh_token") or os.getenv("RUATICHSAN_REFRESH_TOKEN")
    if not current:
        raise RuatichsanError("no refresh token available")

    resp = requests.post(
        f"{BASE_URL}/auth/refresh",
        headers=_headers({"Cookie": f"refresh_token={current}"}),
        timeout=TIMEOUT,
    )
    if not resp.ok:
        raise RuatichsanError(f"refresh failed: HTTP {resp.status_code} — {resp.text[:200]}")

    token = _extract_token(_read_response(resp))
    if not token:
        raise RuatichsanError("refresh succeeded but returned no access token")

    # Persist the rotated refresh token; losing it would force a re-login.
    _store_cache(token=token, refresh_token=resp.cookies.get("refresh_token"))
    return token


def _extract_token(payload: Any) -> Optional[str]:
    """Pull the access token out of a login response, whatever it calls it."""
    if not isinstance(payload, dict):
        return None
    for key in ("access_token", "accessToken", "token", "jwt"):
        value = payload.get(key)
        if value:
            return str(value)
    # Some APIs nest it one level down (e.g. {"data": {...}}).
    for nest in ("data", "result"):
        inner = payload.get(nest)
        if isinstance(inner, dict):
            found = _extract_token(inner)
            if found:
                return found
    return None


def login(email: str | None = None, password: str | None = None) -> str:
    """Log in and return an access token.

    Registers a device against the account's two-device cap — prefer a cached
    token or ``RUATICHSAN_TOKEN``. Raises :class:`DeviceLimitError` when both
    slots are already taken.
    """
    import requests

    email = email or os.getenv("RUATICHSAN_EMAIL", "kyostyle1@gmail.com")
    password = password or os.getenv("RUATICHSAN_PASSWORD", "kyostyle1")
    if not email or not password:
        raise RuatichsanError(
            "credentials missing: set RUATICHSAN_EMAIL and RUATICHSAN_PASSWORD "
            "(or pass them to login())"
        )

    headers = _headers({"Content-Type": "application/json"})
    seed = _load_cache().get("refresh_token") or os.getenv("RUATICHSAN_REFRESH_TOKEN")
    if seed:
        headers["Cookie"] = f"refresh_token={seed}"

    resp = requests.post(
        f"{BASE_URL}/auth/login",
        headers=headers,
        json={"email_or_phone": email, "password": password},
        timeout=TIMEOUT,
    )

    if resp.status_code == 409:
        detail = (resp.json() or {}).get("detail", {})
        if isinstance(detail, dict) and detail.get("error") == "device_limit_exceeded":
            raise DeviceLimitError(
                detail.get("message", "device limit exceeded"),
                detail.get("devices", []),
            )
    if not resp.ok:
        raise RuatichsanError(f"login failed: HTTP {resp.status_code} — {resp.text[:300]}")

    token = _extract_token(_read_response(resp))
    if not token:
        raise RuatichsanError("login succeeded but no access token was found in the response")

    _store_cache(token=token, refresh_token=resp.cookies.get("refresh_token"))
    logger.info("Logged in to ruatichsan (device %s)", device_id(token))
    return token


def get_token(force: bool = False) -> str:
    """An access token, in cheapest-first order.

    ``RUATICHSAN_TOKEN`` → unexpired cached token → ``/auth/refresh`` → login.
    Only the last step can consume a device slot, and the stable
    :data:`USER_AGENT` keeps even that on the same device.
    """
    if not force:
        env_token = os.getenv("RUATICHSAN_TOKEN")
        if env_token:
            return env_token.strip()
        cached = _load_cache().get("token")
        if cached and _token_is_fresh(str(cached)):
            return str(cached)

    try:
        return refresh_token()
    except RuatichsanError as exc:
        logger.info("Refresh unavailable (%s); falling back to login", exc)

    return login()


# ── data ────────────────────────────────────────────────────────────────────
def fetch_financial_statements(
    ticker: str,
    period: str = "quarter",
    token: str | None = None,
) -> Any:
    """Fetch and decrypt a ticker's financial statements.

    ``period`` is the API's path segment — ``quarter`` or ``annual``.

    Goes through :data:`PUBLIC_STATEMENTS_PATH` with no credentials at all, which
    is why the common case never touches :func:`get_token`. Authentication is
    used only when the caller supplies ``token``, or if the public route ever
    starts answering 401/403 — and then a stale cached token is renewed once.
    """
    import requests

    sym = ticker.strip().upper()
    if period not in PERIODS:
        raise RuatichsanError(f"period must be one of {PERIODS}, got {period!r}")

    # Unauthenticated by default: the public route carries the same data, so
    # there is no reason to spend a token — or a device slot — on it.
    if token is None:
        resp = requests.get(
            f"{BASE_URL}/{PUBLIC_STATEMENTS_PATH}/{period}/{sym}",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        # Only an auth-shaped refusal means "the public route is gone"; a 404 is
        # the API saying this symbol has no statements (futures, say), and
        # logging in would not conjure any.
        if resp.status_code not in (401, 403):
            if not resp.ok:
                raise RuatichsanError(
                    f"fetch failed for {sym}: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            return _read_response(resp)
        logger.info("public statements route refused (HTTP %s); authenticating", resp.status_code)

    url = f"{BASE_URL}/{PRIVATE_STATEMENTS_PATH}/{period}/{sym}"
    auth_token = token or get_token()

    for attempt in (1, 2):
        resp = requests.get(
            url,
            headers=_headers({"Authorization": f"Bearer {auth_token}"}),
            timeout=TIMEOUT,
        )
        # A stale token — renew once and retry (only when we chose the token
        # ourselves; an explicitly passed one is the caller's to manage).
        if resp.status_code in (401, 403) and attempt == 1 and token is None:
            logger.info("ruatichsan token rejected; renewing")
            auth_token = get_token(force=True)
            continue
        if not resp.ok:
            raise RuatichsanError(
                f"fetch failed for {sym}: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        return _read_response(resp)

    raise RuatichsanError(f"fetch failed for {sym}: authentication rejected twice")


# ── tabular rendering ────────────────────────────────────────────────────────
# Confirmed shape of a ``fetch_financial_statements`` response: a dict with a
# shared ``fiscalDates`` period axis and one 2-D grid per statement — each grid
# a list of ``[item_label, *period_values]`` rows, one row per line item:
#   {"fiscalDates": [...], "cdkt": [...], "kqkd": [...], "lctt": [...], "dataSource": "..."}
# cdkt = Cân đối kế toán (balance sheet), kqkd = Kết quả kinh doanh (income
# statement), lctt = Lưu chuyển tiền tệ (cash flow statement).
_PERIOD_KEYS = ("fiscalDates", "periods", "period_labels", "labels", "columns", "headers", "dates")
_STATEMENT_KEYS = {
    "cdkt": "balance_sheet",
    "kqkd": "income_statement",
    "lctt": "cash_flow",
}
# Fallback grid key names for shapes other than the confirmed one above.
_GRID_KEYS = ("rows", "data", "items", "table", "values", "statements")


def _period_label(entry: Any) -> str:
    """Turn one ``fiscalDates`` entry into a human column label.

    Entries seen: plain strings (``"2025-Q3"``, ``"30/09/2025"``) and dicts
    carrying either an explicit label/date field or a quarter+year pair.
    """
    if isinstance(entry, dict):
        for key in ("label", "name", "period", "fiscalDate", "date", "endDate", "end_date"):
            val = entry.get(key)
            if val:
                return str(val)
        quarter = entry.get("quarter") or entry.get("q")
        year = entry.get("year") or entry.get("y")
        if quarter and year:
            return f"Q{quarter}/{year}"
        if year:
            return str(year)
        return str(entry)
    return str(entry)


def _extract_periods(payload: dict) -> Optional[list]:
    """Pull a list of column labels out of a handful of common key names.

    Unlike a naive lookup, this accepts dict-shaped entries too (mapped
    through :func:`_period_label`) — ``fiscalDates`` items aren't guaranteed
    to be plain strings.
    """
    for key in _PERIOD_KEYS:
        val = payload.get(key)
        if isinstance(val, list) and val:
            return [_period_label(v) for v in val]
    return None


def _align_grid_to_periods(raw_values: list, n_full: int, periods: Optional[list]):
    """Find how many leading non-period values precede the period values.

    Some grids carry extra per-row values right after the label (an order/
    level flag, a running total, ...) before the actual period values start.
    Tries dropping 0..3 leading values and returns the first offset whose
    remaining width matches ``len(periods)``; returns offset 0 (no change) if
    none match or ``periods`` is unknown.
    """
    if periods:
        for offset in range(0, min(3, n_full) + 1):
            if n_full - offset == len(periods):
                return [v[offset:] for v in raw_values], len(periods), offset
    return raw_values, n_full, 0


def _grid_to_dataframe(grid: list, periods: Optional[list], n_periods: Optional[int] = None):
    """One statement's ``[label, *values]`` rows -> a DataFrame (item x period).

    An exact length match between ``periods`` and the row width is used
    as-is; a close mismatch is resolved by dropping 1-3 leading per-row values
    (see :func:`_align_grid_to_periods`); anything else falls back to
    ``period_0, period_1, ...`` with a warning that includes samples of both
    sides, so a genuine mismatch is easy to diagnose from the logs.

    ``n_periods`` keeps only the most recent N period columns. Periods are
    assumed oldest-to-newest (as observed in ``fiscalDates``), so "most recent"
    means the trailing columns; pass a negative number to instead keep the
    first N (oldest) if a given payload turns out to be ordered the other way.
    """
    import pandas as pd

    labels = [row[0] for row in grid]
    raw_values = [list(row[1:]) for row in grid]
    n_full = max((len(v) for v in raw_values), default=0)

    values, n_cols, offset = _align_grid_to_periods(raw_values, n_full, periods)
    if offset:
        logger.info(
            "dropped %d leading non-period value(s) per row to align with fiscalDates "
            "(row width %d -> %d)",
            offset, n_full, n_cols,
        )

    values = [v + [None] * (n_cols - len(v)) for v in values]

    if periods and len(periods) == n_cols:
        columns = periods
    else:
        if periods:
            logger.warning(
                "period label count (%d) doesn't match this statement's column "
                "count (%d) even after checking a 1-3 value offset; falling back "
                "to period_i names. periods sample=%r  first_row(label=%r) raw_len=%d "
                "raw_sample=%r",
                len(periods), n_cols, periods[:3] + (["..."] if len(periods) > 3 else []),
                labels[0] if labels else None, n_full,
                raw_values[0][:6] if raw_values else None,
            )
        else:
            logger.warning(
                "no period labels available for this statement; falling back to "
                "period_i names (row width %d)", n_cols,
            )
        columns = [f"period_{i}" for i in range(n_cols)]

    df = pd.DataFrame(values, index=pd.Index(labels, name="item"), columns=columns)
    return _limit_periods(df, n_periods)


def _limit_periods(df, n_periods: Optional[int]):
    """Slice a statement DataFrame down to N period columns.

    ``n_periods > 0`` keeps the last N columns (most recent, assuming
    oldest-to-newest column order); ``n_periods < 0`` keeps the first N
    (oldest) instead, for payloads ordered newest-to-oldest.
    """
    if not n_periods:
        return df
    return df.iloc[:, -n_periods:] if n_periods > 0 else df.iloc[:, :-n_periods]


def to_dataframe(data: Any, n_periods: Optional[int] = None):
    """Best-effort conversion of a financial-statements payload to DataFrame(s).

    Handles:
      * the confirmed multi-statement shape (``fiscalDates`` + ``cdkt``/``kqkd``/
        ``lctt`` grids) -> returns ``{"balance_sheet": df, "income_statement":
        df, "cash_flow": df}`` (only the statements actually present);
      * a bare list of record dicts -> one row per record (single DataFrame);
      * a dict wrapping a single 2-D grid under a generic key (``rows``/``data``/
        etc.), with period labels picked up from a handful of common key names
        if present (falls back to ``period_0, period_1, ...`` otherwise).

    ``n_periods``, if given, limits every statement to its N most recent
    periods (see :func:`_limit_periods`). Not applied to the bare
    record-dicts shape, since there's no reliable period axis to slice there.
    """
    import pandas as pd

    payload = data

    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return pd.DataFrame(payload)
        if payload and isinstance(payload[0], list):
            return _grid_to_dataframe(payload, None, n_periods)
        raise RuatichsanError(f"empty or unrecognized list payload: {payload!r}"[:300])

    if not isinstance(payload, dict):
        raise RuatichsanError(f"unsupported payload type: {type(payload).__name__}")

    periods = _extract_periods(payload)
    if periods:
        logger.info(
            "period labels: %d found under one of %s, sample=%r",
            len(periods), _PERIOD_KEYS, periods[:2] + (["..."] if len(periods) > 2 else []) + periods[-1:],
        )
    else:
        logger.warning(
            "no period labels found under any of %s (top-level keys: %s)",
            _PERIOD_KEYS, list(payload.keys()),
        )

    # Confirmed shape: several statement keys, each its own grid, sharing `periods`.
    statement_grids = {
        name: payload[key]
        for key, name in _STATEMENT_KEYS.items()
        if isinstance(payload.get(key), list) and payload[key] and isinstance(payload[key][0], list)
    }
    if statement_grids:
        return {
            name: _grid_to_dataframe(grid, periods, n_periods)
            for name, grid in statement_grids.items()
        }

    # Fallback: a single generic grid/records key.
    for key in _GRID_KEYS:
        val = payload.get(key)
        if isinstance(val, list) and val:
            if isinstance(val[0], dict):
                return pd.DataFrame(val)
            if isinstance(val[0], list):
                return _grid_to_dataframe(val, periods, n_periods)

    raise RuatichsanError(
        f"could not find a tabular grid in the response (top-level: {list(payload.keys())}); "
        "inspect the raw JSON and adjust ruatichsan_client._STATEMENT_KEYS / _GRID_KEYS / _PERIOD_KEYS"
    )


def _scale_df(df, scale: float, decimals: int):
    import pandas as pd

    numeric = df.apply(pd.to_numeric, errors="coerce")
    scaled = (numeric / scale).round(decimals)
    # Keep non-numeric cells (labels ended up here via a bad offset, stray
    # strings, None) as they were instead of turning them into NaN.
    return scaled.where(numeric.notna(), df)


def format_scaled(df_or_dict, scale: float = 1e12, decimals: int = 3):
    """Scale numeric values for display, e.g. raw VND -> nghìn tỷ đồng.

    ``3.15481e+13`` at the default ``scale=1e12`` becomes ``31.548``. Applies
    to every statement when given a ``{name: DataFrame}`` dict (as returned by
    :func:`to_dataframe` for the multi-statement shape). Non-numeric cells
    (labels, missing values) pass through unchanged.
    """
    if isinstance(df_or_dict, dict):
        return {name: _scale_df(df, scale, decimals) for name, df in df_or_dict.items()}
    return _scale_df(df_or_dict, scale, decimals)


def _df_to_markdown(df) -> str:
    """Render one DataFrame as a GitHub-flavored markdown table.

    Falls back to a hand-rolled table if ``tabulate`` (pandas' markdown
    backend) isn't installed.
    """
    try:
        return df.to_markdown()
    except ImportError:
        header = [str(df.index.name or "")] + [str(c) for c in df.columns]
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * len(header)) + " |",
        ]
        for idx, row in df.iterrows():
            lines.append("| " + " | ".join([str(idx)] + [str(v) for v in row.tolist()]) + " |")
        return "\n".join(lines)


def to_markdown(df_or_dict) -> str:
    """Render a DataFrame, or a ``{name: DataFrame}`` dict, as markdown.

    Multi-statement results (see :func:`to_dataframe`) get one ``### name``
    heading per statement, in insertion order.
    """
    if isinstance(df_or_dict, dict):
        sections = []
        for name, df in df_or_dict.items():
            sections.append(f"### {name}\n\n{_df_to_markdown(df)}")
        return "\n\n".join(sections)
    return _df_to_markdown(df_or_dict)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ticker")
    parser.add_argument("--period", default="quarter", choices=PERIODS)
    parser.add_argument("-o", "--out", help="write JSON here instead of stdout")
    parser.add_argument(
        "-m", "--markdown", action="store_true",
        help="convert the response to a DataFrame and print it as a markdown table",
    )
    parser.add_argument(
        "-n", "--n-periods", type=int, default=None, metavar="N",
        help=(
            "with --markdown, keep only the N most recent periods per statement "
            "(negative N keeps the N oldest instead — use if periods turn out to "
            "be ordered newest-to-oldest)"
        ),
    )
    parser.add_argument(
        "-s", "--scale", type=float, default=1e12, metavar="FACTOR",
        help=(
            "with --markdown, divide numeric values by this before rounding "
            "(default 1e12 = raw VND -> nghìn tỷ đồng, e.g. 3.15481e+13 -> 31.548); "
            "use 1 to print raw values"
        ),
    )
    parser.add_argument(
        "--decimals", type=int, default=3, metavar="N",
        help="rounding precision after scaling (default 3)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        data = fetch_financial_statements(args.ticker, args.period)
    except DeviceLimitError as exc:
        print(f"error: {exc}\nActive devices (revoke one, or set RUATICHSAN_TOKEN):")
        for dev in exc.devices:
            print(
                f"  id={dev.get('id')}  {dev.get('device_name')}  "
                f"ip={dev.get('ip_address')}  last_active={dev.get('last_active_at')}"
            )
        return 2
    except RuatichsanError as exc:
        print(f"error: {exc}")
        return 1

    if args.markdown:
        try:
            df = to_dataframe(data, n_periods=args.n_periods)
        except RuatichsanError as exc:
            print(f"error: {exc}")
            return 1
        if args.scale != 1:
            df = format_scaled(df, scale=args.scale, decimals=args.decimals)
        text = to_markdown(df)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
