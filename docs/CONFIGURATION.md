# Configuration Reference

Hachi is configured through `settings.toml` (written by `hachi quickstart`),
project-level `.env` files, and environment variables. Environment variables take
precedence over `settings.toml`. This page lists every variable the code actually
reads.

Config lives under your platform config dir (e.g. `~/.config/hachi/settings.toml`
on Linux); run `hachi doctor` to see the resolved paths.

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
| `HACHI_MODEL` | `claude-haiku-4-5` | Override the configured model id. |
| `HACHI_MAX_TOKENS` | `8192` | Max output tokens per LLM call. |

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
| `HACHI_STT_API_KEY` | `OPENAI_API_KEY` | API key for transcription (falls back to the OpenAI key). |
| `HACHI_STT_BASE_URL` | `OPENAI_BASE_URL` or `https://api.openai.com/v1` | Whisper-compatible endpoint (e.g. Groq, a local server). |
| `HACHI_STT_MODEL` | `whisper-1` | Transcription model id. |

### Voice output (text-to-speech)

The `speak_text` tool synthesizes speech to a local audio file via an OpenAI-compatible
`/audio/speech` endpoint. Same lazy-cred + graceful-degradation pattern.

| Variable | Default | Purpose |
|----------|---------|---------|
| `HACHI_TTS_API_KEY` | `OPENAI_API_KEY` | API key for synthesis (falls back to the OpenAI key). |
| `HACHI_TTS_BASE_URL` | `OPENAI_BASE_URL` or `https://api.openai.com/v1` | Speech-compatible endpoint. |
| `HACHI_TTS_MODEL` | `tts-1` | Synthesis model id. |
| `HACHI_TTS_VOICE` | `alloy` | Default voice (provider-specific). |

## Safety, budgets & rate limits

| Variable | Default | Purpose |
|----------|---------|---------|
| `HACHI_PERMISSION_TIER` | – | Override the permission tier (`sandbox`/`guided`/`unrestricted`). |
| `HACHI_MAX_SESSION_COST_USD` | `0` (off) | Stop a session once estimated spend reaches this (USD). |
| `HACHI_MAX_SESSION_TOKENS` | `0` (off) | Stop a session once total tokens reach this. |
| `HACHI_CHANNEL_RATE_LIMIT` | `30` | Max channel-webhook messages per sender per minute. |
| `HACHI_OPENAI_RATE_LIMIT` | `60` | Max `/v1/chat/completions` requests per minute. |
| `HACHI_CHANNEL_ALLOWLIST` | – | Comma list of `sender` or `channel:sender` allowed to use the webhook. |

Budget caps can also be set in `settings.toml`:

```toml
[limits]
max_session_cost_usd = 1.50
max_session_tokens = 500000
```

## Gateway (`hachi gateway`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `HACHI_GATEWAY_API_TOKEN` | – | Bearer token required for protected gateway routes. |
| `HACHI_GATEWAY_API_TOKEN_FILE` | – | Read the token from a file (Docker-secret friendly). |
| `HACHI_GATEWAY_MAX_SESSIONS` | `100` | Max concurrent sessions. |
| `HACHI_GATEWAY_MAX_PROMPT_CHARS` | `100000` | Max characters per prompt. |
| `HACHI_CORS_ORIGINS` | `localhost:5173,4173` | Comma list of allowed CORS origins (`*` for any). |

