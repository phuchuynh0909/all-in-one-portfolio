# Simple authentication

**Status:** approved, not yet implemented
**Date:** 2026-09-01

## Problem

Every route on the backend is open. `main.py` wires 25+ routers behind no
authentication whatsoever, and the deployment at `phuchuynh.site` /
`api.phuchuynh.site` is reachable by anyone. A stranger can read the portfolio,
create and delete positions and transactions, arm price alerts, and trigger
`POST /trading-agents/run`, which spends real LLM budget.

`backend/app/api/v1/routes/auth.py` exists but is **not** application
authentication — it is a proxy that refreshes an MBS broker token on behalf of
the Chat Agents page. The name is misleading and is addressed below.

## Goal

A small number of known users log in with a username and password before they
can reach anything. No self-registration, no per-user data scoping — every
authenticated user sees the same single portfolio, exactly as today.

## Non-goals

- Self-service registration, email verification, password reset
- Per-user data isolation (no `user_id` columns on `positions`, `transactions`,
  `price_alerts`, …)
- Roles or permissions — every authenticated user is equal
- Refresh-token rotation and short-lived access tokens
- Login rate limiting. Bcrypt's cost factor makes online guessing slow and the
  user set is a handful of known people. Explicitly deferred, not overlooked.
- OAuth / social sign-in

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Auth model | A few known users in a `users` table, seeded by CLI | Per-user tokens allow revoking one person without rotating everyone; no registration surface |
| Coverage | Deny by default, app-wide | A new router is protected the moment it is added; there is no step to forget |
| Token transport | JWT in `localStorage`, `Authorization: Bearer` | Prod is cross-site (`phuchuynh.site` → `api.phuchuynh.site`); cookies would need `SameSite=None; Secure` and exact CORS origins in place of the current wildcard. A header behaves identically in dev and prod. |
| Token lifetime | 30 days, no refresh token | Re-login is rare; no refresh plumbing to build |
| Password hashing | `bcrypt` directly, not `passlib` | `passlib` is effectively unmaintained and its bcrypt-4.x backend detection is a known source of spurious warnings and breakage |
| JWT library | `PyJWT`, HS256 | Small and maintained |
| Guard mechanism | A FastAPI dependency registered app-wide | Yields proper 401 JSON, injects the current user into any handler, appears in OpenAPI, and is neutralised in tests with one `dependency_overrides` line. ASGI middleware can do none of those. |
| Security scheme | `HTTPBearer` | Gives Swagger an Authorize box that accepts a pasted token. `OAuth2PasswordRequestForm`'s Authorize flow posts form-encoded data and would fail against a JSON login endpoint. |
| Provisioning | `python -m app.scripts.create_user <username>`, password prompted | No secrets in git, none in argv or shell history |
| Frontend token attachment | A scoped `window.fetch` interceptor | See "The 76 call sites" below |

### Rejected alternatives

- **`fastapi-users`** — built around async SQLAlchemy sessions; this project is
  entirely sync (`SessionLocal`, `get_db` yielding a sync `Session`). Adopting
  it means a second async engine or fighting the library, and it ships the
  registration/verification/reset flows listed under non-goals.
- **Pure ASGI middleware guard** — cannot inject the current user into
  handlers, contributes nothing to OpenAPI, and returns hand-built
  `JSONResponse` errors that bypass the app's exception handling.
- **Shared single password** — no way to revoke one person.
- **HttpOnly cookie session** — the cross-site prod split forces
  `SameSite=None; Secure`, `credentials: 'include'` on every fetch, and
  replacing `allow_origins=["*"]` with an exact list. More to get wrong for a
  private app.

## Backend

### New files

| File | Contents |
| --- | --- |
| `app/db/models/user.py` | `User`: `id` PK, `username` String(64) unique indexed, `password_hash` String(255), `is_active` Boolean default True, `created_at` TIMESTAMP server default |
| `alembic/versions/<rev>_add_users_table.py` | Creates `users`. `down_revision` chains off the current head (confirm with `alembic heads` at implementation time; `d5a91c3e7b20_corporate_actions` as of writing) |
| `app/core/security.py` | Pure functions, no FastAPI import: `hash_password`, `verify_password`, `create_access_token`, `decode_access_token` |
| `app/api/deps.py` | `require_user` dependency and `EXEMPT_PATHS` |
| `app/schemas/auth.py` | Pydantic v2: `LoginRequest`, `TokenResponse`, `UserOut` |
| `app/scripts/create_user.py` | Provisioning CLI. Lives under `app/` **because the Docker image does not copy `backend/scripts/`** — it copies only `app`, `tasks`, `alembic`, `alembic.ini`, `libs`. Placing it here needs no Dockerfile change. |

### Changed files

- `app/api/v1/routes/auth.py` — becomes real authentication: `POST /auth/login`,
  `GET /auth/me`.
