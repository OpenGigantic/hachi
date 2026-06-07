# Configuration Reference

Tokoboros is configured through `settings.toml` (written by `tokoboros quickstart`),
project-level `.env` files, and environment variables. Environment variables take
precedence over `settings.toml`. This page lists every variable the code actually
reads.

Config lives under your platform config dir (e.g. `~/.config/tokoboros/settings.toml`
on Linux); run `tokoboros doctor` to see the resolved paths.

## Provider & model

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | – | API key for the `anthropic` provider. |
| `OPENAI_API_KEY` | – | API key for the `openai-compatible` provider. |
| `OPENROUTER_API_KEY` | – | API key for the `openrouter` provider (200+ models). |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL for `openai-compatible`. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434/v1` | Endpoint for the local `ollama` provider. |
| `GEMINI_API_KEY` | – | Free API key for the `gemini` provider (get one at https://aistudio.google.com/). |
| `GOOGLE_AI_API_KEY` | – | Alias for `GEMINI_API_KEY` (checked if the primary is unset). |
| `CHATGPT_SESSION_TOKEN` | – | Browser session cookie for the `chatgpt-web` unofficial provider. |
| `CHATGPT_ACCESS_TOKEN` | – | Pre-resolved JWT access token for `chatgpt-web` (alternative to session cookie). |
| `TOKOBOROS_MODEL` | `claude-haiku-4-5` | Override the configured model id. |
| `TOKOBOROS_MAX_TOKENS` | `8192` | Max output tokens per LLM call. |

Supported provider kinds: `anthropic`, `openai-compatible`, `openrouter`, `groq`, `together`, `deepseek`, `ollama`, `gemini`, `chatgpt-web`.

### Model selection & guardrail (`settings.toml`)

```toml
[provider]
kind = "anthropic"
model = "claude-haiku-4-5"
# Hard guardrail: when set, ONLY these models may be selected via /model.
# Leave empty (or omit) to allow any model the provider exposes.
allowed_models = ["claude-haiku-4-5", "claude-sonnet-4-5"]
```

In the TUI, `/model` lists the models the provider currently exposes (discovered
live via the provider's models API, falling back to a curated list when offline).
If `allowed_models` is set, the list — and what `/model <name>` will switch to —
is restricted to exactly those entries.

### Voice input (speech-to-text)

The `transcribe_audio` tool transcribes a local audio file via an OpenAI-compatible
`/audio/transcriptions` endpoint. Credentials resolve lazily; the tool degrades to a
"not configured" hint until set.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKOBOROS_STT_API_KEY` | `OPENAI_API_KEY` | API key for transcription (falls back to the OpenAI key). |
| `TOKOBOROS_STT_BASE_URL` | `OPENAI_BASE_URL` or `https://api.openai.com/v1` | Whisper-compatible endpoint (e.g. Groq, a local server). |
| `TOKOBOROS_STT_MODEL` | `whisper-1` | Transcription model id. |

### Voice output (text-to-speech)

The `speak_text` tool synthesizes speech to a local audio file via an OpenAI-compatible
`/audio/speech` endpoint. Same lazy-cred + graceful-degradation pattern.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKOBOROS_TTS_API_KEY` | `OPENAI_API_KEY` | API key for synthesis (falls back to the OpenAI key). |
| `TOKOBOROS_TTS_BASE_URL` | `OPENAI_BASE_URL` or `https://api.openai.com/v1` | Speech-compatible endpoint. |
| `TOKOBOROS_TTS_MODEL` | `tts-1` | Synthesis model id. |
| `TOKOBOROS_TTS_VOICE` | `alloy` | Default voice (provider-specific). |

## Safety, budgets & rate limits

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKOBOROS_PERMISSION_TIER` | – | Override the permission tier (`sandbox`/`guided`/`unrestricted`). |
| `TOKOBOROS_MAX_SESSION_COST_USD` | `0` (off) | Stop a session once estimated spend reaches this (USD). |
| `TOKOBOROS_MAX_SESSION_TOKENS` | `0` (off) | Stop a session once total tokens reach this. |
| `TOKOBOROS_CHANNEL_RATE_LIMIT` | `30` | Max channel-webhook messages per sender per minute. |
| `TOKOBOROS_OPENAI_RATE_LIMIT` | `60` | Max `/v1/chat/completions` requests per minute. |
| `TOKOBOROS_CHANNEL_ALLOWLIST` | – | Comma list of `sender` or `channel:sender` allowed to use the webhook. |

Budget caps can also be set in `settings.toml`:

```toml
[limits]
max_session_cost_usd = 1.50
max_session_tokens = 500000
```

## Gateway (`tokoboros gateway`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKOBOROS_GATEWAY_API_TOKEN` | – | Bearer token required for protected gateway routes. |
| `TOKOBOROS_GATEWAY_API_TOKEN_FILE` | – | Read the token from a file (Docker-secret friendly). |
| `TOKOBOROS_GATEWAY_MAX_SESSIONS` | `100` | Max concurrent sessions. |
| `TOKOBOROS_GATEWAY_MAX_PROMPT_CHARS` | `100000` | Max characters per prompt. |
| `TOKOBOROS_CORS_ORIGINS` | `localhost:5173,4173` | Comma list of allowed CORS origins (`*` for any). |

