# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest on `main` | Yes |

Hachi is currently pre-1.0 (development release). Security fixes are applied
to the `main` branch only; no backport branches exist yet.

## Reporting a Vulnerability

**Do not report security issues in public GitHub issues.**

Email: **security@opengigantic.com**

Please include:
- A clear description of the vulnerability
- Steps to reproduce (or a minimal proof-of-concept)
- The affected component (e.g., `gateway/fastapi_app.py`, permission gate, credential store)
- The potential impact

We aim to acknowledge reports within **48 hours** and will communicate a triage
decision within **7 days**. Critical issues (remote code execution, credential leak,
permission-gate bypass) are treated as P0 and patched on an accelerated track.

## Responsible Disclosure

We follow coordinated disclosure. Please give us **90 days** to investigate and ship
a fix before public disclosure. We will credit researchers (by name or alias, your
choice) in the release notes unless you request anonymity.

## Scope

Security issues we consider in-scope:

- **Permission gate bypass** — any path that executes an OS command or filesystem
  write outside the declared roots in `device-permissions.og`
  (`src/hachi/tools/permissions.py`)
- **Credential/secret exposure** — credentials leaking via logs, API responses,
  error messages, or the session transcript (`src/hachi/core/session_store.py`)
- **Gateway authentication bypass** — `src/hachi/gateway/fastapi_app.py`
  bearer-token gate skipped under any condition
- **Session isolation failures** — one user's session data readable by another
  session or by the gateway's event stream
- **Sub-agent privilege escalation** — a delegated sub-agent exceeding its declared
  scope (`src/hachi/core/sub_agent.py` `PermissionScope`)
- **Dependency confusion / supply-chain** — malicious `.ogpack` installable without
  user consent (when the pack marketplace ships in P2)

Out of scope (use regular GitHub issues):

- Rate limiting / DoS against a locally-hosted gateway
- Issues requiring physical access to the machine
- Social-engineering attacks against the user (Hachi is a local-first tool)

## Trust model

**Hachi is a single-operator product.** One trusted operator per machine / VPS. It is NOT
multi-tenant. Do NOT expose a shared Hachi gateway to multiple distinct users or tenants.

### Memory isolation (AUDIT P1-1)

All ChromaDB collections (`core_memory`, `user_profile`, `agent_profile`, `delegation_log`) are
shared per install. The `MemoryVault` accepts an `account_id` parameter that namespaces
collections with a per-account prefix (ADV-023), but this parameter is **intentionally unplumbed**
in the current release — `boot_agent` constructs the vault with no `account_id`, and the
`principal` field in `TraceContext` is a hardcoded `"session"` placeholder
(`src/hachi/gateway/orchestrator.py`).

**Under the single-operator trust model this is safe** because only one authenticated principal
exists in a normal install.

**It becomes unsafe the moment a second user is added.** Two principals sharing one install would
share one `user_profile` / `core_memory` collection, enabling cross-user PII recall and profile
contamination.

**Do NOT add a second principal/tenant without:**
1. Plumbing the authenticated principal → `account_id` into `MemoryVault` construction.
2. Updating the `ProcessSharedServices` fingerprint to include the account key so each account
   gets a separate shared-vault singleton.

The `account_id` seam is already built into `MemoryVault.__init__` — it just needs to be wired
from the authentication layer. See `src/hachi/core/memory_vault.py` docstring (ADV-023) and
`DECISIONS.md` "P1-1 — document the single-tenant constraint" for the full design note.

Multi-tenant account isolation is tracked as a 12-month roadmap item (AUDIT P1-1 action b).

## Security Architecture Notes

- **No remote code execution path in skill install** — `.og` files are data files parsed
  by a pure-Python parser; they do not execute code at install time. This is a
  deliberate design constraint (`docs/strategy/05-agent-platform.md` §6).
- **PermissionGate** enforces filesystem roots and shell-command allowlists at
  call time, not at prompt time (`src/hachi/tools/permissions.py`).
- **Credentials** are stored in the system keyring via `keyring`; the `.env`
  fallback path is documented as development-only (`src/hachi/core/llm.py:50–52`).
- **Memory quarantine** — writes from untrusted content are quarantined and require
  explicit user approval before entering long-term storage
  (`src/hachi/core/memory_vault.py` `MemoryGuard`).
- **Docker non-root runtime** — the published container image runs as UID 10001
  (`hachi` user, no supplementary groups). The gateway process never requires root
  and has no `sudo`/`setuid` paths.

## CI Security Gates

Every push and pull request runs the following automated security checks:

| Check | Tool | Trigger | Failure behaviour |
|-------|------|---------|-------------------|
| Dependency CVE scan | `pip-audit` | push + PR | Advisory on `main`; **blocking on PRs** |
| SAST (medium+ severity) | `bandit -ll` | push + PR | Advisory until src/ medium findings resolved |
| Application security audit | `hachi security-audit` | push + PR | Blocking on `--fail-on-high`; `--fail-on-medium` on PRs |

### Outstanding CVEs (last audited 2026-06-19)

These CVEs are present in transitive dependencies and are tracked for remediation:

| Package | CVE / Advisory | Fixed in | Notes |
|---------|---------------|----------|-------|
| `aiohttp 3.13.5` | CVE-2026-34993, CVE-2026-47265, CVE-2026-50269, CVE-2026-54273–54280 | 3.14.0 / 3.14.1 | Transitive via `chromadb` and `uvicorn`; bump when chromadb pins allow |
| `starlette 1.2.1` | CVE-2026-54282, CVE-2026-54283 | 1.3.0 / 1.3.1 | Transitive via `fastapi`; bump fastapi to lift starlette |
| `cryptography 48.0.0` | GHSA-537c-gmf6-5ccf | 48.0.1 | Direct dep; bump `cryptography>=48.0.1` in `pyproject.toml` |
| `chromadb 1.5.9` | CVE-2026-45829 | unknown | No fix version published yet; track upstream advisory |

To suppress a CVE in CI once a justified exception is needed, add
`--ignore-vuln <ID>` to the `pip-audit` step in `.github/workflows/ci.yml` with a
comment that references the tracking issue. Do not add ignores without a public issue.
