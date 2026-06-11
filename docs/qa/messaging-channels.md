# QA Report — Discord / Slack / WhatsApp Channels & Messaging Tools

Scope: `src/tokoboros/channels/{discord,slack,whatsapp,router,runner,base}.py`,
`src/tokoboros/tools/messaging_tools.py`, `src/tokoboros/integrations/slack/*`.
Environment: Windows 11, Python 3.14.3, `./.venv` with `PYTHONPATH=src`, tier `unrestricted`.
Date: 2026-06-11.

## Summary

The channel/messaging code is in good shape. Pure request builders, SSRF guard,
secret redaction, clamping, the router, and the runner all behave correctly and the
86 existing tests pass. **No crashes were found** — every misconfiguration path
degrades to a readable message.

Live sends performed (exactly one per surface, clearly labeled):

- **Discord webhook** — SENT live (HTTP 204). `{'status':'sent','target':'webhook'}`.
- **Slack webhook** — SENT live (HTTP 200). `{'status':'sent','target':'webhook'}`.
- **Discord bot** (channel `1514470972953067682`) — attempted live, **403 Missing Access
  (code 50001)**. Config caveat: bot "Toko" is not in that channel's guild. Request
  builder verified correct.
- **Slack bot send** — NOT sent. The provided `slack_bot_token` (env and vault are
  identical) only carries the `incoming-webhook` OAuth scope, so a bot `chat.postMessage`
  is impossible. Spending the live-send budget on a guaranteed `missing_scope` was not
  useful; the bot request builder is verified correct by unit test.

The one substantive code issue is a **secret-redaction inconsistency**: the Slack
*integration* tools (`slack_send_message`, `slack_upload_file`) bypass the outbound
secret-redaction guard that the `messaging_tools` Slack tool applies — so the agent has
both a guarded and an unguarded path to post to Slack.

Severity counts: Critical 0 · High 0 · Medium 1 · Low 2 · Nit 1.
(Plus 2 environment/config caveats that are not code bugs.)

## Findings

### [Medium] Slack integration tools bypass outbound secret-redaction (leak path)

**Where:** `src/tokoboros/integrations/slack/chat.py` `SlackChat.send_message` /
`upload_file`; contrast with `src/tokoboros/tools/messaging_tools.py` `_make_send_slack`
(`_guard_outbound` → `redact_outbound`).

**Repro (live-verified with a mocked transport):**
```python
from unittest.mock import patch
from tokoboros.integrations.slack import SlackChat
SECRET = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
chat = SlackChat(); captured = {}
with patch.object(chat, "_post", side_effect=lambda ep, json_data=None: captured.setdefault("p", json_data) or {"ok": True, "ts": "1"}):
    chat.send_message("#ops", f"deploy key {SECRET}")
print(captured["p"])   # -> {'channel': '#ops', 'text': 'deploy key sk-ant-api03-...'}  SECRET on the wire
```
The parallel `send_slack_message` tool (messaging_tools) redacts the same input to
`deploy key [REDACTED SECRET]`.

**Expected:** All agent-reachable outbound Slack text should pass through
`redact_outbound`, consistently with the messaging_tools surface.

**Actual:** `slack_send_message` and `slack_upload_file` (`initial_comment`) send the
secret un-redacted. Both tool sets are registered into the same registry in
`gateway/orchestrator.py` (`register_messaging_tools` at the `messaging` ToolSpec and
`SlackChat`/`SlackWorkspace` via the `slack` capability / `integration_tools` ToolSpec),
so the agent really does have both a guarded and an unguarded path.

**Impact:** A secret the agent read (config file, env dump) can be exfiltrated to Slack
via the integration tool, defeating the `TOKOBOROS_ALLOW_OUTBOUND_SECRETS`-gated guard.
Confidentiality regression; reachable whenever Slack is configured.

**Suggested fix:** Route `SlackChat.send_message`/`upload_file` text and
`initial_comment` through `tokoboros.core.secret_scanner.redact_outbound` (and append the
"N secret(s) redacted" note), or centralize redaction in `SlackService._post` /
`_post_multipart` so every integration cannot forget it.

### [Low] `slack_search_messages` can never work with a bot token (wrong token type)

**Where:** `src/tokoboros/integrations/slack/workspace.py` `search_messages` →
`search.messages`.

**Repro (live-verified):** with the bot token, `search.messages` returns
`{'ok': False, 'error': 'not_allowed_token_type'}` — Slack's `search.*` family requires a
**user** token (`xoxp-`) with `search:read`, never a bot token, regardless of scopes.
The tool returned: `Failed to search Slack workspace: Slack API returned error:
not_allowed_token_type` (graceful, no crash).

**Expected:** The tool should either use a user token or surface that `search.messages` is
bot-token-incompatible up front.

**Actual:** It always fails for a bot-token-only configuration (the only token type the
rest of the integration uses). The tool's description hints "may require user token
scopes" but the integration has no user-token plumbing.

**Impact:** Dead capability under the standard bot-token setup; wasted agent turn.

**Suggested fix:** Document/guard that `slack_search_messages` needs a user token, or add
a separate `slack_user_token` vault key for `search.*`.

### [Low] Slack API errors drop the `needed` scope, hurting diagnosability

**Where:** `src/tokoboros/integrations/slack/service.py` `_post`/`_get`/`_post_multipart`
(`error_msg = data.get("error", ...)` — only the short error code is kept).