## Channels & outbound calls

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | – | Bot token for `hachi channel telegram` (inbound) **and** the `send_telegram_message` tool (outbound). |
| `TELEGRAM_CHAT_ID` | – | Default chat id for `send_telegram_message` (user, group, or channel). Required unless passed per-call. |
| `META_WHATSAPP_TOKEN` | – | Meta WhatsApp Business bearer token (`hachi channel whatsapp` inbound + `send_whatsapp_message` outbound). |
| `META_WHATSAPP_PHONE_ID` | – | Meta sender phone-number ID (from the Meta App Dashboard). |
| `META_WHATSAPP_VERIFY_TOKEN` | – | Webhook verify token to match in the Meta App Dashboard. Used by `hachi channel whatsapp` for the GET verification handshake. |
| `TWILIO_ACCOUNT_SID` | – | Twilio Account SID (alternative to Meta Cloud API for WhatsApp). |
| `TWILIO_AUTH_TOKEN` | – | Twilio Auth Token. |
| `TWILIO_WHATSAPP_FROM` | – | Twilio WhatsApp sender (e.g. `whatsapp:+14155238886`). |
| `SLACK_BOT_TOKEN` | – | Slack bot token for `send_slack_message` (`chat.postMessage`; pass a channel). |
| `SLACK_WEBHOOK_URL` | – | Slack incoming-webhook URL (single channel; alternative to a bot token). |
| `DISCORD_WEBHOOK_URL` | – | Discord webhook URL for `send_discord_message` (single channel). |
| `DISCORD_BOT_TOKEN` | – | Discord bot token (alternative to a webhook; pass a channel id). |
| `HACHI_SMTP_HOST` | `smtp.gmail.com` | SMTP server for `send_email_digest` and notification digests. |
| `HACHI_SMTP_PORT` | `587` | SMTP port (STARTTLS). |
| `HACHI_SMTP_USER` | – | SMTP username / sender address. Required to enable email notifications. |
| `HACHI_SMTP_PASSWORD` | – | SMTP password or app-specific password. |
| `HACHI_SMTP_FROM` | `SMTP_USER` | Override the From: address (defaults to `SMTP_USER`). |
| `HACHI_SMTP_TO` | – | Default recipient for `send_email_digest` and digest flushes. |
| `HACHI_REST_ALLOWLIST` | – | Comma list of hosts the Universal REST Adapter may call. |
| `HACHI_REST_ALLOW_PRIVATE` | `false` | Allow REST/webhook **and browser** calls to private/loopback hosts (use with care). |
| `HACHI_ALLOW_OUTBOUND_SECRETS` | `false` | When `true`, disables automatic redaction of API keys/tokens/private keys in outbound Slack/Discord/WhatsApp/Telegram messages. Leave off so secrets can't be accidentally leaked. |

## Optional features (extras)

Some capabilities ship as optional dependency groups so the base install stays lean:

| Extra | Install | Enables |
|-------|---------|---------|
| `browser` | `pip install 'hachi-og[browser]'` then `playwright install chromium` | Headless browser tools (`browse_web`, `browser_act`, `browser_screenshot`). Disabled in the `sandbox` permission tier; URLs are validated like other outbound calls. |
| `google` | `pip install 'hachi-og[google]'` | Google Workspace integration (`GOOGLE_CLIENT_ID`/`SECRET`). |
| `slack` | `pip install 'hachi-og[slack]'` | Slack SDK. |
| `tracing` | `pip install 'hachi-og[tracing]'` | OpenTelemetry tracing. Enable at runtime with `HACHI_TRACING=true` (console exporter); emits spans for LLM and tool calls. No-op when the extra is absent or the flag is unset. |

## Integrations & secrets

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | – | OAuth client for Google Workspace integration. |
| `HACHI_SECRET_PASSPHRASE` | – | Passphrase for the encrypted secret-store fallback (when no OS keyring). |

## Install script overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `HACHI_REPO` | the public repo | Source repo for `install.sh` / `install.ps1`. |
| `HACHI_HOME` | `~/.hachi-src` | Where the installer clones Hachi. |

## Always-on background daemon

Hachi can run the gateway as a persistent background service that starts
automatically when you log in.  This powers proactive scheduling — the
`schedule run-due` tick happens continuously without a manual `hachi schedule run-due` call.

### Install / manage

```bash
# Install the daemon (auto-starts the gateway on every login)
hachi daemon install

# Custom bind address / port
hachi daemon install --host 0.0.0.0 --port 8888

# Check if the daemon is running
hachi daemon status

# Start / stop without re-installing
hachi daemon start
hachi daemon stop

# Remove the daemon completely
hachi daemon uninstall
```

### Platform details

| Platform | Mechanism | Service file |
|----------|-----------|-------------|
| macOS | launchd user agent (`launchctl load -w`) | `~/Library/LaunchAgents/ai.hachi.gateway.plist` |
| Linux | systemd user service (`systemctl --user enable --now`) | `~/.config/systemd/user/hachi.service` |
| Windows | Task Scheduler (`schtasks /sc ONLOGON`) | Task name: `Hachi Gateway` |

The daemon writes stdout/stderr to `{state_dir}/gateway.log`
(run `hachi doctor --json | jq .state_dir` to find the path).

### Schedule runner

Once the daemon is running, scheduled tasks fire automatically.
You can still run due schedules manually:

```bash
hachi schedule run-due
```

For a lightweight cron-only setup (no full gateway), call `run-due` from the
system cron instead of installing the daemon:

```
# crontab -e
*/5 * * * * hachi schedule run-due >> ~/.hachi-schedule.log 2>&1
```
