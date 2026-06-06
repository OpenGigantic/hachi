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
| `TOKOBOROS_MODEL` | `claude-haiku-4-5` | Override the configured model id. |
| `TOKOBOROS_MAX_TOKENS` | `8192` | Max output tokens per LLM call. |

Supported provider kinds: `anthropic`, `openai-compatible`, `openrouter`, `ollama`.

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
