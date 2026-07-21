# `hachi.sdk` — the public API for pack authors

`hachi.sdk` is Hachi's supported, [PEP 561](https://peps.python.org/pep-0561/)-typed public
surface for third-party `.og` pack authors and anyone else writing code against Hachi's core
primitives from outside the `hachi` package. Everything else inside `hachi`
(`hachi.core`, `hachi.gateway`, `hachi.tui`, ...) is **internal** and may change without notice
— only the names re-exported from `hachi.sdk.__all__` are covered by this policy.

## Install

```bash
pip install hachi-og
```

The SDK ships as part of the base `hachi-og` package — **no extra is required**. Everything
`hachi.sdk` re-exports (FastAPI, tomlkit, pydantic, ...) is already a base dependency, so there
is nothing additional to install.

```python
import hachi.sdk as sdk

print(sdk.sdk_version())            # "0.1.0"
print(sdk.is_compatible("0.1.0"))   # True
```

## The stable surface (v0)

12 symbols, four groups:

| Symbol | What it is |
|---|---|
| `ToolRegistry` | Registers and dispatches LLM-callable tools — the loop's tool dispatch table. |
| `ToolSpec` | Declarative schema for a single tool (name, description, JSON schema, handler). |
| `BaseLLMClient` | Abstract base class for a provider integration (`generate`, `generate_stream`). |
| `OgPackManifest` | A parsed `pack.toml` manifest for an installable `.ogpack` (id, semver, scope). |
| `PackScope` | The permission/consent scope a pack declares it needs. |
| `ScheduleBudget` | A pack's declared limits on proactive scheduling (cadence / cost ceiling). |
| `parse_pack_toml` | Parse a `pack.toml` file into an `OgPackManifest`. |
| `render_consent_sheet` | Render the human-readable consent sheet shown before a pack install/update. |
| `events` | `hachi.core.runtime_events` re-exported as `sdk.events` — every `SESSION_*` / `TOOL_*` / `MEMORY_*` / `MCP_*` / `CHANNEL_*` constant. |
| `sdk_version()` | Returns the running `SDK_VERSION` string. |
| `is_compatible(required)` | The supported runtime compatibility check — see below. |
| `SDK_VERSION` | The SDK's own semver string, versioned independently of the app release. |

## Stability policy (v0)

> - Symbols in `hachi.sdk.__all__` are the supported surface. Everything else in `hachi` is
>   internal and may change in any release without notice.
> - **v0 caveat, stated plainly:** while `SDK_VERSION` is `0.x`, the surface may still change in
>   a minor release. Removals will be announced one minor version ahead. This is a real caveat,
>   not boilerplate — do not expect 1.0 guarantees at 0.1.0.
> - Adding a symbol bumps the **minor** version. Removing or changing one bumps the **major**.
> - `is_compatible(required)` is the supported runtime check for pack authors.
> - The surface is typed; `py.typed` ships, so `mypy` / `pyright` type-check against it like any
>   other typed dependency.

This is mechanically enforced, not aspirational: `tests/test_p3_06_sdk_v0.py` asserts
`set(hachi.sdk.__all__) == set(_STABLE_SYMBOLS)` (nothing missing, nothing extra) *and* pins
`(SDK_VERSION, sorted(__all__))` to a recorded fingerprint — so widening or narrowing the
surface without a deliberate version bump fails the suite.

### Checking compatibility at runtime

```python
from hachi.sdk import SDK_VERSION, is_compatible

if not is_compatible("0.1.0"):
    raise RuntimeError(f"this pack needs hachi.sdk >= 0.1, running {SDK_VERSION}")
```

`is_compatible(required)` implements **major-match + minor-GTE**: the running major version
must equal the required major version exactly, and the running minor must be `>=` the required
minor. Patch is not considered. A malformed version string (e.g. `"not-a-version"`) returns
`False` rather than raising.

## `sdk/` vs `client/` — deliberately separate, do not merge them

`hachi.sdk` (this package — 1 file, ~110 lines) and `hachi.client` (2,488 lines across 5 files)
look like they might be duplication. They are not, and should not be merged:

| | `hachi.sdk` | `hachi.client` |
|---|---|---|
| Audience | third-party **pack authors** | the Textual TUI (`tui/cockpit/`) |
| What it is | in-process re-export facade | synchronous REST + SSE consumer over HTTP |
| Talks to | `hachi.core` / `hachi.gateway` by **import** | the gateway over **HTTP** (`httpx`) — imports nothing from `hachi.*` |
| Versioned | `SDK_VERSION`, with a stability policy | no version constant, no stability claim |
| Tests | `tests/test_p3_06_sdk_v0.py` (14) | 164 across 7 files |

One is an import-time facade for code that runs inside the same process as Hachi; the other is
a network client for code that talks to a running Hachi gateway over HTTP. They answer
different questions and share no code. See
[ADR-0007](../architecture/adr/0007-sdk-public-surface.md) for the full decision record.

## A note on what `hachi.sdk` pulls in

`ToolSpec` is re-exported from `hachi.gateway.tool_registration`, so `import hachi.sdk`
transitively imports the FastAPI gateway package. This is a known, deliberate seam — decoupling
`ToolSpec` from the gateway is a real refactor with its own blast radius, tracked separately, not
done as part of blessing this surface. If an example that only imports `hachi.sdk` feels
inexplicably slow to start, that import is why.