**Repro (live-verified):** every `slack_*` tool against the provided token returns just
`missing_scope`. Slack's response body actually includes the precise fix, e.g.
`conversations.list` → `needed: 'channels:read,groups:read,mpim:read,im:read'`;
`chat.postMessage` → `chat:write:bot`; `reactions.add` → `reactions:write`;
`files.getUploadURLExternal` → `files:write`.

**Expected:** Surface the missing scope so the user/agent can self-serve the fix.

**Actual:** The actionable `needed`/`provided` fields are discarded; the agent only sees
`missing_scope`.

**Impact:** Poor operability — the user cannot tell which scope to add without manually
inspecting the raw API response.

**Suggested fix:** Include `needed`/`provided` in the raised message, e.g.
`Slack API error: missing_scope (needs: channels:read; has: incoming-webhook)`.

### [Nit] `_clamp` truncation drops 1 extra char and may split a surrogate/grapheme

**Where:** `channels/discord.py::_clamp` (`content[: MAX_CONTENT - 1] + "…"`), same in
`channels/whatsapp.py::_clamp`.

**Repro:** a 3000-char Discord message clamps to exactly 2000 chars ending in `…`
(verified). The slice is by code point, so a long message ending mid-emoji could cut a
multi-codepoint grapheme; also the `-1` means the cap is `MAX-1` original chars + ellipsis
rather than `MAX`. Cosmetic only — within Discord's 2000 limit, never over.

**Impact:** Negligible (cosmetic truncation edge).

**Suggested fix:** Optional — clamp on graphemes, or document the off-by-one as intentional
headroom.

## Config / environment caveats (not code bugs)

- **Discord bot lacks guild access:** sending to channel `1514470972953067682` with
  `DISCORD_BOT_TOKEN` (bot "Toko") returns `403 Missing Access (50001)`. The channel
  belongs to the Captain Hook webhook's guild; the bot is not a member. Request builder
  (`POST /api/v10/channels/{id}/messages`, `Authorization: Bot …`, `{"content": …}`,
  204/200/201 success handling) is correct.
- **Slack bot token is webhook-scoped only:** `auth.test` → team *Automation*, user
  *tokoboros*, `x-oauth-scopes: incoming-webhook`. Env `SLACK_BOT_TOKEN` == vault
  `slack_bot_token`. Consequently all six `slack_*` Web-API tools (`list_channels`,
  `send_message`, `read_channel`, `add_reaction`, `search_messages`, `upload_file`) fail —
  five with `missing_scope`, search with `not_allowed_token_type` — but all fail
  **gracefully** (readable string, no exception). To exercise them live, the Slack app
  needs `channels:read,groups:read` (list/read), `chat:write` (send), `reactions:write`
  (react), `files:write` (upload), and a **user** token with `search:read` (search).

## What passed

- **Discord webhook send** — live, HTTP 204, success dict returned. ✅ live-verified.
- **Slack webhook send** — live, HTTP 200, success dict returned. ✅ live-verified.
- **Pure request builders** — Discord webhook/bot, Slack webhook/bot, WhatsApp Meta/Twilio
  all produce correct URL/payload/headers; Twilio `whatsapp:` prefix added idempotently;
  Meta strips leading `+`; Basic-auth header well-formed. Empty-channel/empty-`to` raise the
  right typed errors. ✅ code + unit-verified.
- **SSRF guard** (`net_guard.validate_outbound_url`) — blocks `127.0.0.1`, `localhost`,
  `169.254.169.254` (cloud metadata), `10.0.0.0/8`, and non-http(s) schemes (`file://`);
  allows `hooks.slack.com`. Enforced on Slack/Discord/WhatsApp webhook send paths
  (`SlackSender`/`DiscordSender` raise `…Error: blocked webhook URL …`). Bot/fixed-host
  paths correctly skip it. `follow_redirects=False` in the default poster. ✅ live-verified.
- **Outbound secret redaction** (messaging_tools path) — default redacts `sk-ant-…`,
  `AKIA…`, `xoxb-…`, etc. before the wire; `TOKOBOROS_ALLOW_OUTBOUND_SECRETS=true` opts
  out; redaction count surfaced to the agent; no false positives on prose or webhook URLs.
  ✅ verified.
- **`from_env()` construction** — Discord/Slack report configured with real env; WhatsApp
  reports **not configured** with `_active_provider == "none"`; Slack correctly prefers the
  bot token when both bot+webhook are set. ✅ live-verified.
- **WhatsApp not-configured paths** — `WhatsAppSender.send`, `WhatsAppAdapter.send`, and the
  `send_whatsapp_message` tool all return/raise a graceful "not configured" message (no
  crash) for both Meta and Twilio. ✅ verified.
- **`parse_meta_webhook` / `parse_twilio_webhook`** — correctly parse representative
  inbound payloads (sender id, profile name, text); skip non-text Meta messages; return
  `[]` on garbage/empty/non-form bodies without raising. ✅ verified.
- **Router** (`ChannelRouter`) — stable per-`(channel, sender_id)` session keys; allowlist
  blocks unknown senders with a friendly reply and coerces int/str ids; a runner exception
  is isolated into a friendly `⚠️ …` reply (receive loop never crashes). ✅ verified.
- **Runner** (`GatewaySessionRunner`) — boots exactly one `AgentSession` per session key,
  reuses it across turns, and shuts down cleanly. ✅ verified.
- **Graceful tool errors** — malformed Slack `ts`, missing upload file, missing `to`, and
  all not-configured cases return readable strings rather than raising. ✅ verified.
