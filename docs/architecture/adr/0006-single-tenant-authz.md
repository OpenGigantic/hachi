# ADR-0006 — Single-tenant authorization: delete the per-session ownership layer

- **Status:** Accepted
- **Date:** 2026-07-21
- **Deciders:** Product owner (via `docs/audit/2026-07-19/tasks/T-61-single-tenant-adr.md`)
- **Related:** [T-61](../../audit/2026-07-19/tasks/T-61-single-tenant-adr.md) (this task);
  [T-62](../../audit/2026-07-19/tasks/T-62-session-messages-ownership-check.md) (superseded by
  this ADR — see Consequences); CF-15 in
  [`ARCHITECTURE-REVIEW.md`](../../audit/2026-07-19/ARCHITECTURE-REVIEW.md); `docs/DECISIONS.md`
  entries for T-31/T-32 (introduced the OpenAI-facade half of the ownership machinery this ADR
  removes); `CLAUDE.md` ("Multi-tenant support (single-tenant is load-bearing) — flag for a
  decision.")

## Context

The gateway carried a per-session ownership system, referred to in the code as "C1": sha256
digests of the bearer token, an in-memory owner map on `GatewayControlPlane._connections`, 403
branches on nine of eleven session routes plus the runs and interactions routers, and inline
owner-filters on the three list endpoints (`GET /sessions`, `GET /runs`, `GET /interactions`).

It could not discriminate between callers, because **exactly one bearer token exists per
process** (`create_gateway_app(control_plane, auth_token, ...)` takes a single token; there is
exactly one comparison against it, in `_require_auth`/`require_auth`). Every authenticated
caller therefore hashed to the same digest, so every ownership comparison
(`secrets.compare_digest(owner, caller_hash)`) compared a value to itself. The control carried
real ceremony — a docstring, a 403 path, a filtered list — while enforcing nothing, because there
was never a second principal for it to discriminate against.

It was also incomplete in ways that would matter the day a second principal *did* appear:

1. **The owner map the gateway actually enforced against was in-memory only**
   (`GatewayControlPlane._connections[session_id]["owner_token_hash"]`) — nothing in the
   enforcement path persisted it. A *second*, entirely orphaned attempt at persistence existed
   in `core/session_store.py` (`SessionStore.set_session_owner`/`.get_session_owner`, an
   `owner_token_hash` column added via migration) — unreferenced by any caller anywhere in the
   codebase since at least 2026-06-20 (`git show bd24afe:src/hachi/core/session_store.py`
   confirms it predates even this task's own cited baseline commit). The task spec's own §3.2/§4
   claimed `grep -rn "owner" src/hachi/core/session_store.py` "returns nothing today" — that was
   wrong, not merely stale; re-verify repro steps with a live grep, not by trusting the doc.
   Whatever earlier work added this column never wired it to `GatewayControlPlane`, so the
   gateway's ownership enforcement was in-memory-only exactly as this ADR describes regardless —
   but the orphaned copy is exactly the kind of misleading artifact this ADR exists to eliminate,
   so it is removed alongside the machinery the task named explicitly (see Decision).
2. **`set_session_owner` was a silent no-op** when the session id was not already a key in
   `_connections` — no raise, no log, no return value the caller could inspect.
3. **A missing owner meant *allow*, by documented policy** (`get_session_owner` returning `None`
   was treated as "legacy / no-auth session, permit access"). Combined with (1) and (2): after
   any process restart, `_connections` is empty, so every persisted session is ownerless, so
   every session is open to any authenticated caller. The system's steady state across a process
   lifetime was "off."
4. **Enforcement was inconsistent even within one process.** `GET /sessions/{id}/messages` and
   `POST /sessions/cleanup-empty` performed no ownership check at all, for a structural reason
   (neither handler took a `Request` parameter, so neither could obtain the caller digest
   `_check_session_ownership` needs) — not a deliberate exemption. [T-62] had already fixed this
   specific inconsistency in Wave 1, immediately before this ADR was written.
5. **Token rotation orphans every owner digest**, since the owner *is* `sha256(bearer)` rather
   than an identity independent of the credential — masked in practice only because (3) already
   makes every session ownerless after the restart rotation implies.

This is a security control in the worst possible state: enough ceremony that a reader
reasonably concludes Hachi enforces per-caller session isolation, and no capability to actually
do so. `CLAUDE.md` already states the product's stance explicitly: *"Multi-tenant support
(single-tenant is load-bearing) — flag for a decision."* A half-built multi-principal system is
not a defensible middle ground between "single-tenant" and "multi-tenant" — it is neither.

## Decision

**Delete the C1 ownership machinery. Hachi is honestly single-principal: the bearer token IS the
identity, and possession of a valid token is full authority over every session, run, and
interaction the process manages.** Authentication (`require_auth`, the `secrets.compare_digest`
bearer check, the `HACHI_GATEWAY_NO_AUTH` loopback-only opt-out, and the CRIT-001/E8/T8.2 startup
guards) is unchanged — this ADR is about the authorization layer built *on top of* authentication,
not authentication itself.

Removed, across `src/hachi/gateway/{deps,control_plane}.py`,
`src/hachi/gateway/routers/{sessions,runs,interactions,channels,openai_compat}.py`, and (the
orphaned second copy — see Context) `src/hachi/core/session_store.py`:

- `GatewayControlPlane.set_session_owner` / `.get_session_owner`, and the `owner_token_hash` key
  on the `_connections` dict.
- `SessionStore.set_session_owner` / `.get_session_owner` and the `owner_token_hash` column
  migration entry in `core/session_store.py` — the orphaned, never-called second copy from
  Context point 1. Safe to delete outright (no `SELECT *` on `sessions` anywhere serializes it
  into a response; every query names its columns explicitly).
- `deps.check_session_ownership` (the shared "C1" check, formerly `fastapi_app.
  _check_session_ownership`) and its `routers/runs.py` wrapper `_check_run_ownership`.
- Every call site of the above: the 403 branches on `GET /sessions/{id}`,
  `POST /sessions/{id}/messages`, `DELETE /sessions/{id}`, `POST /sessions/{id}/interrupt`,
  `POST /sessions/{id}/permissions/always-allow`, `GET /sessions/{id}/events`,
  `GET /sessions/{id}/stream`, `GET/POST/DELETE /runs/*`, and `GET /interactions` +
  `PUT /interactions/{id}/rating`.
- **`GET /sessions/{id}/messages`'s ownership check specifically** — the one T-62 (Wave 1) had
  just added. Deleting the machinery means that check goes too; T-62's file records this
  supersession rather than silently vanishing from history.
- The owner-stamping on session creation (`POST /sessions`, and the OpenAI-compat / channel-
  inbound facades via `GatewayControlPlane.get_or_create_session`/`complete_chat`'s
  `owner_token_hash` parameter).
- The owner-filters on the three list endpoints (`GET /sessions`, `GET /runs`,
  `GET /interactions`) — they now return every session/run/interaction the store holds, exactly
  as they already did for legacy (ownerless) rows before this change.
- **The digest return value from `require_auth`** (`deps.py`; `fastapi_app.py`'s `_require_auth`
  before T-47 moved it). It existed for exactly one purpose — "so routes can compare ownership
  without storing or comparing the raw bearer value" (its own docstring) — and that purpose no
  longer exists. `require_auth` now returns `bool`: `True` when a token is configured and the
  request's credential was validated against it, `False` in the two pre-existing no-enforcement
  modes (`HACHI_GATEWAY_NO_AUTH=1`; no token configured at all, the unit-test/OpenAPI-export
  path). It still raises on a missing/invalid credential exactly as before — that is
  authentication, untouched.
- The `CallerHash` dependency-alias type (`Annotated[str | None, Depends(require_auth)]`),
  renamed `Authenticated` (`Annotated[bool, Depends(require_auth)]`) to match the new, honest
  contract — a type still named "caller hash" after the hash is gone would itself be the kind of
  misleading artifact this decision exists to remove.

Two non-ownership consumers of the old digest survive, adapted to the boolean contract rather
than deleted, because they solve problems that are not per-session ownership: the OpenAI-compat
facade's per-request rate-limit key (BB-58) and its facade session-key namespacing (T-32) both
used to fold in the caller digest. Both now use a fixed `"authenticated"` / `"anonymous"` (or
`"anon"`) marker instead. This does not change their observable behaviour: under one token, the
digest was already the same value for every request in a given auth mode, so both were already
single shared buckets/namespaces per mode, not per-principal ones — the same CF-15 observation
applied to a different feature. Neither test suite's assertions relied on the exact digest bytes,
only on "not the old hardcoded literal" / "does not collide across modes," both of which still
hold.

## Alternatives considered

1. **(b) Finish it — make ownership real.** Requires all four of: a persisted owner column on
   `core/session_store.py` **wired to the enforcement path** (a column already existed there,
   per Context point 1, but nothing ever read it back into `GatewayControlPlane` — "persisted"
   and "persisted *and connected to the check*" are different bars, and only the second one
   counts); a principal id that is *not* `sha256(bearer)`, so token rotation does not orphan
   every session (§Context point 5) — which implies a token table and token issuance/management,
   a product surface of its own; uniform enforcement via a
   dependency rather than a per-handler call, so "forgot to check" becomes structurally
   impossible; and a fail-closed default (`None` owner denies, not allows), which requires a
   migration for every existing ownerless row. Rejected for now: this is multi-tenancy's front
   door — per-principal isolation within one tenant — which `CLAUDE.md` puts explicitly out of
   scope, and the 2–4 day estimate is honest only for the enforcement mechanism, not for the
   token-management surface a second principal would actually require to be useful. If a second
   principal (a shared team gateway, a service account, a per-integration key) is ever on the
   roadmap, a future ADR should supersede this one and build option (b) properly, informed by
   what that principal actually needs — not retrofitted onto C1's shape.
2. **(c) Keep the middle — leave the ownership machinery as-is.** Rejected outright, and not
   treated as a real option. This is the status quo the Context section describes: ceremony
   without capability, a control a reader will trust and that does nothing, silently "off" after
   every restart. It actively misleads the next contributor — human or agent — who reads
   `_check_session_ownership` and reasonably concludes Hachi enforces per-caller isolation. It
   does not, and it structurally cannot with one token.
3. **Partial deletion** (e.g., delete the 403 branches but keep `set_session_owner`, or vice
   versa) was not seriously considered as a distinct option — it is strictly worse than either
   endpoint of the (a)/(b) choice: unreachable dead code (if checks are gone) or a control that
   still silently no-ops and still means "missing = allow" (if the checks stay). The task
   spec's own §9 names this explicitly as something not to do.

## Consequences

**Positive:**
- The code now matches reality. A reader of any gateway router can tell, by the absence of the
  word "owner," that Hachi does not attempt per-caller session isolation — CF-15's own
  observable-outcome bar ("under a minute, and the answer is true").
- ~200 lines of misleading ceremony are gone: two control-plane methods (plus their orphaned
  `SessionStore` twins), one check function and its runs-router wrapper, nine 403 branches, three
  inline list-filters, and the owner-stamping on three session-creation paths.
- [T-62]'s inconsistency (two of eleven session routes skipped the check) stops mattering — there
  is no longer a check for any route to skip.
- The two adjacent, unrelated features that had opportunistically reused the caller digest
  (BB-58's rate-limit key, T-32's facade session-key namespacing) are now honestly named
  `"authenticated"`/`"anonymous"` instead of implying a per-principal discrimination that never
  existed under one token.

**Negative / cost:**
- If a second principal is ever introduced (a shared team gateway, a service account, a
  per-integration API key), there is no ownership layer to fall back on — every valid token would
  have full access to every session, run, and interaction, by design, until a future ADR
  supersedes this one and option (b) (or an equivalent, purpose-built design) is built.
- `tests/test_session_ownership.py`, `tests/test_session_route_ownership.py`,
  `tests/test_gateway_interrupt.py`, `tests/test_gateway_router_extraction.py`, and
  `tests/test_gateway_openai_channels.py` all carried tests that asserted the deleted 403/filter
  behaviour or the deleted digest return value; those are rewritten to assert the new contract
  (see the T-61 completion report / commit message for the itemized list), not silently dropped.

**Reversibility: medium.** The deleted code is preserved in git history and this ADR describes
its exact shape, so resurrecting a comparable mechanism is not a blind rebuild. But reversal is
not a revert: §Context points 1–5 are real defects in the deleted design, so "undo this ADR"
should mean building option (b) properly (persisted owners, rotation-independent identities,
dependency-enforced uniform coverage, fail-closed default) rather than restoring `set_session_owner`
verbatim. The seam is `GatewayControlPlane` (where a persisted-owner column and lookup would live)
and the `Authenticated`/`require_auth` dependency in `gateway/deps.py` (where a second principal's
identity would need to be resolved instead of collapsed to a bool) — a future ADR should start
there.
