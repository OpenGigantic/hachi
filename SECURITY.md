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
