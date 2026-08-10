# Better Harness Task-Loop Report

## At a Glance

- Loop Effectiveness: 35/100 (changes only after comparable later task outcomes)
- Asset Health / Repair Progress: 0/100 (0 verified, 0 partial, 11 pending)
- Demonstrated autonomy radius: not observed (not observed; not observed confidence)
- Strongest loop: Not enough evidence difference to name one.
- Largest observed leak: Use the priority moves; no single loop is uniquely weakest.
- Top expected gain: No priority benefit is available in this evidence boundary.

## What You Can Rely On Today

- No reliable user outcome has been demonstrated in this evidence boundary yet.

## What You Gain Next

- No priority Harness move is available in this evidence boundary.



### Why these moves matter

### Working DNSE trading-API credentials sit in a git-visible file with nothing between them and a push
- Priority: High · Evidence: not observed in this boundary
- Reason: Fact: `testing/test_dnse_api.py` hardcodes an API key and secret whose values match the `DNSE_API_KEY` / `DNSE_API_SECRET` entries in `.env` and `prod.env`; `git check-ignore` exits non-zero for that path, `testing/` already tracks nine files, and no commit-time gate exists (no non-sample hook in `.git/hooks`, no `.github` workflow). The env files themselves are correctly ignored — the exposure is this one script. Inference: a single `git add -A && git commit && git push` would place live credentials in remote history permanently. Owner: `.gitignore` plus the script itself, which should read from the environment. Uncertainty: whether the credentials are still valid upstream was not tested, and no evidence indicates they have already been committed. Provider scope: repository state, verified statically; nothing was executed.
- Expected Output:
  1. Give the next developer a repository where the live trading credentials exist only in ignored env files, and a commit-time gate that refuses a staged credential literal.

### No command can refute a change, and the obvious one calls a live trading API with no assertions
- Priority: High · Evidence: not observed in this boundary
- Reason: Fact: the `Makefile` exposes only docker lifecycle targets (build/up/down/logs/clean/prod-*) with no test, lint, or typecheck target; `frontend/package.json` declares `dev`, `build`, `lint`, `preview` and no test runner in devDependencies; `backend/pytest.ini` sets only `asyncio_mode=auto` with no `testpaths`, and no root pytest/pyproject config fences collection. `testing/test_dnse_api.py` performs its signed HTTP request at module import and only prints, so it contains no assertion. The project permission list preserves the residue of hand-rolled checks (`npx tsc`, an absolute-path `tsc --noEmit`, `vite build`, timed `curl` against a local endpoint), which is where the verification procedure currently lives. Inference: `pytest` from the repo root is the discoverable default and would execute that live call while always reporting success. Owner: `Makefile` for one `verify` target, `backend/pytest.ini` for `testpaths = tests`. Uncertainty: exact root-`pytest` collection behavior was not executed, per the read-only boundary.
- Expected Output:
  1. Give an agent finishing a change one named command that can fail, and that never touches a live external API.

### The quote and indicator functions changed in this work are pure, untested, and can regress prices silently
- Priority: High · Evidence: not observed in this boundary
- Reason: Fact: the current change set adds four pure decision points — `_pick_trade` (board priority G1/G7/G4, put-through boards discarded) and `_to_quote` (a reported 0 mapped to "not reported") in `backend/app/services/dnse_client.py`, `isVnMarketSession` (hardcoded GMT+7, inclusive 15:00 bound) in `frontend/src/lib/services/quote.ts`, and in-browser indicator math moved into `frontend/src/lib/tv/studies.ts` — while `backend/tests/` contains only a health check and the front end has no test runner at all (643 source files against 8 test files; 0 test files for JavaScript/TypeScript). `frontend/src/components/chart/StockChart.tsx` is the repository's top churn hotspot and is changed again here. Inference: a wrong board selection, a real 0 treated as a price, or an off-by-one session bound would change displayed prices and indicator values with no failing check anywhere. Uncertainty: no wrong value was observed; the confirmed defect is the absence of a verifier, not a known bug. Owner: `backend/tests/` for the two Python functions, `frontend/package.json` for the missing runner.
- Expected Output:
  1. Give the next change to price selection or session bounds a check that fails when the numbers are wrong.

