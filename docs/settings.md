# Hachi Settings Reference

This document describes every key in `settings.toml`, the authority model, the
permission check for the settings file itself, and setup guidance.

---

## File locations

| Scope | Path |
|-------|------|
| Global (user) | `~/.config/hachi/settings.toml` (Linux/macOS) or `%APPDATA%\hachi\settings.toml` (Windows) |
| Project-local | `<project>/.hachi/settings.toml` |

Project-local settings override global settings, which override built-in defaults.
Environment variables and `.env` files override both; CLI flags override everything.

**Both paths are listed in `.gitignore`.**  Use `settings.example.toml` (repo root)
as a template — copy and rename it; never commit the real file.

---

## Authority model

`settings.toml` is a **read-only authority file** from the agent's perspective.

- The agent reads config via `load_effective_config()` at session boot.
- The agent may call `write_global_config()` indirectly (via the settings UI or
  `hachi config-set`) but only for non-sensitive keys; it does not have direct
  filesystem write access to the config directory through its tool registry.
- `PermissionGate` enforces the tier, paths, and command rules declared here
  *before* any OS operation runs — the agent cannot override this at runtime.

---

## World-writable permission check

On POSIX (Linux / macOS), the config loader calls `stat()` on `settings.toml` and
warns if the file mode has the world-write bit set (`mode & 0o002`).

```
SECURITY: settings file /home/user/.config/hachi/settings.toml is world-writable
(mode 0o646). An unprivileged process could tamper with agent configuration.
Fix with: chmod o-w "/home/user/.config/hachi/settings.toml"
```

Set `HACHI_CONFIG_STRICT=1` in the environment to make this a hard error (raises
`PermissionError` and aborts config load).

On Windows, ACL-based permissions are used and this check is skipped silently.

---

## Strict mode

Setting the environment variable `HACHI_CONFIG_STRICT=1` (or passing `strict=True` to
`load_effective_config()`) switches from warning-mode to abort-mode for:

- World-writable `settings.toml` (raises `PermissionError`).
- Malformed TOML in the primary settings file (re-raises `tomllib.TOMLDecodeError`).
- Unknown top-level keys (raises `ValueError` listing them).

Recommended for CI and production deployments.

---

## Unknown-key validation

The loader compares every top-level key in the parsed TOML against the fields declared
on `ResolvedConfig`.  Unknown keys are logged as a warning:

```
settings file /home/user/.config/hachi/settings.toml contains unknown top-level
key(s): foo, bar. These keys are ignored. Check for typos or consult settings.example.toml.
```

Under `HACHI_CONFIG_STRICT=1` this is a hard error.

---

## Key reference

The authoritative list of keys and their defaults is in `settings.example.toml` at the
repository root.  Below is a compact summary by section.

### [meta]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `config_version` | int | `1` | Schema version for forward-compat checks |
| `imported_from_legacy` | bool | `false` | Set by migration wizard; do not edit |

### [provider]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `kind` | str | `"anthropic"` | LLM backend selector |
| `model` | str | `"claude-haiku-4-5"` | Model id sent to the provider |
| `base_url` | str\|null | `null` | Base URL for OpenAI-compatible endpoints |
| `api_key_env` | str\|null | `null` | **[SENSITIVE]** Env var holding the API key |
| `api_key_ref` | str\|null | `null` | **[SENSITIVE]** Secret-store reference |
| `allowed_models` | list[str] | `[]` | Allowlist; empty = no restriction |
| `headers` | dict[str,str] | `{}` | **[SENSITIVE]** Extra HTTP headers |

### [providers.\<name\>]

Named additional providers for multi-model routing.  Same fields as `[provider]`.

### [agent]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `name` | str | `""` | Display name in TUI/web |
| `persona` | str | `""` | System-prompt persona prefix |
| `max_tokens` | int | `8192` | Max tokens per generated turn |
| `temperature` | float | `0.0` | Sampling temperature |

### [permissions]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `tier` | str | `"guided"` | PermissionGate enforcement level |

`tier` choices: `"sandbox"` (read-only), `"guided"` (workspace write + shell approval),
`"unrestricted"` (no confirmation prompts).

#### [permissions.ask_before]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `spend_over_threshold` | bool | `true` | Pause before task exceeds per-task cost cap |
| `network_egress` | bool | `true` | Pause before off-machine data send |
| `delete_files` | bool | `true` | Pause before file deletion |

### [limits]

All values default to `null` (no cap).

| Key | Type | Effect |
|-----|------|--------|
| `max_daily_cost_usd` | float\|null | Rolling calendar-day spend cap in USD |
| `max_session_cost_usd` | float\|null | Per-session cumulative spend cap in USD |
| `max_turn_cost_usd` | float\|null | Per-turn spend cap in USD |
| `max_session_tokens` | int\|null | Per-session token limit (input + output) |
| `max_turn_tokens` | int\|null | Per-turn token limit |