- `app/api/v1/routes/broker.py` (moved) — the existing MBS refresh-token proxy,
  relocated from `auth.py` to prefix `/broker`. Rationale: otherwise
  `/auth/refresh-token` sits beside `/auth/login` carrying its own unrelated
  `access_token` / `refresh_token` vocabulary, and a reader will reasonably
  assume it refreshes the app session. The endpoint's body is unchanged.
- `app/main.py` — `FastAPI(..., dependencies=[Depends(require_user)])`; register
  the broker router; import `User` in the `db/base.py` model block so SQLAlchemy
  and alembic see it.
- `app/core/settings.py` — `auth_secret_key: str`, `auth_token_ttl_days: int = 30`.
- `requirements.txt` — add `PyJWT`, `bcrypt`, then regenerate the lock.

### The guard

`require_user` short-circuits when `request.url.path` is in `EXEMPT_PATHS`,
otherwise reads the `HTTPBearer` credential, decodes the JWT, loads the user,
and returns it. Handlers that want the caller take
`user: User = Depends(require_user)`.

Exempt: `/api/v1/health`, `/api/v1/auth/login`.

`/docs` and `/openapi.json` remain reachable without a token because FastAPI
registers them as Starlette routes, so app-level dependencies do not apply.
A test asserts this rather than trusting it.

401 on: missing header, malformed header, bad signature, expired token, a
subject with no matching row, `is_active=False`.

`/auth/refresh-token` → `/broker/refresh-token` becomes protected, which is
correct — only a logged-in user should proxy broker calls. It needs no client
change beyond its new path because it already goes through `apiPost`.

### Secret key

`APP_AUTH_SECRET_KEY` is read from the environment.

- Unset and `environment == "production"` → refuse to start.
- Unset otherwise → generate a random ephemeral key and emit a loud
  `logger.warning`. Tokens then die on restart, which is the correct signal.

No committed default. A hardcoded fallback secret is how tokens become
forgeable.

## Frontend

### The 76 call sites

`apiGet` / `apiPost` in `src/lib/api.ts` are **not** the only path to the API.
Roughly 76 raw `fetch()` calls across 18 files bypass them, including all of
`lib/services/timeseries.ts`, `quote.ts`, `chat.ts`, `tradingAgents.ts`,
`mvf.ts`, `regime.ts`, `backtest.ts`, `future.ts`, `report.ts`,
`priceAlerts.ts`, the four portfolio CRUD components, `pages/Home.tsx`, and
`pages/Backtest.tsx`. Some are streaming reads (`/portfolio/mvf/stream`, chat).
Attaching the header only inside `api.ts` would leave most of the app 401ing.

There are no `EventSource` or `WebSocket` consumers, which matters because
neither can carry a custom header.

**Chosen: a scoped `window.fetch` interceptor** in `src/lib/auth/authFetch.ts`,
installed once from `main.tsx`. It attaches `Authorization` **only** when the
request URL resolves to the API origin or begins with `/api/`, and leaves every
other request untouched. One auditable file, no possibility of a missed call
site now or later, trivially removable, and it transparently covers
`SectorPerformanceChart.tsx:97`, which uses a relative `/api/v1/...` path that
only resolves behind nginx.

The origin check is the load-bearing part: it must not attach the token to
third-party requests such as the TradingView CDN. It is tested by asserting a
non-API URL comes back without the header.

The rejected alternative was mechanically rewriting `fetch(` → `authFetch(` in
19 files — explicit, but a wide diff and it can silently miss a call site.

### New files

| File | Contents |
| --- | --- |
| `src/lib/auth/token.ts` | `getToken` / `setToken` / `clearToken` over `localStorage['auth_token']` |
| `src/lib/auth/authFetch.ts` | The interceptor: attaches the header **and** detects 401s |
| `src/lib/services/auth.ts` | `login(username, password)`, `fetchMe()` |
| `src/components/auth/AuthProvider.tsx` | Context `{ user, status: 'loading' \| 'authed' \| 'anon', login, logout }`. On mount, if a token exists it calls `GET /auth/me` to validate — this catches both an expired token and a deactivated user. Subscribes to `auth:unauthorized`. |
| `src/components/auth/RequireAuth.tsx` | `loading` → spinner; `anon` → `<Navigate to="/login" replace state={{ from }} />` |
| `src/pages/Login.tsx` | Centered MUI card, username + password, inline error text |

### Changed files

- `App.tsx` — wrap in `AuthProvider`; restructure so `/login` renders outside
  `AppShell` (no nav chrome on the login screen) and everything else nests
  under `RequireAuth`:

  ```tsx
  <Routes>
    <Route path="/login" element={<Login />} />
    <Route path="*" element={<RequireAuth><ShellRoutes /></RequireAuth>} />
  </Routes>
  ```

- `AppShell.tsx` — a `UserMenu` (username, Logout) after `<ModeToggle />` in the
  toolbar (line 186).