### The agent permission surface grants shell wildcards over live credentials and the production deploy path with no deny rule and no gate
- Priority: High · Evidence: not observed in this boundary
- Reason: Fact: the project permission file contains `permissions.allow` only — 51 entries, no `deny` block — and the asset baseline reports `enabledHookCount: 0`, so no PreToolUse gate exists as a second layer. Among the pre-approved patterns are `Bash(python3:*)` and `Bash(python3 -)` (arbitrary code whose payload never appears in a permission decision), `Bash(xargs cat)` while `.env` and `prod.env` sit unencrypted in the tree, and `Bash(docker compose *)` while `docker-compose.prod.yml` and the Makefile's `prod-up` / `prod-down` targets are present. Inference: a secret read or a production mutation can occur with no prompt and no audit record. Uncertainty: whether any such action actually happened is unobserved — runtime use was outside the reviewed authority, and both permission files are machine-local and git-ignored, so no reviewer has ever seen this surface. Owner: a git-tracked `.claude/settings.json` carrying `permissions.deny`.
- Expected Output:
  1. Give the repository one reviewable file that states which actions an agent may never take here, instead of an invisible per-machine allow list.

### When the live price stops updating, nothing distinguishes missing credentials from a filtered board or an empty upstream
- Priority: Medium · Evidence: not observed in this boundary
- Reason: Fact: in `backend/app/services/dnse_client.py` the 503 unconfigured-credential exit and the 404 `No recent trade` exit both return without emitting a log line, so `_pick_trade` discarding every put-through board is indistinguishable from an empty upstream payload; `backend/app/main.py` middleware logs only method, path, status and duration, and no request id is generated or propagated; no `logger.add` or `logger.configure` exists in `backend/app` or `worker`, so loguru writes to default stderr reachable only through `make backend-logs`, which no document names as the debug route. On the client, the poll in `frontend/src/lib/tv/datafeed.ts` ends in a bare `catch` commented "transient", which swallows a permanent 503 and retries silently every interval. Inference: the failure narrative above is read from source, not observed — no request was run. Owner: `dnse_client.py` for the two silent exits and the discarded-board reason; `main.py` middleware for the missing correlation id.
- Expected Output:
  1. Give whoever debugs a frozen live price one log line that names which of the three causes occurred.

### The repository's only entry document describes a different system than the code
- Priority: Medium · Evidence: not observed in this boundary
- Reason: Fact: `README.md` documents a PostgreSQL financial-schema tool with psql and import steps and mentions no test, lint, verify, npm, or make route, while `backend/requirements.txt` declares FastAPI and no Django dependency exists anywhere in the tree. The static profiler inherited that framing: it named the project after the README heading, detected `django` at medium confidence, and ranked `frontend/src/lib/tv/index.ts` above `backend/app/main.py` as the entry candidate. No scoped agent instruction file exists to correct it (no `CLAUDE.md`, no `AGENTS.md` at any depth inspected). Inference: an agent orienting from the repository's own documents is routed to the wrong stack; that the mismatch actually misdirected specific work was not established from session evidence in this lane. Owner: `README.md`.
- Expected Output:
  1. Give a newcomer or an agent a first document that names the real stack, the real entry points, and the command that checks a change.

### Edits inside the vendored trading-agents package never appear in a repository diff, and a fresh clone cannot initialize it
- Priority: Medium · Evidence: not observed in this boundary
- Reason: Fact: `git ls-files -s vendor` reports mode 160000 for `vendor/TradingAgents` — a gitlink — while `.gitmodules` does not exist, and the package is loaded into the backend through a `sys.path` shim. No instruction asset claims the vendored boundary. Inference: a change to code that computes trading signals shows in the super-repo only as a dirty submodule pointer, so it can ship unreviewed; and a fresh clone cannot reproduce the directory at all, so agent-run verification there diverges from the developer machine. Uncertainty: whether an agent has in fact modified `vendor/` is unobserved.
- Expected Output:
  1. Make every change to third-party trading logic either visible in a diff or impossible.

### The vendored integration boundary is re-supplied by hand each time instead of living in the project
- Priority: Medium · Evidence: not observed in this boundary
- Reason: Fact: in two Task Episodes from independent context groups in the reviewed 30-day window, the user's own prompt carried the architecture briefing the agent needed — the backend stack, that the trading-agents package is vendored and resolved through a `sys.path` shim, where to look for the runner, and a demand for exact function signatures and return shapes — and each episode still spent its trace re-reading source (34 and 9 classified reads). One ended with a change and no check; the other ended with no change. Project-scope agent assets are zero and no `CLAUDE.md` or `AGENTS.md` exists, so there is no durable owner for that boundary. Inference: the demand is knowledge-shaped, not procedure-shaped. No Skill is proposed: the two episodes share no ordered procedure, verifier, or stop rule, so procedure demand is unsupported and the correct first owner is project documentation. Uncertainty: two episodes is the minimum threshold, prompt text was redacted to summaries, and whether the two targeted the same module could not be confirmed. Provider scope: `claude` session evidence only.
- Expected Output:
  1. Stop the user from re-typing the same architecture briefing before work on the vendored package can start.