### [memory]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `enable_session_history` | bool | `true` | Persist transcripts in SQLite |
| `enable_semantic_memory` | bool | `true` | Index facts into ChromaDB |
| `backend` | str | `"chroma"` | Vector store: `"chroma"` \| `"in_memory"` \| `"fts5_only"` |
| `graph` | bool | `false` | FTS5 + Chroma RRF fusion (experimental) |

### [voice]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `stt_model` | str\|null | `null` | STT model name (e.g. `"whisper-1"`) |
| `stt_base_url` | str\|null | `null` | STT endpoint base URL |
| `stt_api_key_env` | str\|null | `null` | **[SENSITIVE]** STT API key env var |
| `stt_provider` | str | `"openai"` | STT provider (only `"openai"` supported) |
| `tts_model` | str\|null | `null` | TTS model name (e.g. `"tts-1"`) |
| `tts_base_url` | str\|null | `null` | TTS endpoint base URL |
| `tts_api_key_env` | str\|null | `null` | **[SENSITIVE]** TTS API key env var |
| `tts_voice` | str\|null | `null` | Default TTS voice (e.g. `"alloy"`) |
| `tts_provider` | str | `"openai"` | TTS provider: `"openai"` \| `"elevenlabs"` |
| `elevenlabs_api_key_env` | str\|null | `null` | **[SENSITIVE]** ElevenLabs API key env var |
| `elevenlabs_voice_id` | str\|null | `null` | ElevenLabs voice id |

### [search]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `provider` | str\|null | `null` | Search provider (`"tavily"` to enable web search) |
| `max_calls_per_turn` | int | `10` | Max search/extract calls per turn |
| `cache_ttl_seconds` | float | `300.0` | In-session cache TTL in seconds |
| `window_max_calls` | int | `60` | Sliding-window rate limit call count |
| `window_seconds` | float | `3600.0` | Sliding-window duration in seconds |

### [files]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `max_size_bytes` | int | `52428800` | Upload ceiling in bytes (50 MB) |
| `allowed_types` | list[str] | `[]` | MIME type allowlist; empty = all allowed |
| `storage_backend` | str | `"local"` | Storage backend (`"local"` is the only active impl) |
| `scan_hook` | str | `"noop"` | Virus-scan hook (`"clamav"` / `"cloudmersive"` are future seams) |
| `index_on_extract` | bool | `true` | Index extracted text into Chroma for RAG |

### [routing]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `default_model` | str | `"claude-haiku-4-5"` | Fallback model id |
| `auto` | bool | `false` | Derive smart-default routing from the model registry |
| `capabilities` | dict[str, list[str]] | `{}` | Ordered candidate model-id lists per capability |
| `model_overrides` | list | `[]` | Extra ModelDescriptor dicts (highest registry precedence) |

#### [routing.resilience]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `max_attempts` | int | `3` | Max retry attempts |
| `backoff_base_s` | float | `0.5` | Exponential-backoff base in seconds |
| `timeout_s` | float | `60.0` | Per-attempt timeout in seconds |
| `breaker_threshold` | int | `5` | Consecutive failures before circuit breaker opens |
| `breaker_cooldown_s` | float | `30.0` | Cooldown in seconds before breaker re-closes |

### [events]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `bus_funnel` | bool | `false` | Route events through the EventBus singleton |

### [jobs]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `durable_handlers` | bool | `false` | Use named handlers for background jobs |
| `worker_count` | int | `1` | Job-queue worker-thread count |

### [observability]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `enabled` | bool | `false` | Enable tracing on gateway boot |
| `exporter` | str | `"console"` | Trace exporter: `"console"` \| `"otlp"` \| `"langfuse"` |
| `endpoint` | str\|null | `null` | OTLP exporter endpoint URL |
| `metrics_enabled` | bool | `false` | Also initialise the metrics facade |

### [channels.telegram]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `allowlist` | list[str] | `[]` | **[SENSITIVE]** Telegram chat ids allowed to reach the agent |

### [capabilities.\<name\>]

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `enabled` | bool | `false` | Enable the integration |
| `configured` | bool | `false` | Set by the setup wizard when credentials are stored |
| `secret_ref` | str\|null | `null` | Secret-store key for the integration credential |

Built-in capabilities: `google`, `notion`, `slack`.

### [mcp_servers.\<name\>]

Each sub-table registers one MCP server.

| Key | Type | Effect |
|-----|------|--------|
| `command` | str | Shell command to launch the server |
| `args` | list[str] | Arguments passed to `command` |
| `env` | dict[str,str] | Extra environment variables |
| `timeout_s` | float | Connection timeout in seconds (default: 30) |
| `enabled` | bool | Set to `false` to disable without removing the entry |

---

## Quick-start example

```toml
[provider]
kind = "anthropic"
model = "claude-haiku-4-5"
api_key_env = "ANTHROPIC_API_KEY"

[permissions]
tier = "guided"

[limits]
max_daily_cost_usd = 5.0
```

See `settings.example.toml` in the repository root for a fully-commented template
covering every section.