## Channels & outbound calls

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | – | Bot token for `tokoboros channel telegram`. |
| `SLACK_BOT_TOKEN` | – | Slack bot token for `send_slack_message` (`chat.postMessage`; pass a channel). |
| `SLACK_WEBHOOK_URL` | – | Slack incoming-webhook URL (single channel; alternative to a bot token). |
| `DISCORD_WEBHOOK_URL` | – | Discord webhook URL for `send_discord_message` (single channel). |
| `DISCORD_BOT_TOKEN` | – | Discord bot token (alternative to a webhook; pass a channel id). |
| `TOKOBOROS_REST_ALLOWLIST` | – | Comma list of hosts the Universal REST Adapter may call. |
| `TOKOBOROS_REST_ALLOW_PRIVATE` | `false` | Allow REST/webhook **and browser** calls to private/loopback hosts (use with care). |
| `TOKOBOROS_ALLOW_OUTBOUND_SECRETS` | `false` | When `true`, disables automatic redaction of API keys/tokens/private keys in outbound Slack/Discord messages. Leave off so secrets can't be accidentally leaked. |

## Optional features (extras)

Some capabilities ship as optional dependency groups so the base install stays lean:

| Extra | Install | Enables |
|-------|---------|---------|
| `browser` | `pip install 'tokoboros[browser]'` then `playwright install chromium` | Headless browser tools (`browse_web`, `browser_act`, `browser_screenshot`). Disabled in the `sandbox` permission tier; URLs are validated like other outbound calls. |
| `google` | `pip install 'tokoboros[google]'` | Google Workspace integration (`GOOGLE_CLIENT_ID`/`SECRET`). |
| `slack` | `pip install 'tokoboros[slack]'` | Slack SDK. |
| `tracing` | `pip install 'tokoboros[tracing]'` | OpenTelemetry tracing. Enable at runtime with `TOKOBOROS_TRACING=true` (console exporter); emits spans for LLM and tool calls. No-op when the extra is absent or the flag is unset. |

## Integrations & secrets

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | – | OAuth client for Google Workspace integration. |
| `TOKOBOROS_SECRET_PASSPHRASE` | – | Passphrase for the encrypted secret-store fallback (when no OS keyring). |

## Install script overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `TOKOBOROS_REPO` | the public repo | Source repo for `install.sh` / `install.ps1`. |
| `TOKOBOROS_HOME` | `~/.tokoboros-src` | Where the installer clones Tokoboros. |

## Always-on background daemon

Tokoboros can run the gateway as a persistent background service that starts
automatically when you log in.  This powers proactive scheduling — the
`schedule run-due` tick happens continuously without a manual `tokoboros schedule run-due` call.

### Install / manage

```bash
# Install the daemon (auto-starts the gateway on every login)
tokoboros daemon install

# Custom bind address / port
tokoboros daemon install --host 0.0.0.0 --port 8888

# Check if the daemon is running
tokoboros daemon status

# Start / stop without re-installing
tokoboros daemon start
tokoboros daemon stop

# Remove the daemon completely
tokoboros daemon uninstall
```

### Platform details

| Platform | Mechanism | Service file |
|----------|-----------|-------------|
| macOS | launchd user agent (`launchctl load -w`) | `~/Library/LaunchAgents/ai.tokoboros.gateway.plist` |
| Linux | systemd user service (`systemctl --user enable --now`) | `~/.config/systemd/user/tokoboros.service` |
| Windows | Task Scheduler (`schtasks /sc ONLOGON`) | Task name: `Tokoboros Gateway` |

The daemon writes stdout/stderr to `{state_dir}/gateway.log`
(run `tokoboros doctor --json | jq .state_dir` to find the path).

### Schedule runner

Once the daemon is running, scheduled tasks fire automatically.
You can still run due schedules manually:

```bash
tokoboros schedule run-due
```

For a lightweight cron-only setup (no full gateway), call `run-due` from the
system cron instead of installing the daemon:

```
# crontab -e
*/5 * * * * tokoboros schedule run-due >> ~/.tokoboros-schedule.log 2>&1
```