### Work reaches "done" with no acceptance boundary — no review, no required check, no recorded decision
- Priority: Medium · Evidence: not observed in this boundary
- Reason: Fact: no `.github`, `.gitlab-ci.yml`, or `.circleci` directory exists, `.git/hooks` holds only samples, and no merge or review gate was found; in the reviewed window the one change-bearing Task Episode closed as changed-without-check and ended at an assistant handoff, and no episode contains a check bound to its own change. The two checks that do appear are passing type checks in episodes that changed nothing. Inference: the only signal that work is complete is the agent saying so, which cannot be distinguished from unfinished or wrong work. Uncertainty: the reviewed portfolio is truncated and its population contains 75 change-bearing episodes that were not individually reviewed, so the per-episode fact must not be scaled to all of them; provider scope is `claude` only. Owner: a required check trigger bound to the change, once a verify command exists.
- Expected Output:
  1. Give a finished change one recorded decision that is not the agent's own statement that it is done.

### Two directory-scoped permission files disagree, and one carries a malformed path pattern
- Priority: Low · Evidence: not observed in this boundary
- Reason: Fact: `.claude/settings.local.json` (51 allow entries) and `worker/.claude/settings.local.json` (9 entries) diverge — the root allows `Bash(python3:*)` while the nested file instead allows `Bash(python *)`, `Bash(python3 -c ' *)` and `Bash(python3 -)` — and the nested file re-grants workspace-wide read access from a subdirectory scope using a pattern with a doubled leading slash. Inference: that malformed pattern either over-grants the whole workspace from a nested scope or never matches; both are wrong and neither is visible to anyone, because both files are git-ignored. Uncertainty: effective precedence and whether the malformed pattern matches were not executed. Owner: consolidate into the single tracked `.claude/settings.json`.
- Expected Output:
  1. Make one command mean one permission decision, wherever the agent happens to start.

### Browser-automation scratch output is committed and inflates the change set a reviewer must read
- Priority: Low · Evidence: not observed in this boundary
- Reason: Fact: `.playwright-mcp/` is not ignored; console log and page snapshot files from earlier sessions are already tracked, and four more untracked ones from the current session account for 4 of the 15 files in the current change set. They are the sole trigger for a documentation-drift signal in the change analysis, which asked for README updates about a Playwright dump. Inference: agent tool output accumulates in history and distorts both human review and the automated change signal. Owner: `.gitignore`, plus removal of the already-tracked files.
- Expected Output:
  1. Keep agent scratch output out of commits so a reviewer only reads real changes.

## Five Lifecycle Dimensions

| Dimension | What the evidence proves | Evidence boundary | Summary | Boundary / blocker |
| --- | --- | --- | --- | --- |
| Task Understanding | Not observed yet | not observed in this boundary | The one entry document describes a different system than the code, and no scoped instruction corrects it; two change surfaces (a vendored gitlink and agent scratch output) sit outside the reviewable boundary. | not observed |
| Controlled Execution | Not observed yet | not observed in this boundary | Container startup and service routes are declared and reachable, but no supported verify route exists, and the permission surface is an append-only allow list with no deny rules and no gate over live credentials and a production deploy path. | not observed |
| Change Validation | Not observed yet | not observed in this boundary | Type checking is genuinely exercised, but nothing covers behavior: the changed quote and indicator functions are pure, untested, and the reviewed window contains no check bound to a change. | not observed |
| Reliable Delivery | Not observed yet | not observed in this boundary | No review, CI, or merge boundary exists to accept a change, the reviewed change-bearing episode closed at an assistant handoff, and no rollback is bound to the new externally dependent path. | not observed |
| Learning Capture | Not observed yet | not observed in this boundary | A bounded review completed and found one supported knowledge demand — the vendored trading-agents integration boundary, re-supplied by hand in two independent contexts — with no durable owner to route it to. | not observed |

## The 15 Small Checks

| Dimension | Small check | What the evidence proves | Evidence boundary |
| --- | --- | --- | --- |


## Evidence and Boundaries

- Episode coverage: 0 episodes, 0 edited, 0 closed, 0 repaired-and-passed
- Model: agent-work-loop-v4
- Session selection: not observed; 0 sessions analyzed of 0 eligible sessions; not observed confidence
- Delivery grades observed: not observed
- Source gaps: not observed
- Learning comparison: Not observed; 0 declared intervention(s)