- `src/lib/api.ts` — unchanged. Deliberately: it is not the choke point.
- `src/lib/services/chat.ts:69` — `/auth/refresh-token` → `/broker/refresh-token`.
- `main.tsx` — install the interceptor before the first render.

### Error flow

On any 401 from an API URL the **interceptor** clears the stored token and
dispatches `auth:unauthorized`. This has to live in the interceptor rather than
in `api.ts` for the same reason the header does: `apiGet` / `apiPost` see only a
fraction of API traffic, so a 401 handler there would never fire for the ~76 raw
`fetch()` call sites, and an expired token would surface as scattered error
cards instead of a redirect. `AuthProvider` flips to `anon`; `RequireAuth` redirects to
`/login`. This deliberately keeps expiry out of `ErrorBoundary` and away from
TanStack Query's `retry: 1`, which would otherwise retry a doomed request and
surface a raw error card instead of a login screen.

Logout calls `queryClient.clear()` so one user's cached portfolio cannot flash
into another's session.

## Testing

### Keeping the existing suite green

Six test files construct `TestClient(app)` (`test_health.py`,
`test_corporate_action_routes.py`, `test_tradingagents_route.py`,
`test_block_episodes.py`, `test_optimization_service.py`,
`test_tradingagents_shutdown_hook.py`). A global guard 401s all of them.

One autouse fixture in `tests/conftest.py` fixes every one without editing any
of the six:

```python
@pytest.fixture(autouse=True)
def _authenticated(request):
    """Run as a logged-in user unless the test opts out with @pytest.mark.real_auth."""
    if "real_auth" in request.keywords:
        yield
        return
    app.dependency_overrides[require_user] = lambda: User(
        id=1, username="test", is_active=True
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_user, None)
```

`real_auth` is registered in `pytest.ini` so `--strict-markers` stays happy.

### New `tests/test_auth.py`

Written before the implementation.

**No DB, always runs:**
- `hash_password` / `verify_password` roundtrip; wrong password rejected
- two hashes of the same password differ (salting)
- token roundtrip returns the subject
- expired token rejected
- tampered payload rejected
- token signed with a different key rejected
- garbage input raises rather than returning a truthy user

**Routes** — following the established `test_corporate_action_routes.py`
pattern: `app.dependency_overrides[get_db]` bound to the rolled-back `db`
fixture and gated on `requires_mysql`, so the seeded test user is discarded and
no real row is touched. Marked `real_auth`.
- `POST /auth/login` → 200 with a token for valid credentials
- 401 for wrong password, unknown username, and `is_active=False`
- a protected route → 401 with no header, 401 with a malformed header, non-401
  with a valid token
- `GET /api/v1/health` → 200 with no token
- `/docs` → 200 with no token (proves the Starlette-route carve-out holds)
- `GET /auth/me` → the username

### Verify commands

- `cd backend && pytest tests` — never bare `pytest` from the repo root, which
  collects `testing/test_dnse_api.py` and fires a live signed DNSE request at
  import time
- `cd frontend && npm run build` (tsc + vite)
- `cd frontend && npm run lint` (`--max-warnings 0`)

This project has no frontend test runner (`package.json` defines only `dev`,
`build`, `lint`), so the interceptor's origin check has **no automated test**.
It is the riskiest line in the frontend change — if it matches too broadly the
token is sent to third parties such as the TradingView CDN. It is verified
manually: log in, open the Network panel, and confirm that requests to the API
origin carry `Authorization` while the TradingView CDN and
`EXPERIMENTS_BASE_URL` requests do not. Standing up vitest for a single unit
test is out of scope here, and is called out as a gap rather than papered
over.

## Rollout

Steps 1–4 are the implementer's. Step 5 is the owner's.

1. Add `APP_AUTH_SECRET_KEY` to `.env` (generated; never printed, and the rest
   of the file is not read or echoed)
2. Add `PyJWT` and `bcrypt` to `backend/requirements.txt`, then
   `make lock-backend` — the image installs the lock, so editing
   `requirements.txt` alone changes nothing
3. Rebuild the backend image, then `alembic upgrade head` inside the container
   to create `users`. Alembic is not wired into the Makefile or compose;
   migrations are run by hand.
4. `docker compose exec backend python -m app.scripts.create_user phuc` —
   password prompted
5. **Production, owner only.** `prod.env` needs its own, *different*
   `APP_AUTH_SECRET_KEY`; the migration and user creation must be repeated
   there. No `make prod-*` command is run as part of this work.

## Known loose end, not addressed

`settings.backend_cors_origins` ends with `"*"` while `allow_credentials=True`
— a combination browsers reject for credentialed requests. It is harmless here
because authentication travels in a header rather than a cookie, so this design
leaves it alone. Replacing the wildcard with the explicit origin list already
present above it is sensible hardening, but it is a separate change.
