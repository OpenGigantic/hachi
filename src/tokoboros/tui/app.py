"""Textual-first Tokoboros operator interface.

v1 scope:
- Single-user local runtime
- Session-first workflow with streaming transcript and tool timeline
- Operational visibility for jobs, permissions, and MCP inventory
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib import error, request

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, NoScreen, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, RichLog, Static, TextArea

from tokoboros.core.onboarding import (
    OnboardingService,
    SetupStep,
    default_api_key_env_for,
)
from tokoboros.core.permissions import PermissionDecision, PermissionRequest
from tokoboros.core.runtime_read_models import (
    ActivitySnapshot,
    _looks_like_error,
    advance_activity,
)
from tokoboros.core.secret_store import create_default_secret_store
from tokoboros.gateway.orchestrator import AgentSession, boot_agent
from tokoboros.gateway.workspace import get_skills_dir, get_workspace_dir, load_config
from tokoboros.tui import theme
from tokoboros.tui.authoring import (
    apply_authoring_update,
    discover_authoring_targets,
    generate_unified_patch,
    is_authoring_target,
)
from tokoboros.tui.formatters import (
    format_integrations,
    format_memory,
    format_og_explorer,
    format_ops,
    format_search_results,
    format_settings,
    format_welcome,
)
from tokoboros.tui.state_store import TUIStateSnapshot, TUIStateStore
from tokoboros.tui.theme import TOKOBOROS_THEME
from tokoboros.tui.widgets import (
    AgentMessage,
    ErrorCard,
    InputHistory,
    SidebarPanel,
    SlashMenu,
    SlashPalette,
    StarterPrompt,
    StatusStrip,
    TokoGlyph,
    TokoSplash,
    ToolCard,
    UserBubble,
    search_transcript,
    transcript_overflow,
)

TierName = Literal["lightning", "recommended", "enhanced", "power"]

# Below this width the sidebar collapses (CSS `#session-root.narrow #sidebar`),
# so the docked header takes over as the live-metrics surface. Shared by the
# resize handler and the metrics-routing in refresh_sidebar.
NARROW_WIDTH = 90

# Fallback model lists per provider, used by /model only when live discovery
# from the provider API is unavailable (offline / no /models endpoint).
_CURATED_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"],
    "openai-compatible": ["gpt-4.1-mini", "gpt-4.1"],
    "openrouter": ["anthropic/claude-3.5-sonnet", "openai/gpt-4.1"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "ollama": ["llama3.1:8b", "qwen2.5-coder:7b"],
}

# One-click suggestions shown on the empty first-run transcript — a discovery
# prompt, a dev task, and an everyday task to showcase breadth.
_STARTER_PROMPTS: tuple[str, ...] = (
    "What can you do?",
    "Summarize my recent git commits",
    "Organize my Downloads folder",
)

_GATEWAY_TOKEN: str = os.environ.get("TOKOBOROS_GATEWAY_API_TOKEN", "")
_URLOPEN_TIMEOUT_S: int = 10

_LEGACY_RICH_TAG_RE = re.compile(
    r"\[/?(?:"
    r"dim|green|cyan|yellow|red|magenta|bold|italic|underline|"
    r"b|i|u"
    r")(?:\s+[^\]]+)?\]",
    re.IGNORECASE,
)


def _sanitize_legacy_rich_markup(text: str) -> str:
    """Strip legacy Rich style tags from plain-text UI surfaces."""
    return _LEGACY_RICH_TAG_RE.sub("", text)


def _sanitize_agent_markdown(text: str) -> str:
    """Normalize model output for Markdown rendering in transcript views."""
    return _sanitize_legacy_rich_markup(text).strip()


def _event_input_summary(tool_input: Any) -> str:
    """Build a short one-line summary of a tool-call input payload."""
    if not isinstance(tool_input, dict) or not tool_input:
        return "running…"
    parts = []
    for key, value in list(tool_input.items())[:2]:
        text = _sanitize_legacy_rich_markup(str(value)).replace("\n", " ")
        if len(text) > 40:
            text = text[:39] + "…"
        parts.append(f"{key}={text}")
    return "  ".join(parts)


def _event_input_detail(tool_input: Any) -> str:
    """Build the full ``key: value`` request block shown when a tool card expands."""
    if not isinstance(tool_input, dict) or not tool_input:
        return ""
    lines = []
    for key, value in tool_input.items():
        lines.append(f"{key}: {_sanitize_legacy_rich_markup(str(value))}")
    return "\n".join(lines)


def _strip_metrics(info: dict[str, Any]) -> dict[str, Any]:
    """Copy ``info`` without the live metrics (model/tokens/cost).

    Used to keep the docked header brand+state only while the sidebar is the
    visible metrics surface, so model/tokens/cost never render in both at once.
    """
    return {key: value for key, value in info.items() if key not in ("model", "tokens", "cost")}


APP_CSS = """
Screen {
  layout: vertical;
  background: $background;
}


.mode-title {
  height: auto;
  padding: 0 2;
  margin: 1 0 0 0;
  color: $text-muted;
  text-style: bold;
}

/* --- Session screen ---------------------------------------------------- */
#toko-header {
  dock: top;
  height: 1;
  padding: 0 2;
  background: $surface;
  color: $foreground;
  content-align: left middle;
}

#status-strip {
  dock: top;
  height: 1;
  padding: 0 2;
  background: $background;
  color: $foreground;
  content-align: left middle;
}

#session-root {
  height: 1fr;
  width: 100%;
}

.toko-splash {
  height: auto;
  width: 100%;
  content-align: center top;
  padding: 1 0 0 0;
}

#transcript {
  height: 1fr;
  width: 1fr;
  padding: 1 2;
  background: $background;
}

#sidebar {
  width: 34;
  height: 1fr;
  padding: 1 2;
  background: $surface;
  color: $foreground;
}

#session-root.narrow #sidebar {
  display: none;
}

#composer {
  dock: bottom;
  margin: 0 2 1 2;
  border: round #30363d;
  background: $surface;
}

#composer:focus {
  border: round $primary;
}

/* --- Transcript message widgets --------------------------------------- */
.msg:hover {
  background: $surface-lighten-1;
}
.msg {
  height: auto;
  margin: 0 0 1 0;
  padding: 0 1;
}

.msg-user {
  border-left: thick $accent;
  color: $foreground;
}

.msg-agent {
  border-left: thick $primary;
}

.msg-error {
  border-left: thick $error;
  background: $surface;
}

.tool-card {
  height: auto;
  margin: 0 0 1 0;
  padding: 0;
  border-left: thick $accent;
  background: $surface;
}

.tool-card.done {
  border-left: thick $success;
}

.tool-card.failed {
  border-left: thick $error;
}

.tool-card CollapsibleTitle {
  background: $surface;
  color: $foreground;
  padding: 0 1;
}

.tool-card CollapsibleTitle:hover {
  background: $surface-lighten-1;
}

.tool-card Contents {
  padding: 0 1 1 2;
}

.tool-detail {
  height: auto;
  color: $text-muted;
}

.tool-response {
  margin: 1 0 0 0;
}

.system-line {
  height: auto;
  margin: 0 0 1 0;
  color: $text-muted;
}

.welcome-card {
  height: auto;
  margin: 1 0 1 0;
  padding: 0 1;
}

.starter-prompt {
  height: auto;
  margin: 0 0 1 2;
  padding: 0 1;
  border-left: thick $primary;
  background: $surface;
}

.starter-prompt:hover {
  background: $primary 20%;
}

#slash-menu {
  height: auto;
  max-height: 9;
  margin: 0 1;
  padding: 0 1;
  background: $surface;
  border: round $primary;
}

/* --- Approval modal ---------------------------------------------------- */
#approval-modal {
  align: center middle;
}

#approval-card {
  width: 76;
  height: auto;
  padding: 1 2;
  border: round $warning;
  background: $surface;
}

#approval-actions {
  margin-top: 1;
  height: auto;
}

/* --- Help modal -------------------------------------------------------- */
#help-modal {
  align: center middle;
}

#help-card {
  width: 64;
  height: auto;
  padding: 1 2;
  border: round $primary;
  background: $surface;
}

/* --- Authoring screen -------------------------------------------------- */
#authoring-root {
  height: 1fr;
  margin: 0 2;
}

#authoring-targets {
  border: round #30363d;
  width: 34%;
  background: $surface;
}

#authoring-editor {
  border: round #30363d;
  height: 1fr;
}

#authoring-patch {
  border: round #30363d;
  height: 1fr;
  background: $surface;
}

#authoring-actions {
  height: auto;
  margin: 1 2;
}

#authoring-status {
  margin: 0 2 1 2;
  color: $text-muted;
}

/* --- Setup wizard ------------------------------------------------------ */
#setup-progress {
  height: auto;
  padding: 0 2;
  color: $text-muted;
}

#setup-main {
  height: 1fr;
  margin: 1 2 0 2;
}

#setup-rail {
  width: 26;
  height: 1fr;
  border: round #30363d;
  background: $surface;
  padding: 1 1;
  margin: 0 1 0 0;
}

#setup-content {
  width: 1fr;
  height: 1fr;
}

#setup-body {
  border: round #30363d;
  background: $surface;
  padding: 1 2;
  height: auto;
  margin: 0 0 1 0;
}

#setup-options {
  height: 1fr;
  padding: 0 1;
}

#setup-options Button {
  margin: 0 0 1 0;
  width: 100%;
}

#setup-review {
  height: 1fr;
  border: round #30363d;
  background: $surface;
  padding: 1 2;
  display: none;
}

#setup-nav {
  margin: 1 2 1 2;
  height: auto;
}

/* --- Snapshot screens -------------------------------------------------- */
#snapshot-body {
  height: 1fr;
  padding: 1 2;
}
"""


class PermissionApprovalModal(ModalScreen[PermissionDecision]):
    """Interactive approval flow for risky runtime operations."""

    BINDINGS = [
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__(id="approval-modal")
        self._request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-card"):
            yield Static("Approval Required")
            yield Static(f"Operation: {self._request.operation}")
            yield Static(f"Tool: {self._request.tool}")
            yield Static(f"Target: {self._request.target}")
            yield Static(f"Policy: {self._request.policy_summary}")
            yield Static(f"Reason: {self._request.reason}")
            yield Input(
                placeholder="Rationale (required for deny, optional otherwise)",
                id="approval-rationale",
            )
            with Horizontal(id="approval-actions"):
                yield Button("Approve", id="approval-approve", variant="success")
                yield Button("Allow Once", id="approval-once", variant="primary")
                yield Button("Deny", id="approval-deny", variant="error")

    def action_deny(self) -> None:
        rationale = self.query_one("#approval-rationale", Input).value.strip()
        self.dismiss(PermissionDecision(action="deny", rationale=rationale or "Denied from modal"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        rationale = self.query_one("#approval-rationale", Input).value.strip()
        if event.button.id == "approval-approve":
            self.dismiss(PermissionDecision(action="approve", rationale=rationale))
        elif event.button.id == "approval-once":
            self.dismiss(PermissionDecision(action="allow_once", rationale=rationale))
        elif event.button.id == "approval-deny":
            self.dismiss(
                PermissionDecision(action="deny", rationale=rationale or "Denied from modal")
            )


_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("Enter", "Send the prompt in the composer"),
    ("Ctrl+1 … Ctrl+7", "Switch mode (Session / Ops / Memory / …)"),
    ("Ctrl+P", "Command palette"),
    ("Ctrl+R", "Refresh the current screen"),
    ("Ctrl+C", "Interrupt: stop polling, dismiss approvals"),
    ("Ctrl+Y", "Copy last response to clipboard"),
    ("Ctrl+Q", "Quit Tokoboros"),
    ("?", "Toggle this help"),
    ("Escape", "Close a dialog / deny an approval"),
    ("Click message", "Copy that message to clipboard"),
    ("Ctrl+V / right-click", "Paste into the composer"),
)

# Slash commands typed into the composer. Each entry is (usage, description).
# Kept as a single source of truth: the help modal renders it and
# ``TokoborosTextualApp.handle_slash_command`` dispatches on the verbs here.
_SLASH_HELP: tuple[tuple[str, str], ...] = (
    ("/help", "Show this help"),
    ("/clear", "Clear the conversation context and transcript"),
    ("/compact", "Compress older context to reclaim tokens"),
    ("/new", "Start a fresh conversation (alias of /clear)"),
    ("/sessions", "List recent sessions (resume with `tokoboros -r`)"),
    ("/model [name]", "Show or switch the active model"),
    ("/search <query>", "Find text in this conversation's transcript"),
    ("/memory <query>", "Search long-term memory"),
    ("/remember <fact>", "Save a fact to long-term memory"),
    ("/forget <query>", "Remove matching facts from long-term memory"),
    ("/copy", "Copy the last response to clipboard"),
    ("/plan <task>", "Plan-first: outline before acting"),
    ("/deep-research <q>", "Thorough, multi-source research answer"),
    ("/settings", "Open the Settings screen"),
    ("/setup", "Open the Setup wizard"),
    ("/quit", "Quit Tokoboros (alias /exit)"),
)


class HelpModal(ModalScreen[None]):
    """Keybinding reference overlay."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        rows = Text()
        for key, description in _HELP_ROWS:
            rows.append(f"  {key:<18}", style=f"bold {theme.PRIMARY}")
            rows.append(f"{description}\n", style=theme.FOREGROUND)

        slash = Text()
        for usage, description in _SLASH_HELP:
            slash.append(f"  {usage:<20}", style=f"bold {theme.PRIMARY}")
            slash.append(f"{description}\n", style=theme.FOREGROUND)

        with Vertical(id="help-card"):
            yield Static(Text("Keyboard shortcuts", style=f"bold {theme.ACCENT}"))
            yield Static(rows)
            yield Static(Text("Slash commands", style=f"bold {theme.ACCENT}"))
            yield Static(slash)
            yield Button("Close", id="help-close", variant="primary")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)


class SetupWizardController:
    """Stateful setup flow controller used by CLI/TUI onboarding surfaces."""

    def __init__(self, service: OnboardingService, persisted_resume_json: str = "{}") -> None:
        self.service = service
        self.environment = service.detect_environment()
        self.state = service.resume_state()
        self.selected_tier: TierName = "lightning"
        self.answers: dict[str, Any] = {}
        self.current_step: SetupStep = "welcome"
        self._steps: list[SetupStep] = ["welcome", "provider", "safety", "finish"]
        self.can_resume = False
        # The pasted API key is held in memory only (never serialized to the resume
        # blob); on finish it is persisted to the secret store + process env.
        self.pasted_key: str | None = None

        if self.state.selected_tier in {"lightning", "recommended", "enhanced", "power"}:
            self.selected_tier = self.state.selected_tier  # type: ignore[assignment]

        self._apply_tier(self.selected_tier)
        self._hydrate_resume_blob(persisted_resume_json)

    def _hydrate_resume_blob(self, persisted_resume_json: str) -> None:
        try:
            payload = json.loads(persisted_resume_json or "{}")
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            return

        tier = payload.get("selected_tier")
        if tier in {"lightning", "recommended", "enhanced", "power"}:
            self.select_tier(tier)

        answers = payload.get("answers")
        if isinstance(answers, dict):
            self.answers = dict(answers)

        step = payload.get("current_step")
        if step in self._steps:
            self.current_step = step

        self.can_resume = bool(self.answers) or bool(self.state.completed_steps)

    def _apply_tier(self, tier: TierName) -> None:
        plan = self.service.build_tier_plan(tier, self.answers)
        steps = plan.get("steps", ["welcome", "provider", "safety", "finish"])
        self._steps = [
            step
            for step in steps
            if step
            in {"welcome", "provider", "safety", "memory", "integrations", "advanced", "finish"}
        ]
        if self.current_step not in self._steps:
            self.current_step = self._steps[0]

    @property
    def steps(self) -> tuple[SetupStep, ...]:
        return tuple(self._steps)

    def select_tier(self, tier: TierName) -> None:
        self.selected_tier = tier
        self._apply_tier(tier)
        self.answers["tier"] = tier

    def step_index(self) -> int:
        return self._steps.index(self.current_step)

    def set_answer(self, key: str, value: Any) -> None:
        self.answers[key] = value
        self.can_resume = True

    # --- API-key handling (provider step) --------------------------------
    def set_api_key(self, key: str | None) -> None:
        self.pasted_key = (key or "").strip() or None

    def provider_kind(self) -> str:
        provider = self.answers.get("provider")
        return str(provider.get("kind", "")).lower() if isinstance(provider, dict) else ""

    def provider_is_keyless(self) -> bool:
        return self.provider_kind() == "ollama"

    def provider_key_status(self) -> tuple[bool, str]:
        """Validate the selected provider + (pasted key or env var). (ok, message)."""
        kind = self.provider_kind()
        if not kind:
            return False, "Pick a provider above."
        provider = self.answers.get("provider", {})
        env_name = (
            str(
                (provider.get("api_key_env") if isinstance(provider, dict) else "")
                or default_api_key_env_for(kind)
                or ""
            ).strip()
            or None
        )
        return self.service.validate_provider(
            kind=kind, api_key=self.pasted_key, api_key_env=env_name
        )

    def preselect_provider(self) -> None:
        """On first entry to the provider step, default to whatever the env offers."""
        if isinstance(self.answers.get("provider"), dict):
            return
        env = self.environment
        if env.get("ollama_cli"):
            self.answers["provider"] = {
                "kind": "ollama",
                "model": "llama3.1:8b",
                "base_url": "http://127.0.0.1:11434/v1",
            }
        elif env.get("anthropic_env"):
            self.answers["provider"] = {
                "kind": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key_env": "ANTHROPIC_API_KEY",
            }
        elif env.get("openai_env"):
            self.answers["provider"] = {
                "kind": "openai-compatible",
                "model": "gpt-4.1-mini",
                "api_key_env": "OPENAI_API_KEY",
            }

    def _persist_api_key_if_pasted(self) -> None:
        """Persist a pasted key to the secret store + process env; set api_key_ref."""
        key = (self.pasted_key or "").strip()
        provider = self.answers.get("provider")
        if not key or not isinstance(provider, dict) or self.provider_is_keyless():
            return
        env_name = str(
            provider.get("api_key_env") or default_api_key_env_for(self.provider_kind()) or ""
        ).strip()
        if not env_name:
            return
        os.environ[env_name] = key  # immediate boot + this process
        provider["api_key_ref"] = env_name
        try:
            create_default_secret_store().set(env_name, key)
        except Exception:
            pass  # keyring + passphrase unavailable → env-only for this session

    def back(self) -> None:
        idx = self.step_index()
        if idx > 0:
            self.current_step = self._steps[idx - 1]

    def next(self) -> None:
        idx = self.step_index()
        if idx < len(self._steps) - 1:
            self.current_step = self._steps[idx + 1]

    def skip(self) -> None:
        self.next()

    def serialize_resume(self) -> str:
        return json.dumps(
            {
                "selected_tier": self.selected_tier,
                "current_step": self.current_step,
                "answers": self.answers,
            }
        )

    def finish(self) -> dict[str, Any]:
        self._persist_api_key_if_pasted()
        payload = self.service.build_tier_plan(self.selected_tier, self.answers)
        applied = self.service.apply_setup(payload)
        return applied.model_dump(mode="python")


_SETUP_STEP_COPY: dict[str, tuple[str, str]] = {
    "welcome": (
        "Choose a capability tier",
        "Tiers set the default tools, memory, and integrations. You can change "
        "everything later from Settings.",
    ),
    "provider": (
        "Pick a model provider",
        "Where completions run. API keys are resolved lazily at call time — "
        "nothing is requested now.",
    ),
    "safety": (
        "Set the permission tier",
        "Controls how risky filesystem/shell operations are gated at runtime.",
    ),
    "memory": (
        "Configure memory",
        "Session history and long-term semantic recall. Off keeps everything in-context only.",
    ),
    "integrations": (
        "Connect integrations (optional)",
        "Enable now or skip and connect later. Enabling only records intent; "
        "credentials are configured separately.",
    ),
    "finish": (
        "Review and finish",
        "Apply the selected setup and boot the runtime.",
    ),
}

_SETUP_STEP_OPTIONS: dict[str, list[tuple[str, str, str]]] = {
    "welcome": [
        ("setup-tier-lightning", "Lightning", "fastest, minimal capabilities"),
        ("setup-tier-recommended", "Recommended", "balanced defaults for most users"),
        ("setup-tier-enhanced", "Enhanced", "more tools and memory"),
        ("setup-tier-power", "Power", "everything enabled"),
    ],
    "provider": [
        ("setup-provider-anthropic", "Anthropic", "Claude models (ANTHROPIC_API_KEY)"),
        ("setup-provider-openai", "OpenAI-compatible", "GPT / compatible endpoints"),
        ("setup-provider-ollama", "Ollama", "local models via Ollama"),
    ],
    "safety": [
        ("setup-perm-sandbox", "Sandbox", "blocks risky operations"),
        ("setup-perm-guided", "Guided", "asks for approval on risky ops"),
        ("setup-perm-unrestricted", "Unrestricted", "no gating — use with care"),
    ],
    "memory": [
        ("setup-memory-on", "Memory on", "session history + semantic recall"),
        ("setup-memory-off", "Memory off", "in-context only, no persistence"),
    ],
    "integrations": [
        ("setup-google-enable", "Google: enable", "record intent to connect Google"),
        ("setup-google-later", "Google: later", "skip for now"),
        ("setup-slack-enable", "Slack: enable", "record intent to connect Slack"),
        ("setup-slack-later", "Slack: later", "skip for now"),
        ("setup-notion-enable", "Notion: enable", "record intent to connect Notion"),
        ("setup-notion-later", "Notion: later", "skip for now"),
    ],
    "finish": [],
}


class SetupScreen(Screen):
    """Tiered onboarding wizard with resumable state."""

    def compose(self) -> ComposeResult:
        yield Static("Tokoboros Setup", classes="mode-title")
        yield Static("", id="setup-progress")
        with Horizontal(id="setup-main"):
            yield Static("", id="setup-rail")
            with Vertical(id="setup-content"):
                yield Static("Preparing setup…", id="setup-body")
                with VerticalScroll(id="setup-options"):
                    # All option buttons are created once; visibility is toggled
                    # per step in render_step to avoid dynamic mount/remove id
                    # collisions.
                    for options in _SETUP_STEP_OPTIONS.values():
                        for button_id, label, desc in options:
                            yield Button(f"  {label} — {desc}", id=button_id)
                yield Input(
                    placeholder="Paste your API key (or leave blank to use the env var)",
                    password=True,
                    id="setup-api-key",
                )
                yield Static("", id="setup-key-status")
                yield Static("", id="setup-review")
        with Horizontal(id="setup-nav"):
            yield Button("← Back", id="setup-back")
            yield Button("Skip for now", id="setup-skip")
            yield Button("Next →", id="setup-next", variant="primary")
            yield Button("Finish", id="setup-finish", variant="success")

    def on_mount(self) -> None:
        # Create the wizard on demand so Setup is usable any time (Ctrl+7),
        # not only during first-run onboarding.
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.ensure_setup_controller()
        self.render_step()

    def _controller(self) -> SetupWizardController | None:
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.ensure_setup_controller()
            return app.setup_controller
        return None

    _STEP_LABELS = {
        "welcome": "Capability tier",
        "provider": "Model provider",
        "safety": "Permissions",
        "memory": "Memory",
        "integrations": "Integrations",
        "advanced": "Advanced",
        "finish": "Review & finish",
    }

    def _render_rail(self, controller: SetupWizardController) -> None:
        current = controller.current_step
        rail = Text()
        rail.append("Steps\n\n", style=f"bold {theme.ACCENT}")
        for step in controller.steps:
            label = self._STEP_LABELS.get(step, step.title())
            if step == current:
                rail.append("▶ ", style=f"bold {theme.PRIMARY}")
                rail.append(f"{label}\n", style=f"bold {theme.FOREGROUND}")
            else:
                rail.append("  ")
                rail.append(f"{label}\n", style=theme.MUTED)
        self.query_one("#setup-rail", Static).update(rail)

    def _render_review(self, controller: SetupWizardController) -> None:
        answers = controller.answers
        provider = answers.get("provider", {})
        provider_kind = provider.get("kind", "anthropic") if isinstance(provider, dict) else "—"
        provider_model = provider.get("model", "") if isinstance(provider, dict) else ""
        memory = answers.get("memory", {})
        memory_on = isinstance(memory, dict) and bool(memory.get("enable_semantic_memory"))
        integrations = answers.get("integrations", {})
        enabled = [
            name
            for name, entry in integrations.items()
            if isinstance(entry, dict) and entry.get("enabled")
        ]

        review = Text()
        review.append("Review your choices\n\n", style=f"bold {theme.ACCENT}")

        def row(label: str, value: str) -> None:
            review.append(f"  {label:<16}", style=theme.MUTED)
            review.append(f"{value}\n", style=theme.FOREGROUND)

        row("Tier", controller.selected_tier)
        row("Provider", f"{provider_kind} {provider_model}".strip())
        row("Permissions", str(answers.get("permission_tier", "guided")))
        row("Memory", "on" if memory_on else "off")
        row("Integrations", ", ".join(enabled) if enabled else "none")
        review.append("\nPress ", style=theme.MUTED)
        review.append("Finish", style=f"bold {theme.SUCCESS}")
        review.append(" to apply and boot the runtime.", style=theme.MUTED)
        self.query_one("#setup-review", Static).update(review)

    def _option_selected(self, button_id: str, controller: SetupWizardController) -> bool:
        answers = controller.answers
        if button_id.startswith("setup-tier-"):
            return controller.selected_tier == button_id.rsplit("-", 1)[-1]
        if button_id.startswith("setup-provider-"):
            kind_map = {
                "anthropic": "anthropic",
                "openai": "openai-compatible",
                "ollama": "ollama",
            }
            wanted = kind_map.get(button_id.rsplit("-", 1)[-1])
            provider = answers.get("provider", {})
            return isinstance(provider, dict) and provider.get("kind") == wanted
        if button_id.startswith("setup-perm-"):
            return answers.get("permission_tier") == button_id.rsplit("-", 1)[-1]
        memory = answers.get("memory")
        if button_id == "setup-memory-on":
            return isinstance(memory, dict) and bool(memory.get("enable_semantic_memory"))
        if button_id == "setup-memory-off":
            return isinstance(memory, dict) and not memory.get("enable_semantic_memory")
        for service in ("google", "slack", "notion"):
            if button_id.startswith(f"setup-{service}-"):
                entry = answers.get("integrations", {}).get(service)
                if not isinstance(entry, dict):
                    return False
                enabled = bool(entry.get("enabled"))
                return enabled if button_id.endswith("enable") else not enabled
        return False

    def _sync_api_key_from_input(self, controller: SetupWizardController) -> None:
        """Keep controller key state aligned with the current input widget value."""
        try:
            key_value = self.query_one("#setup-api-key", Input).value
        except Exception:
            return
        controller.set_api_key(key_value)

    def render_step(self) -> None:
        controller = self._controller()
        if not controller:
            return

        step = controller.current_step
        if step == "provider":
            controller.preselect_provider()
        idx = controller.step_index() + 1
        total = len(controller.steps)

        dots = " ".join("●" if i < idx else "○" for i in range(total))
        self.query_one("#setup-progress", Static).update(
            Text.assemble(
                (f"Step {idx}/{total}  ", theme.MUTED),
                (dots, theme.PRIMARY),
            )
        )

        self._render_rail(controller)

        title, description = _SETUP_STEP_COPY.get(step, (step.title(), ""))
        body = self.query_one("#setup-body", Static)
        body.update(
            Text.assemble(
                (f"{title}\n", f"bold {theme.FOREGROUND}"),
                (f"{description}\n", theme.MUTED),
                ("\nSelected tier: ", theme.MUTED),
                (controller.selected_tier, theme.ACCENT),
            )
        )

        is_finish = step == "finish"
        current_ids = {button_id for button_id, _, _ in _SETUP_STEP_OPTIONS.get(step, [])}
        for options in _SETUP_STEP_OPTIONS.values():
            for button_id, label, desc in options:
                button = self.query_one(f"#{button_id}", Button)
                visible = button_id in current_ids
                button.display = visible
                if visible:
                    selected = self._option_selected(button_id, controller)
                    button.label = f"{'✓ ' if selected else '  '}{label} — {desc}"
                    button.variant = "primary" if selected else "default"

        # API-key entry: only on the provider step, and only for a key-requiring
        # provider whose key isn't already resolvable.
        key_input = self.query_one("#setup-api-key", Input)
        key_status = self.query_one("#setup-key-status", Static)
        show_key = step == "provider" and not controller.provider_is_keyless()
        key_input.display = show_key
        key_status.display = show_key
        if show_key:
            self._sync_api_key_from_input(controller)
            ok, message = controller.provider_key_status()
            key_status.update(
                Text(
                    ("✓ " if ok else "⚠ ") + message.splitlines()[0],
                    style=theme.SUCCESS if ok else theme.WARNING,
                )
            )

        # The options list and the review panel are mutually exclusive: the final
        # step swaps the (empty) option list for a summary of every choice.
        self.query_one("#setup-options").display = not is_finish
        review = self.query_one("#setup-review", Static)
        review.display = is_finish
        if is_finish:
            self._render_review(controller)

        self.query_one("#setup-back", Button).disabled = controller.step_index() == 0
        self.query_one("#setup-next", Button).disabled = is_finish
        self.query_one("#setup-skip", Button).disabled = is_finish
        self.query_one("#setup-finish", Button).disabled = not is_finish

    def on_button_pressed(self, event: Button.Pressed) -> None:
        controller = self._controller()
        if not controller:
            return

        # Users often click Next/Finish without pressing Enter in the key field.
        # Sync whatever is currently typed so validation/persistence sees it.
        self._sync_api_key_from_input(controller)

        button_id = event.button.id or ""

        tier_map = {
            "setup-tier-lightning": "lightning",
            "setup-tier-recommended": "recommended",
            "setup-tier-enhanced": "enhanced",
            "setup-tier-power": "power",
        }
        if button_id in tier_map:
            controller.select_tier(tier_map[button_id])

        provider_map: dict[str, dict[str, str]] = {
            "setup-provider-anthropic": {
                "kind": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "setup-provider-openai": {
                "kind": "openai-compatible",
                "model": "gpt-4.1-mini",
                "api_key_env": "OPENAI_API_KEY",
            },
            "setup-provider-ollama": {
                "kind": "ollama",
                "model": "llama3.1:8b",
                "base_url": "http://127.0.0.1:11434/v1",
            },
        }
        if button_id in provider_map:
            controller.set_answer("provider", provider_map[button_id])

        perm_map = {
            "setup-perm-sandbox": "sandbox",
            "setup-perm-guided": "guided",
            "setup-perm-unrestricted": "unrestricted",
        }
        if button_id in perm_map:
            controller.set_answer("permission_tier", perm_map[button_id])

        if button_id == "setup-memory-on":
            controller.set_answer(
                "memory",
                {
                    "enable_session_history": True,
                    "enable_semantic_memory": True,
                },
            )
        elif button_id == "setup-memory-off":
            controller.set_answer(
                "memory",
                {
                    "enable_session_history": False,
                    "enable_semantic_memory": False,
                },
            )

        if button_id.startswith("setup-google-"):
            integrations = dict(controller.answers.get("integrations", {}))
            integrations["google"] = {"enabled": button_id.endswith("enable"), "configured": False}
            controller.set_answer("integrations", integrations)
        if button_id.startswith("setup-slack-"):
            integrations = dict(controller.answers.get("integrations", {}))
            integrations["slack"] = {"enabled": button_id.endswith("enable"), "configured": False}
            controller.set_answer("integrations", integrations)
        if button_id.startswith("setup-notion-"):
            integrations = dict(controller.answers.get("integrations", {}))
            integrations["notion"] = {"enabled": button_id.endswith("enable"), "configured": False}
            controller.set_answer("integrations", integrations)

        if button_id == "setup-back":
            controller.back()
        elif button_id == "setup-next":
            controller.next()
        elif button_id == "setup-skip":
            controller.skip()
        elif button_id == "setup-finish":
            ok, message = controller.provider_key_status()
            if not ok and not controller.provider_is_keyless():
                self.app.notify(f"Can't finish — {message.splitlines()[0]}", timeout=6)
                controller.current_step = "provider"
                self.render_step()
                return
            app = self.app
            if isinstance(app, TokoborosTextualApp):
                app.complete_setup()
            return

        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.persist_setup_resume()
        self.render_step()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "setup-api-key":
            return
        controller = self._controller()
        if controller is not None:
            controller.set_api_key(event.value)
            self.render_step()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "setup-api-key":
            return
        controller = self._controller()
        if controller is not None:
            controller.set_api_key(event.value)
        self.render_step()


class Composer(Input):
    """The prompt input, augmented with a slash-command autocomplete menu.

    Slash logic lives in the pure ``SlashPalette``; this subclass only routes keys
    and refreshes the attached ``SlashMenu`` widget.
    """

    def __init__(self, *args: Any, commands: tuple[tuple[str, str], ...], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.palette = SlashPalette(commands)
        self.history = InputHistory()
        self._menu: SlashMenu | None = None

    def _set_value(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)

    def attach_menu(self, menu: SlashMenu) -> None:
        self._menu = menu

    def refresh_menu(self) -> None:
        self.palette.update(self.value)
        if self._menu is not None:
            self._menu.show(self.palette)

    def complete_selected(self) -> bool:
        """Fill the input with the selected command verb (+ trailing space)."""
        verb = self.palette.selected_verb()
        if verb is None:
            return False
        self.value = f"{verb} "
        self.cursor_position = len(self.value)
        self.refresh_menu()  # the trailing space hides the menu
        return True

    def hide_menu(self) -> None:
        self.palette.visible = False
        if self._menu is not None:
            self._menu.display = False

    async def on_key(self, event: events.Key) -> None:
        if self.palette.visible:
            if event.key == "down":
                self.palette.move(1)
            elif event.key == "up":
                self.palette.move(-1)
            elif event.key == "tab":
                self.complete_selected()
                event.stop()
                event.prevent_default()
                return
            elif event.key == "escape":
                self.hide_menu()
                event.stop()
                event.prevent_default()
                return
            else:
                return
            if self._menu is not None:
                self._menu.show(self.palette)
            event.stop()
            event.prevent_default()
            return

        # Menu hidden → ↑/↓ recall previously-submitted prompts.
        if event.key in ("up", "down"):
            recalled = (
                self.history.prev(self.value)
                if event.key == "up"
                else self.history.next(self.value)
            )
            if recalled is not None:
                self._set_value(recalled)
                event.stop()
                event.prevent_default()


class SessionScreen(Screen):
    """Primary chat workspace: scrollable transcript, live sidebar, composer."""

    # Cap the number of mounted transcript widgets so a very long session does
    # not grow Textual's widget tree without bound (memory + relayout cost).
    # Oldest widgets are pruned; recent context (and any in-flight tool cards,
    # which are always newest) stay mounted. /search still covers older text
    # while it is on screen; full history lives in the SessionStore regardless.
    MAX_TRANSCRIPT_WIDGETS = 400

    def __init__(self) -> None:
        super().__init__()
        self._tool_cards: dict[str, list[ToolCard]] = {}
        self._welcome_widgets: list[Widget] = []
        self._streaming_bubble: AgentMessage | None = None
        self._streaming_text: str = ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="session-root"):
            yield VerticalScroll(id="transcript")
            yield SidebarPanel(id="sidebar")
        menu = SlashMenu(id="slash-menu")
        menu.display = False
        composer = Composer(
            commands=_SLASH_HELP,
            placeholder="Send a prompt  ·  / for commands  ·  Enter to submit  ·  ? for help",
            id="composer",
        )
        composer.attach_menu(menu)
        yield menu
        yield composer

    def on_input_changed(self, event: Input.Changed) -> None:
        if isinstance(event.input, Composer):
            event.input.refresh_menu()

    def set_toko_state(self, state: str) -> None:
        """Drive the brand header glyph; no-op if it is not mounted yet."""
        try:
            self.query_one("#toko-header", TokoGlyph).set_state(state)
        except Exception:
            return

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            self.query_one("#composer", Input).value = app.hydrated_draft
            app.refresh_sidebar()
        self._append(TokoSplash(), scroll=False)
        self._mount_system_line("Booting runtime…")

    # --- mounting helpers -------------------------------------------------
    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _at_bottom(self) -> bool:
        """True when the transcript is scrolled to (or near) the bottom.

        Defensive: returns False off a real Textual loop (e.g. unit-test proxy)
        so callers simply skip auto-follow rather than raising.
        """
        try:
            transcript = self._transcript()
            return transcript.scroll_offset.y >= transcript.max_scroll_y - 1
        except Exception:
            return False

    def _scroll_to_tail(self) -> None:
        """Jump the transcript to the latest content (no-op off a real loop)."""
        try:
            self._transcript().scroll_end(animate=False)
        except Exception:
            pass

    def _append(self, widget: Widget, *, scroll: bool = True) -> None:
        transcript = self._transcript()
        transcript.mount(widget)
        self._prune_transcript()
        if scroll:
            transcript.scroll_end(animate=False)

    def _prune_transcript(self) -> None:
        """Drop the oldest transcript widgets once the cap is exceeded."""
        transcript = self._transcript()
        children = list(transcript.children)
        overflow = transcript_overflow(len(children), self.MAX_TRANSCRIPT_WIDGETS)
        if overflow <= 0:
            return
        for child in children[:overflow]:
            if isinstance(child, ToolCard):
                self._forget_tool_card(child)
            child.remove()

    def _forget_tool_card(self, card: ToolCard) -> None:
        """Drop a pruned tool card from the id→cards index (avoid stale refs)."""
        for name, cards in list(self._tool_cards.items()):
            if card in cards:
                cards.remove(card)
            if not cards:
                self._tool_cards.pop(name, None)

    def _mount_system_line(self, text: str, *, style: str = theme.MUTED) -> None:
        # Boot/system lines must not scroll the boot splash off-screen; real
        # messages (user/agent) still scroll into view via the default path.
        self._append(Static(Text(text, style=style), classes="system-line"), scroll=False)

    def clear_transcript(self) -> None:
        """Remove every rendered message/card from the transcript (used by /clear)."""
        transcript = self._transcript()
        for child in list(transcript.children):
            child.remove()
        self._tool_cards.clear()
        self._welcome_widgets = []

    def collect_transcript(self) -> list[tuple[str, str]]:
        """Snapshot the rendered conversation as (role, text) pairs for search."""
        entries: list[tuple[str, str]] = []
        for child in self._transcript().children:
            if isinstance(child, UserBubble):
                entries.append(("you", child.message_text))
            elif isinstance(child, AgentMessage):
                entries.append(("tokoboros", child.message_text))
        return entries

    def show_welcome(self, starters: tuple[str, ...]) -> None:
        """Render the first-run greeting + clickable starter-prompt chips."""
        card = Static(format_welcome(), classes="welcome-card")
        self._append(card, scroll=False)
        self._welcome_widgets = [card]
        for prompt in starters:
            chip = StarterPrompt(prompt)
            self._append(chip, scroll=False)
            self._welcome_widgets.append(chip)

    def clear_welcome(self) -> None:
        """Remove the welcome card + starter chips (on the first real message)."""
        for widget in self._welcome_widgets:
            try:
                widget.remove()
            except Exception:
                pass
        self._welcome_widgets = []

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "composer":
            return
        composer = event.input
        # If the slash menu is open and the user is still mid-verb, Enter completes
        # the selected command (and stays put) instead of submitting a partial verb.
        if isinstance(composer, Composer) and composer.palette.visible:
            verb = composer.palette.selected_verb()
            if verb is not None and event.value.strip() != verb:
                composer.complete_selected()
                return
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt:
            return
        if isinstance(composer, Composer):
            composer.history.add(prompt)
        app = self.app
        if not isinstance(app, TokoborosTextualApp):
            return
        self.clear_welcome()
        if prompt.startswith("/"):
            app.handle_slash_command(prompt)
            return
        self.set_toko_state("thinking")
        app.submit_prompt(prompt)

    def on_resize(self, _event: object) -> None:
        try:
            root = self.query_one("#session-root", Horizontal)
        except Exception:
            return
        root.set_class(self.size.width < NARROW_WIDTH, "narrow")
        # The sidebar just appeared/disappeared — re-route live metrics between
        # it and the docked header so they never show in both places at once.
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.refresh_sidebar()

    # --- transcript rendering --------------------------------------------
    def append_user_prompt(self, text: str) -> None:
        self._append(UserBubble(text))

    def render_assistant_text(self, text: str) -> None:
        cleaned = _sanitize_agent_markdown(text)
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            if cleaned and app.last_agent_text == cleaned:
                return
            app.last_agent_text = cleaned
        self._append(AgentMessage(cleaned))

    def append_agent_response(self, text: str) -> None:
        """Backward-compatible alias retained for callers/tests."""
        self.render_assistant_text(text)

    def append_error(self, message: str) -> None:
        self._append(ErrorCard(_sanitize_legacy_rich_markup(message)))

    def finalize_streaming_bubble(self) -> None:
        """Called when a turn ends — commit accumulated text and clear streaming state."""
        if self._streaming_bubble is not None:
            app = self.app
            # Duck-type: update last_agent_text if the app tracks it.
            if hasattr(app, "last_agent_text"):
                app.last_agent_text = self._streaming_text
        self._streaming_bubble = None
        self._streaming_text = ""

    def reset_streaming_state(self) -> None:
        """Called at turn start — discard any leftover streaming state."""
        self._streaming_bubble = None
        self._streaming_text = ""

    def ingest_runtime_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "text.stream.delta":
            delta = str(payload.get("text", ""))
            if not delta:
                return
            app = self.app
            # Duck-type: notify the app that streaming text arrived this turn.
            mark = getattr(app, "mark_turn_streamed", None)
            if callable(mark):
                mark()
            if self._streaming_bubble is None:
                # First delta — create the streaming widget and mount it
                # (_append scrolls it into view).
                self._streaming_text = delta
                self._streaming_bubble = AgentMessage(delta)
                self._append(self._streaming_bubble)
            else:
                # Subsequent deltas — re-render in-place, then follow the tail
                # so the growing reply stays visible (unless the user scrolled
                # up to read history, in which case we leave them be).
                follow = self._at_bottom()
                self._streaming_text += delta
                self._streaming_bubble.stream_delta(self._streaming_text)
                if follow:
                    self._scroll_to_tail()
            return

        if event_type == "tool.call.started":
            name = str(payload.get("name", "tool"))
            # Seal the in-flight text bubble so this tool card mounts *below* it
            # and any following narration starts a fresh bubble — keeps text and
            # tool calls in chronological order instead of batching cards at the end.
            self.finalize_streaming_bubble()
            card = ToolCard(
                name,
                _event_input_summary(payload.get("input")),
                _event_input_detail(payload.get("input")),
            )
            self._tool_cards.setdefault(name, []).append(card)
            self._append(card)
            return

        if event_type == "tool.call.completed":
            name = str(payload.get("name", "tool"))
            preview = _sanitize_legacy_rich_markup(str(payload.get("result_preview", "")))
            cards = self._tool_cards.get(name)
            if cards:
                card = cards.pop(0)
                if _looks_like_error(preview):
                    card.mark_error(preview)
                else:
                    card.mark_completed(preview)
            return

        if event_type == "boot.progress":
            self._mount_system_line(
                f"Boot {payload.get('index', '?')}/{payload.get('total', '?')}: "
                f"{payload.get('message', 'booting')}"
            )
            return

        if event_type == "boot.complete":
            self._mount_system_line("Runtime ready.", style=theme.SUCCESS)
            return

        if event_type == "boot.failed":
            self.append_error(f"Runtime boot failed: {payload.get('error', 'unknown')}")
            return

        if event_type == "gateway.connect.completed":
            self._mount_system_line(
                f"Gateway connected: {payload.get('base_url', '')}", style=theme.SUCCESS
            )
            return

        if event_type == "gateway.events.error":
            self.append_error(f"Gateway events error: {payload.get('error', 'unknown')}")
            return

        # thinking / usage / session / job / permission events only drive the
        # sidebar status, which the app refreshes after each event.

    def on_key(self, event: events.Key) -> None:
        """Intercept Esc during an active turn to offer a graceful interrupt."""
        if event.key != "escape":
            return
        app = self.app
        # Duck-type: only act when the app tracks a chat thread + can interrupt.
        thread = getattr(app, "_chat_thread", None)
        if thread and thread.is_alive():
            interrupt = getattr(app, "action_interrupt", None)
            if callable(interrupt):
                interrupt()
                event.stop()


class SnapshotScreen(Screen):
    """Generic mode screen that renders snapshots from the active agent session."""

    title_text = "Mode"

    def compose(self) -> ComposeResult:
        yield Static(self.title_text, classes="mode-title")
        with VerticalScroll():
            yield Static("Waiting for runtime…", id="snapshot-body")

    def on_mount(self) -> None:
        app = self.app
        if hasattr(app, "active_agent"):
            self.refresh_from_agent(app.active_agent())

    def refresh_from_agent(self, agent: AgentSession | None) -> None:
        # Subclasses such as AuthoringScreen replace the snapshot layout and have
        # no #snapshot-body. Textual still runs this inherited on_mount for them
        # (it invokes every on_mount in the MRO), and ^r refresh routes here too,
        # so a missing body must be a no-op rather than a NoMatches crash.
        body_matches = self.query("#snapshot-body")
        if not body_matches:
            return
        body = body_matches.first(Static)
        if not agent:
            app = self.app
            if hasattr(app, "runtime_status_text"):
                body.update(app.runtime_status_text())
            else:
                body.update("Runtime is not ready yet.")
            return

        body.update(self._build_snapshot(agent))

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        return "No snapshot configured"


def _mcp_to_dict(mcp: Any) -> dict[str, Any]:
    return {
        "server_count": mcp.server_count,
        "total_tool_count": mcp.total_tool_count,
        "servers": [asdict(srv) for srv in mcp.servers],
    }


class OpsScreen(SnapshotScreen):
    title_text = "Ops"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        jobs = [asdict(job) for job in agent.snapshot_active_jobs()]
        failures = [asdict(job) for job in agent.snapshot_failed_jobs(limit=10)]
        mcp = _mcp_to_dict(agent.snapshot_mcp_inventory())
        events = [asdict(evt) for evt in agent.snapshot_runtime_events(limit=10)]
        usage = agent.snapshot_usage()
        subagents = [asdict(report) for report in agent.query_subagent_reports(top_k=8)]
        permissions = [asdict(row) for row in agent.snapshot_permission_audit(limit=10)]
        tool_calls = [asdict(call) for call in agent.snapshot_tool_calls(limit=12)]
        model = agent.snapshot_session_state().model_name
        return format_ops(
            jobs,
            failures,
            mcp,
            events,
            usage=usage,
            subagents=subagents,
            permissions=permissions,
            tool_calls=tool_calls,
            model=model,
        )


class SpendScreen(SnapshotScreen):
    """Token cost + compaction events breakdown screen."""

    title_text = "Spend"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        from tokoboros.tui.formatters import format_spend

        usage = agent.snapshot_usage()
        model = agent.snapshot_session_state().model_name
        events = [asdict(evt) for evt in agent.snapshot_runtime_events(limit=50)]
        return format_spend(usage, model, events)


class MemoryScreen(SnapshotScreen):
    title_text = "Memory & Skills"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        recalls = [asdict(row) for row in agent.query_memory_recall("recent context", top_k=5)]
        tasks = [asdict(row) for row in agent.snapshot_task_ledger(limit=8)]
        skills = [asdict(row) for row in agent.snapshot_skills(limit=40)]
        # P3-02: include pending gardener diff cards when available
        diffs = (
            agent.snapshot_gardener_diffs()
            if hasattr(agent, "snapshot_gardener_diffs")
            else None
        )
        return format_memory(tasks, recalls, skills, diffs=diffs)


class OGExplorerScreen(SnapshotScreen):
    """P3-05: OG Explorer — library tree + Pass-1/2/3 inspector with token-efficiency stats."""

    title_text = "OG Explorer"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        data = agent.snapshot_og_explorer() if hasattr(agent, "snapshot_og_explorer") else {}
        return format_og_explorer(data)


class IntegrationsScreen(SnapshotScreen):
    title_text = "Integrations"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        return format_integrations(_mcp_to_dict(agent.snapshot_mcp_inventory()))


class AuthoringScreen(SnapshotScreen):
    title_text = "Authoring"

    def __init__(self) -> None:
        super().__init__()
        self._current_path: Path | None = None
        self._original_content = ""
        self._pending_patch = ""

    def compose(self) -> ComposeResult:
        yield Static(self.title_text, classes="mode-title")
        yield Input(
            placeholder="Asset path under ~/.tokoboros (brain.og, skills/*.og, tokoboros.json)",
            id="authoring-path",
        )
        with Horizontal(id="authoring-root"):
            yield RichLog(id="authoring-targets", highlight=True, wrap=True)
            with Vertical():
                yield TextArea("", id="authoring-editor")
                yield RichLog(id="authoring-patch", highlight=False, wrap=False)
        with Horizontal(id="authoring-actions"):
            yield Button("Load", id="authoring-load")
            yield Button("Generate Patch", id="authoring-diff")
            yield Button("Apply", id="authoring-apply", variant="success")
            yield Button("Reject", id="authoring-reject", variant="error")
            yield Button("Refresh Targets", id="authoring-refresh")
        yield Static("Authoring ready.", id="authoring-status")

    def on_mount(self) -> None:
        self.refresh_targets()

    def refresh_targets(self) -> None:
        app = self.app
        if not isinstance(app, TokoborosTextualApp):
            return

        targets_log = self.query_one("#authoring-targets", RichLog)
        targets_log.clear()
        targets = discover_authoring_targets(get_workspace_dir(), get_skills_dir())
        if not targets:
            targets_log.write("No authoring targets discovered.")
            return

        for target in targets:
            display = str(target.relative_to(get_workspace_dir()))
            targets_log.write(display)

        restored = app.hydrated_authoring_target
        if restored:
            self.query_one("#authoring-path", Input).value = restored

    def _set_status(self, text: str) -> None:
        self.query_one("#authoring-status", Static).update(text)

    def _resolve_target(self) -> Path | None:
        raw = self.query_one("#authoring-path", Input).value.strip()
        if not raw:
            self._set_status("Enter a target path first.")
            return None

        candidate = get_workspace_dir() / raw
        if not is_authoring_target(candidate, get_workspace_dir()):
            self._set_status(
                "Target must be an existing .og/.json/.md/.txt file inside ~/.tokoboros."
            )
            return None
        return candidate.resolve()

    def _load_target(self) -> None:
        target = self._resolve_target()
        if not target:
            return

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            self._set_status(f"Failed to load target: {exc}")
            return

        self._current_path = target
        self._original_content = content
        self.query_one("#authoring-editor", TextArea).text = content
        self.query_one("#authoring-patch", RichLog).clear()
        self._pending_patch = ""
        self._set_status(f"Loaded {target.relative_to(get_workspace_dir())}")
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.record_authoring_target(str(target.relative_to(get_workspace_dir())))

    def _generate_patch(self) -> None:
        if not self._current_path:
            self._set_status("Load a target before generating patch.")
            return

        editor = self.query_one("#authoring-editor", TextArea)
        patch = generate_unified_patch(self._current_path, self._original_content, editor.text)
        patch_log = self.query_one("#authoring-patch", RichLog)
        patch_log.clear()
        if not patch.strip():
            self._pending_patch = ""
            patch_log.write("No changes to review.")
            self._set_status("No diff generated.")
            return

        self._pending_patch = patch
        patch_log.write(patch)
        self._set_status("Patch generated. Review before apply.")

    def _apply_patch(self) -> None:
        if not self._current_path:
            self._set_status("No loaded target to apply.")
            return
        if not self._pending_patch:
            self._set_status("Generate a patch first.")
            return

        editor = self.query_one("#authoring-editor", TextArea)
        try:
            apply_authoring_update(self._current_path, editor.text)
        except Exception as exc:
            self._set_status(f"Apply failed: {exc}")
            return

        self._original_content = editor.text
        self._pending_patch = ""
        self.query_one("#authoring-patch", RichLog).clear()
        self._set_status("Patch applied. Runtime reload is explicit; restart or reload manually.")

    def _reject_patch(self) -> None:
        if not self._current_path:
            self._set_status("No loaded target to reject.")
            return
        self.query_one("#authoring-editor", TextArea).text = self._original_content
        self._pending_patch = ""
        self.query_one("#authoring-patch", RichLog).clear()
        self._set_status("Patch rejected. Editor restored to last loaded/applied version.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "authoring-load":
            self._load_target()
        elif event.button.id == "authoring-diff":
            self._generate_patch()
        elif event.button.id == "authoring-apply":
            self._apply_patch()
        elif event.button.id == "authoring-reject":
            self._reject_patch()
        elif event.button.id == "authoring-refresh":
            self.refresh_targets()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "authoring-path":
            self._load_target()

    def current_target(self) -> str:
        if not self._current_path:
            return ""
        return str(self._current_path.relative_to(get_workspace_dir()))


class SettingsScreen(SnapshotScreen):
    title_text = "Settings"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        state = asdict(agent.snapshot_session_state())
        config = load_config()
        sessions = [dict(row) for row in agent.query_recent_sessions(limit=8)]
        return format_settings(state, config if isinstance(config, dict) else {}, sessions)


class TokoborosTextualApp(App[None]):
    """Tokoboros Textual shell with mode-driven navigation."""

    CSS = APP_CSS
    TITLE = "Tokoboros"
    SUB_TITLE = "Textual Runtime Console"

    MODES = {
        "session": SessionScreen,
        "ops": OpsScreen,
        "spend": SpendScreen,
        "memory": MemoryScreen,
        "integrations": IntegrationsScreen,
        "authoring": AuthoringScreen,
        "settings": SettingsScreen,
        "setup": SetupScreen,
        "og_explorer": OGExplorerScreen,
    }
    DEFAULT_MODE = "session"

    BINDINGS = [
        Binding("ctrl+1", "mode_session", "Session"),
        Binding("f1", "mode_session", show=False),
        Binding("alt+1", "mode_session", show=False),
        Binding("ctrl+2", "mode_ops", "Ops"),
        Binding("f2", "mode_ops", show=False),
        Binding("alt+2", "mode_ops", show=False),
        Binding("ctrl+3", "mode_spend", "Spend"),
        Binding("f3", "mode_spend", show=False),
        Binding("alt+3", "mode_spend", show=False),
        Binding("ctrl+4", "mode_memory", "Memory"),
        Binding("f4", "mode_memory", show=False),
        Binding("alt+4", "mode_memory", show=False),
        Binding("ctrl+5", "mode_integrations", "Integrations"),
        Binding("f5", "mode_integrations", show=False),
        Binding("alt+5", "mode_integrations", show=False),
        Binding("ctrl+6", "mode_authoring", "Authoring"),
        Binding("f6", "mode_authoring", show=False),
        Binding("alt+6", "mode_authoring", show=False),
        Binding("ctrl+7", "mode_settings", "Settings"),
        Binding("f7", "mode_settings", show=False),
        Binding("alt+7", "mode_settings", show=False),
        Binding("ctrl+8", "mode_setup", "Setup"),
        Binding("f8", "mode_setup", show=False),
        Binding("alt+8", "mode_setup", show=False),
        Binding("ctrl+9", "mode_og_explorer", "OG Explorer"),
        Binding("f9", "mode_og_explorer", show=False),
        Binding("alt+9", "mode_og_explorer", show=False),
        Binding("ctrl+p", "command_palette", "Palette"),
        Binding("ctrl+c", "interrupt", "Interrupt"),
        Binding("ctrl+r", "refresh_current", "Refresh"),
        Binding("ctrl+y", "copy_last_response", "Copy response", show=False),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(
        self,
        gateway_base_url: str | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._agent: AgentSession | None = None
        self._boot_error: str | None = None
        self._resume_session_id = resume_session_id
        self._gateway_base_url = (gateway_base_url or "").rstrip("/")
        self._gateway_connected = False
        self._gateway_session_id: str = ""
        self._gateway_after_sequence = 0
        self._boot_thread: threading.Thread | None = None
        self._gateway_event_thread: threading.Thread | None = None
        self._gateway_stop_event = threading.Event()
        self._chat_thread: threading.Thread | None = None
        self._event_buffer: list[tuple[str, dict[str, Any]]] = []
        self._state_store = TUIStateStore()
        self._hydrated_state = self._state_store.load_snapshot()
        self._onboarding = OnboardingService()
        self._setup_controller: SetupWizardController | None = None
        self._approval_waiters: dict[
            str, tuple[threading.Event, dict[str, PermissionDecision]]
        ] = {}
        self._approval_lock = threading.Lock()
        # Session transcript / sidebar state.
        self._turn_streamed_any = False
        self.last_agent_text = ""
        self._activity: tuple[str, str] = ("Idle", "idle")
        self._usage: dict[str, Any] | None = None
        self._recent_tools: list[dict[str, str]] = []
        # Event→activity derivation is owned by the read model's reducer; the
        # header folds events through the same function so the two never drift.
        self._activity_state = ActivitySnapshot("Idle", "idle")

    @property
    def hydrated_draft(self) -> str:
        return self._hydrated_state.draft_input

    @property
    def hydrated_authoring_target(self) -> str:
        return self._hydrated_state.authoring_target

    @property
    def setup_controller(self) -> SetupWizardController | None:
        return self._setup_controller

    def ensure_setup_controller(self) -> SetupWizardController:
        """Create the setup wizard on demand so Setup works any time, not just
        during first-run onboarding."""
        if self._setup_controller is None:
            controller = SetupWizardController(
                self._onboarding,
                persisted_resume_json=self._hydrated_state.setup_resume_json,
            )
            # When there is no resume blob (e.g. first open after a completed
            # setup), pre-fill selections from the saved config so the user
            # sees their current choices rather than blank defaults.
            if not controller.answers:
                self._seed_setup_controller_from_config(controller)
            self._setup_controller = controller
        return self._setup_controller

    def _seed_setup_controller_from_config(self, controller: SetupWizardController) -> None:
        """Populate setup wizard answers from the current on-disk config."""
        try:
            from tokoboros.core.config_loader import load_effective_config

            config = load_effective_config()
        except Exception:
            return

        provider: dict[str, Any] = {}
        if config.provider.kind:
            provider["kind"] = config.provider.kind
        if config.provider.model:
            provider["model"] = config.provider.model
        if config.provider.api_key_env:
            provider["api_key_env"] = config.provider.api_key_env
        if config.provider.base_url:
            provider["base_url"] = config.provider.base_url
        if provider:
            controller.answers["provider"] = provider

        if config.permissions.tier:
            controller.answers["permission_tier"] = config.permissions.tier

        controller.answers["memory"] = {
            "enable_session_history": config.memory.enable_session_history,
            "enable_semantic_memory": config.memory.enable_semantic_memory,
        }

        if provider:
            controller.can_resume = True

    def active_agent(self) -> AgentSession | None:
        return self._agent

    def runtime_status_text(self) -> str:
        if self._boot_error:
            return f"Runtime boot failed: {self._boot_error}"
        if self._gateway_connected:
            return "Gateway runtime is online."
        if self._agent:
            if self._gateway_session_id:
                return f"Runtime is online (gateway session {self._gateway_session_id[:8]}...)."
            return "Runtime is online."
        return "Booting runtime..."

    def _runtime_config(self) -> dict[str, Any]:
        try:
            config = load_config()
        except Exception:
            return {}
        return config if isinstance(config, dict) else {}

    def _event_count(self, event_names: set[str]) -> int:
        return sum(1 for event_type, _payload in self._event_buffer if event_type in event_names)

    def _active_job_count(self) -> int:
        if self._agent:
            try:
                return len(self._agent.snapshot_active_jobs())
            except Exception:
                return 0

        active_job_ids: set[str] = set()
        for event_type, payload in self._event_buffer:
            if event_type == "job.started":
                job_id = str(payload.get("job_id", "")).strip()
                if job_id:
                    active_job_ids.add(job_id)
            elif event_type in {"job.completed", "job.failed"}:
                job_id = str(payload.get("job_id", "")).strip()
                if job_id:
                    active_job_ids.discard(job_id)
        return len(active_job_ids)

    def _pending_approval_count(self) -> int:
        waiter_count = len(self._approval_waiters)
        requested = self._event_count({"permission.requested"})
        decided = self._event_count({"permission.decided"})
        return max(waiter_count, requested - decided)

    def _warning_count(self) -> int:
        return self._event_count(
            {"boot.failed", "job.failed", "mcp.server.failed", "gateway.events.error"}
        )

    def _permission_tier(self) -> str:
        config = self._runtime_config()
        permissions = config.get("permissions", {})
        if not isinstance(permissions, dict):
            return "unknown"
        return str(permissions.get("tier", "unknown"))

    def _event_stream_signal(self) -> str:
        if self._gateway_base_url:
            if self._gateway_connected and not self._gateway_stop_event.is_set():
                return "live"
            return "connecting"
        if self._boot_error:
            return "failed"
        if self._agent:
            return "local"
        return "booting"

    def _provider_model(self) -> str:
        provider = self._runtime_config().get("provider", {})
        if isinstance(provider, dict):
            return str(provider.get("model", "unknown"))
        return "unknown"

    def mark_turn_streamed(self) -> None:
        """Flag that the current turn produced streamed assistant text."""
        self._turn_streamed_any = True

    def _sidebar_info(self) -> dict[str, Any]:
        status, kind = self._activity
        session_id = self._gateway_session_id
        if not session_id and self._agent:
            try:
                session_id = self._agent.snapshot_session_state().session_id
            except Exception:
                session_id = ""
        info: dict[str, Any] = {
            "status": status,
            "status_kind": kind,
            "model": self._provider_model(),
            "tier": self._permission_tier(),
            "workspace": get_workspace_dir().name,
            "session": session_id[:12] if session_id else "none",
            "stream": self._event_stream_signal(),
            "jobs": self._active_job_count(),
            "approvals": self._pending_approval_count(),
            "warnings": self._warning_count(),
            "recent_tools": self._recent_tools[-6:],
        }
        if self._usage:
            info["tokens"] = self._usage.get("total_tokens", 0)
            info["cost"] = self._usage.get("estimated_cost_usd", 0.0)
        return info

    def refresh_sidebar(self) -> None:
        info = self._sidebar_info()
        screen = self._current_screen()
        # The sidebar shows the live metrics only when it is actually visible:
        # on the SessionScreen AND wide enough not to be collapsed. Everywhere
        # else (narrow, or a non-session screen) the docked header carries them.
        # Routing here means model/tokens/cost never appear in both at once.
        sidebar_visible = isinstance(screen, SessionScreen) and self.size.width >= NARROW_WIDTH
        try:
            header_info = info if not sidebar_visible else _strip_metrics(info)
            self.query_one("#toko-header", TokoGlyph).set_status(header_info)
        except Exception:
            pass
        # Update the always-visible governance strip regardless of screen/width.
        try:
            self.query_one("#status-strip", StatusStrip).update_info(info)
        except Exception:
            pass
        if not isinstance(screen, SessionScreen):
            return
        try:
            sidebar = screen.query_one("#sidebar", SidebarPanel)
        except Exception:
            return
        sidebar.update_info(info)

    def _note_activity(self, event_type: str, payload: dict[str, Any]) -> None:
        # Fold the event through the read model's reducer (the single owner of
        # event→activity derivation), then mirror it into the render fields.
        self._activity_state = advance_activity(self._activity_state, event_type, payload)
        state = self._activity_state
        self._activity = (state.label, state.state)
        self._recent_tools = [{"name": t.name, "status": t.status} for t in state.recent_tools]
        if state.total_tokens or state.estimated_cost_usd:
            self._usage = {
                "total_tokens": state.total_tokens,
                "estimated_cost_usd": state.estimated_cost_usd,
            }

    def compose(self) -> ComposeResult:
        # The Toko brand glyph *is* the header: a centered wordmark whose eye
        # encodes live runtime state. Replaces the stock Header so the two do
        # not collide at the docked top row.
        yield TokoGlyph(id="toko-header")
        # Ambient governance strip: state, jobs, approvals, spend — always
        # visible on every screen, even when the sidebar is collapsed narrow.
        yield StatusStrip(id="status-strip")
        # The runtime/provider/tier summary lives only in the session sidebar
        # (format_session_info) — no bottom banner, to avoid duplicating it.
        yield Footer()

    def clear_selection(self) -> None:
        """Compatibility shim for Textual startup when no screen is mounted yet."""
        try:
            super().clear_selection()
        except (NoScreen, ScreenStackError):
            # Some Textual versions raise ScreenStackError instead of NoScreen
            # during initial input mount before the first screen is on stack.
            pass

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Custom CSS ``$variables`` that must resolve regardless of active theme.

        ``App.CSS`` is parsed at startup against the default theme — before
        ``on_mount`` registers/activates the tokoboros theme — so the custom
        variables it references (``$border-muted``, ``$text-muted``) must be
        provided here or the stylesheet fails to parse. Values mirror
        ``theme.py`` so there is a single source of truth.
        """
        return {
            "border-muted": theme.BORDER_MUTED,
            "text-muted": theme.MUTED,
            "role-user": theme.ROLE_USER,
            "role-agent": theme.ROLE_AGENT,
        }

    def on_mount(self) -> None:
        self.register_theme(TOKOBOROS_THEME)
        self.theme = "tokoboros"

        target_mode = self.DEFAULT_MODE

        if self._onboarding.needs_setup():
            self._setup_controller = SetupWizardController(
                self._onboarding,
                persisted_resume_json=self._hydrated_state.setup_resume_json,
            )
            target_mode = "setup"
            self.notify("Setup required before first runtime boot.", timeout=4)
        else:
            self.notify("Booting Tokoboros runtime...", timeout=4)
            self._boot_thread = threading.Thread(
                target=self._boot_runtime,
                name="tui-boot",
                daemon=True,
            )
            self._boot_thread.start()

        restored_mode = self._hydrated_state.last_mode
        if (
            target_mode != "setup"
            and restored_mode in self.MODES
            and restored_mode != self.DEFAULT_MODE
        ):
            target_mode = restored_mode

        # Delay initial mode switch until the first refresh to avoid switching
        # while the default screen widgets are still in their mount lifecycle.
        if target_mode != self.DEFAULT_MODE:
            self.call_after_refresh(self.switch_mode, target_mode)

    def _boot_runtime(self) -> None:
        try:
            if self._gateway_base_url:
                self._consume_gateway_handshake()
                self._start_gateway_event_poller()
                self._queue_ui_callback(self._on_boot_complete, None, None)
                return

            agent = boot_agent(
                on_event=self._runtime_event_from_thread,
                on_permission_request=self._request_permission_decision,
                resume_session_id=self._resume_session_id,
            )
            self._queue_ui_callback(self._on_boot_complete, agent, None)
        except Exception as exc:  # pragma: no cover - UI fallback path
            self._queue_ui_callback(self._on_boot_complete, None, str(exc))

    def _consume_gateway_handshake(self) -> None:
        readiness_url = f"{self._gateway_base_url}/readiness"
        connect_url = f"{self._gateway_base_url}/sessions"

        readiness_req = request.Request(readiness_url)
        if _GATEWAY_TOKEN:
            readiness_req.add_unredirected_header("Authorization", f"Bearer {_GATEWAY_TOKEN}")
        try:
            with request.urlopen(readiness_req, timeout=_URLOPEN_TIMEOUT_S) as resp:
                readiness = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
            blockers = payload.get("blockers", [])
            raise RuntimeError(f"Gateway is not ready: blockers={blockers}") from exc
        except Exception as exc:
            raise RuntimeError(f"Gateway readiness check failed: {exc}") from exc

        if not readiness.get("ready", False):
            blockers = readiness.get("blockers", [])
            raise RuntimeError(f"Gateway is not ready: blockers={blockers}")

        payload = {
            "client_id": "tokoboros-tui",
            "surface": "tui",
            "protocol_version": "1.0",
            "capabilities": ["events", "health", "readiness"],
        }
        req = request.Request(
            connect_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if _GATEWAY_TOKEN:
            req.add_unredirected_header("Authorization", f"Bearer {_GATEWAY_TOKEN}")

        try:
            with request.urlopen(req, timeout=_URLOPEN_TIMEOUT_S) as resp:
                connect_ack = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Gateway connect failed: {exc}") from exc

        # Prefer unversioned response shape, but keep legacy envelope fallback.
        ack_payload = connect_ack.get("payload", connect_ack)
        if not ack_payload.get("accepted"):
            raise RuntimeError("Gateway rejected TUI connect request")

        self._gateway_session_id = str(ack_payload.get("session_id", ""))
        self._gateway_connected = True
        self._runtime_event_from_thread(
            "gateway.connect.completed",
            {
                "base_url": self._gateway_base_url,
                "session_id": self._gateway_session_id,
            },
        )

    def _start_gateway_event_poller(self) -> None:
        if self._gateway_event_thread and self._gateway_event_thread.is_alive():
            return

        self._gateway_stop_event.clear()
        self._gateway_event_thread = threading.Thread(
            target=self._poll_gateway_events,
            name="tui-gateway-events",
            daemon=True,
        )
        self._gateway_event_thread.start()

    def _poll_gateway_events(self) -> None:
        while not self._gateway_stop_event.is_set():
            url = (
                f"{self._gateway_base_url}/sessions/{self._gateway_session_id}/events"
                f"?after={self._gateway_after_sequence}"
                "&limit=100"
            )
            poll_req = request.Request(url)
            if _GATEWAY_TOKEN:
                poll_req.add_unredirected_header("Authorization", f"Bearer {_GATEWAY_TOKEN}")
            try:
                with request.urlopen(poll_req, timeout=_URLOPEN_TIMEOUT_S) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                payload = body.get("payload", body)
                events = payload.get("events", [])
                for item in events:
                    self._runtime_event_from_thread(
                        str(item.get("event_type", "unknown")),
                        dict(item.get("payload", {})),
                    )
                self._gateway_after_sequence = int(
                    payload.get("next_sequence", self._gateway_after_sequence)
                )
                if not events:
                    self._gateway_stop_event.wait(0.25)
            except Exception as exc:
                self._runtime_event_from_thread(
                    "gateway.events.error",
                    {"error": str(exc)},
                )
                self._gateway_stop_event.wait(1.0)

    def _request_permission_decision(self, request: PermissionRequest) -> PermissionDecision:
        waiter = threading.Event()
        holder: dict[str, PermissionDecision] = {}
        with self._approval_lock:
            self._approval_waiters[request.request_id] = (waiter, holder)

        # call_from_thread runs _show_permission_modal inside the Textual event loop,
        # ensuring the active_app ContextVar is set so push_screen works correctly.
        try:
            self.call_from_thread(self._show_permission_modal, request)
        except Exception:
            with self._approval_lock:
                self._approval_waiters.pop(request.request_id, None)
            return PermissionDecision(action="deny", rationale="UI not ready")

        completed = waiter.wait(timeout=180)
        if not completed:
            with self._approval_lock:
                self._approval_waiters.pop(request.request_id, None)
            return PermissionDecision(action="deny", rationale="Approval timed out")

        return holder.get(
            "decision",
            PermissionDecision(action="deny", rationale="No decision received"),
        )

    def _show_permission_modal(self, request: PermissionRequest) -> None:
        def _on_close(result: PermissionDecision | None) -> None:
            decision = result or PermissionDecision(
                action="deny",
                rationale="Dismissed without approval",
            )
            self._resolve_permission_decision(request.request_id, decision)

        self.push_screen(PermissionApprovalModal(request), _on_close)

    def _resolve_permission_decision(self, request_id: str, decision: PermissionDecision) -> None:
        with self._approval_lock:
            waiter_data = self._approval_waiters.pop(request_id, None)
        if not waiter_data:
            return

        waiter, holder = waiter_data
        holder["decision"] = decision
        waiter.set()

    def _resolve_all_pending_approvals(self, rationale: str) -> None:
        with self._approval_lock:
            pending = list(self._approval_waiters.items())
            self._approval_waiters.clear()

        for _request_id, (waiter, holder) in pending:
            holder["decision"] = PermissionDecision(action="deny", rationale=rationale)
            waiter.set()

    def _runtime_event_from_thread(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._queue_ui_callback(self._ingest_runtime_event, event_type, payload):
            # Event loop is closed or closing; buffer the event for later
            self._event_buffer.append((event_type, payload))

    def _queue_ui_callback(self, callback, *args: Any) -> bool:
        """Schedule a callback on the Textual loop when it is still alive."""
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(callback, *args)
            return True
        except RuntimeError:
            return False

    def _current_screen(self) -> Screen | None:
        """Return the current screen, or None when screen stack is unavailable."""
        try:
            return self.screen
        except Exception:
            return None

    def _ingest_runtime_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._event_buffer.append((event_type, payload))
        if len(self._event_buffer) > 1500:
            self._event_buffer = self._event_buffer[-1500:]

        self._note_activity(event_type, payload)

        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            is_tool_error = event_type == "tool.call.completed" and _looks_like_error(
                _sanitize_legacy_rich_markup(str(payload.get("result_preview", "")))
            )
            toko_state = theme.toko_state_for_event(event_type, is_error=is_tool_error)
            if toko_state is not None:
                screen.set_toko_state(toko_state)
            screen.ingest_runtime_event(event_type, payload)
        self.refresh_sidebar()

    def _on_boot_complete(self, agent: AgentSession | None, error_text: str | None) -> None:
        self._agent = agent
        self._boot_error = error_text

        if error_text:
            _KEY_HINTS = ("api_key", "API_KEY", "must be provided", "set in the environment")
            is_missing_key = any(hint in error_text for hint in _KEY_HINTS)
            if is_missing_key:
                friendly = (
                    "Provider API key is missing or unresolvable. "
                    "Press Ctrl+7 (or /setup) to open Setup and add your key."
                )
                self.notify(friendly, severity="warning", timeout=10)
                screen = self._current_screen()
                if isinstance(screen, SessionScreen):
                    screen.ingest_runtime_event("boot.failed", {"error": friendly})
                # Offer Setup so the user can fix this without restarting.
                self.call_after_refresh(self.ensure_setup_controller)
            else:
                self.notify(f"Boot failed: {error_text}", severity="error", timeout=8)
                screen = self._current_screen()
                if isinstance(screen, SessionScreen):
                    screen.ingest_runtime_event("boot.failed", {"error": error_text})
            return

        self.notify("Tokoboros is online", timeout=3)
        self._activity = ("Idle", "idle")
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            resumed = self._render_resumed_transcript(agent, screen)
            if not resumed:
                # Fresh session: invite the first action instead of a bare splash.
                screen.show_welcome(_STARTER_PROMPTS)
            screen.ingest_runtime_event("boot.complete", {})

        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self.refresh_sidebar()

    def _render_resumed_transcript(self, agent: AgentSession | None, screen: SessionScreen) -> bool:
        """Replay a resumed session's prior turns. Returns True if anything was restored."""
        if agent is None:
            return False
        transcript = getattr(agent, "resumed_transcript", [])
        if not transcript:
            return False
        screen._mount_system_line(
            f"Resumed previous session — {len(transcript)} messages restored.",
            style=theme.SUCCESS,
        )
        for role, content in transcript:
            if role == "user":
                screen.append_user_prompt(content)
            else:
                screen.render_assistant_text(content)
        return True

    def submit_prompt(self, prompt: str, display_text: str | None = None) -> None:
        if self._boot_error:
            self.notify(
                "Runtime is not available. Check boot failure in transcript.", severity="error"
            )
            return

        if not self._agent:
            if not self._gateway_connected:
                self.notify("Still booting. Please wait.", timeout=2)
                return

        if self._chat_thread and self._chat_thread.is_alive():
            self.notify("A chat request is already running.", timeout=2)
            return

        self._turn_streamed_any = False
        self._activity = ("Working…", "running")

        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.reset_streaming_state()
            # display_text lets slash commands (e.g. /plan) show a friendly bubble
            # while sending an expanded instruction to the model.
            screen.append_user_prompt(display_text or prompt)
        self.refresh_sidebar()

        target = self._run_gateway_chat if self._gateway_connected else self._run_chat
        self._chat_thread = threading.Thread(
            target=target,
            args=(prompt,),
            name="tui-chat",
            daemon=True,
        )
        self._chat_thread.start()

    def _run_chat(self, prompt: str) -> None:
        try:
            assert self._agent is not None
            response = self._agent.chat(prompt)
            self._queue_ui_callback(self._on_chat_result, response, None)
        except Exception as exc:  # pragma: no cover - defensive UI path
            self._queue_ui_callback(self._on_chat_result, "", str(exc))

    def _run_gateway_chat(self, prompt: str) -> None:
        payload = {"prompt": prompt}
        req = request.Request(
            f"{self._gateway_base_url}/sessions/{self._gateway_session_id}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if _GATEWAY_TOKEN:
            req.add_unredirected_header("Authorization", f"Bearer {_GATEWAY_TOKEN}")

        try:
            with request.urlopen(req, timeout=_URLOPEN_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            payload_body = body.get("payload", body)
            response = str(payload_body.get("response", ""))
            self._queue_ui_callback(self._on_chat_result, response, None)
        except (TimeoutError, error.URLError) as exc:
            self._queue_ui_callback(self._on_chat_result, "", f"Gateway request failed: {exc}")
        except Exception as exc:
            self._queue_ui_callback(self._on_chat_result, "", str(exc))

    def _on_chat_result(self, response: str, error_text: str | None) -> None:
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            # Commit any in-flight streaming bubble before rendering error/response.
            screen.finalize_streaming_bubble()
            if error_text:
                screen.append_error(error_text)
            elif not self._turn_streamed_any:
                # No streamed text arrived this turn — render the final
                # response. When deltas did stream (the normal case), the
                # transcript already shows it, so skip to avoid duplication.
                screen.render_assistant_text(response)
        self._activity = ("Idle", "idle")
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self.refresh_sidebar()
        self._persist_ui_state()

    # ==========================================
    # Slash commands (typed into the composer)
    # ==========================================

    def _session_note(self, text: str, *, style: str | None = None) -> None:
        """Write an informational line into the session transcript (or notify)."""
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen._mount_system_line(text, style=style or theme.MUTED)
        else:
            self.notify(text)

    def handle_slash_command(self, raw: str) -> None:
        """Parse and dispatch a ``/command`` typed into the composer."""
        body = raw[1:].strip()
        parts = body.split(maxsplit=1)
        verb = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers: dict[str, Callable[[str], object]] = {
            "help": lambda _a: self.action_help(),
            "clear": self._cmd_clear,
            "new": self._cmd_clear,
            "compact": self._cmd_compact,
            "copy": lambda _a: self.action_copy_last_response(),
            "sessions": self._cmd_sessions,
            "search": self._cmd_search,
            "model": self._cmd_model,
            "memory": self._cmd_memory,
            "remember": self._cmd_remember,
            "forget": self._cmd_forget,
            "plan": self._cmd_plan,
            "deep-research": self._cmd_deep_research,
            "research": self._cmd_deep_research,
            "speak": self._cmd_speak,
            "transcribe": self._cmd_transcribe,
            "notify": self._cmd_notify,
            "schedules": self._cmd_schedules,
            "export": self._cmd_export,
            "settings": lambda _a: self._switch_mode("settings"),
            "setup": lambda _a: self.action_mode_setup(),
            "quit": lambda _a: self.exit(),
            "exit": lambda _a: self.exit(),
        }
        handler = handlers.get(verb)
        if handler is None:
            self._session_note(
                f"Unknown command: /{verb}. Type /help for the list.", style=theme.WARNING
            )
            return
        handler(arg)

    def _cmd_clear(self, _arg: str) -> None:
        agent = self._agent
        if agent is not None:
            try:
                agent.memory.reset()
            except Exception:
                pass
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.clear_transcript()
            screen._mount_system_line("Context cleared.", style=theme.SUCCESS)
        self.last_agent_text = ""
        self.refresh_sidebar()

    def _cmd_compact(self, _arg: str) -> None:
        agent = self._agent
        if agent is None:
            self._session_note("Runtime is still booting.", style=theme.WARNING)
            return
        cleared = 0
        try:
            agent.memory.apply_pending_compaction()
            cleared = agent.memory.clear_stale_tool_results()
        except Exception as exc:
            self._session_note(f"Compaction failed: {exc}", style=theme.ERROR)
            return
        self._session_note(
            f"Context compacted — reclaimed {cleared} stale tool-result block(s).",
            style=theme.SUCCESS,
        )
        self.refresh_sidebar()

    def _cmd_search(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /search <query>", style=theme.WARNING)
            return
        screen = self._current_screen()
        if not isinstance(screen, SessionScreen):
            self._session_note("Search is only available in the chat view.", style=theme.WARNING)
            return
        hits = search_transcript(screen.collect_transcript(), arg)
        screen._append(
            Static(format_search_results(arg, hits), classes="system-line"),
            scroll=True,
        )

    def _cmd_sessions(self, _arg: str) -> None:
        agent = self._agent
        sessions = list(agent.query_recent_sessions(limit=10)) if agent else []
        if not sessions:
            self._session_note("No recent sessions found.", style=theme.MUTED)
            return
        lines = ["Recent sessions (resume one with:  tokoboros -r):"]
        for index, item in enumerate(sessions, start=1):
            started = str(item.get("started_at", ""))[:19].replace("T", " ")
            title = item.get("title") or "(untitled)"
            lines.append(
                f"  {index}. {started}  ·  {item.get('total_turns', 0)} turns"
                f"  ·  {item.get('model', '?')}  ·  {title}"
            )
        self._session_note("\n".join(lines))

    def _cmd_model(self, arg: str) -> None:
        from tokoboros.core.config_loader import load_effective_config, write_global_config
        from tokoboros.core.llm import can_switch_model, resolve_available_models

        try:
            config = load_effective_config()
        except Exception as exc:
            self._session_note(f"Could not load config: {exc}", style=theme.ERROR)
            return

        current = config.provider.model
        allowed = list(getattr(config.provider, "allowed_models", []) or [])
        discovered: list[str] = []
        if self._agent is not None:
            discovered = self._agent.list_provider_models()

        named_providers: dict = getattr(config, "providers", {}) or {}

        if not arg:
            # No args: show active model, any named providers, and available models.
            source = "provider" if discovered else "defaults"
            if not discovered:
                discovered = _CURATED_MODELS.get(config.provider.kind, [current])
            options = resolve_available_models(discovered, allowed, current)
            label = "allowlist" if allowed else source
            lines = [f"Active: [{config.provider.kind}] {current}"]
            if named_providers:
                lines.append(
                    "Other providers (switch with  /model <name>  or  /model <name> <model>):"
                )
                for pname, ps in named_providers.items():
                    lines.append(f"  • {pname}  [{ps.kind}]  default: {ps.model}")
            lines.append(f"Available models ({label}):")
            lines += [f"  • {name}{'  (current)' if name == current else ''}" for name in options]
            if allowed:
                lines.append("Switching is restricted to the configured allowlist.")
            lines.append("Switch with:  /model <model>  or  /model <provider> <model>")
            self._session_note("\n".join(lines))
            return

        parts = arg.split(None, 1)  # at most 2 tokens

        if len(parts) == 2:
            # /model PROVIDER MODEL — switch to a named provider with a specific model.
            provider_name, new_model = parts
            target_ps = named_providers.get(provider_name)
            if target_ps is None:
                known = ", ".join(sorted(named_providers.keys())) or "(none configured)"
                self._session_note(
                    f"Unknown provider '{provider_name}'. Configured providers: {known}.\n"
                    "Add providers in ~/.tokoboros/settings.toml under [providers.NAME].",
                    style=theme.WARNING,
                )
                return
            try:
                config.provider = target_ps.model_copy(update={"model": new_model})
                write_global_config(config)
            except Exception as exc:
                self._session_note(f"Failed to set provider: {exc}", style=theme.ERROR)
                return
            self._session_note(
                f"Switched to [{provider_name}] {new_model}. Rebooting runtime…",
                style=theme.SUCCESS,
            )
            self._reboot_runtime(resume_current=True)
            return

        single = parts[0]

        if single in named_providers:
            # /model PROVIDER — switch to a named provider using its default model.
            target_ps = named_providers[single]
            try:
                config.provider = target_ps.model_copy()
                write_global_config(config)
            except Exception as exc:
                self._session_note(f"Failed to set provider: {exc}", style=theme.ERROR)
                return
            self._session_note(
                f"Switched to [{single}] {target_ps.model}. Rebooting runtime…",
                style=theme.SUCCESS,
            )
            self._reboot_runtime(resume_current=True)
            return

        # /model MODEL — backward-compat: switch model within the current provider.
        if not can_switch_model(single, allowed):
            self._session_note(
                f"'{single}' is not in the allowed model list: {', '.join(allowed)}. "
                "Edit [provider].allowed_models to change this.",
                style=theme.WARNING,
            )
            return

        if discovered and single != current and single not in discovered:
            self._session_note(
                f"'{single}' is not exposed by {config.provider.kind}. "
                "Run /model (no args) to pick from available models.",
                style=theme.WARNING,
            )
            return

        try:
            config.provider.model = single
            write_global_config(config)
        except Exception as exc:
            self._session_note(f"Failed to set model: {exc}", style=theme.ERROR)
            return
        self._session_note(
            f"Model set to {single}. Rebooting runtime (conversation preserved)…",
            style=theme.SUCCESS,
        )
        self._reboot_runtime(resume_current=True)

    def _cmd_memory(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /memory <query>", style=theme.WARNING)
            return
        agent = self._agent
        if agent is None:
            self._session_note("Runtime is still booting.", style=theme.WARNING)
            return
        try:
            results = agent.query_memory_recall(arg, top_k=8)
        except Exception as exc:
            self._session_note(f"Memory search failed: {exc}", style=theme.ERROR)
            return
        if not results:
            self._session_note(f"No memory matches for: {arg}", style=theme.MUTED)
            return
        lines = [f"Memory matches for '{arg}':"]
        lines += [f"  • {row.content[:140]}" for row in results]
        self._session_note("\n".join(lines))

    def _cmd_remember(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /remember <fact>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            "Save the following to long-term memory using your memory tools, "
            f"then confirm briefly: {arg}",
            display_text=f"/remember {arg}",
        )

    def _cmd_forget(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /forget <query>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            "Search long-term memory and delete any facts matching the following, "
            f"then confirm what was removed: {arg}",
            display_text=f"/forget {arg}",
        )

    def _cmd_plan(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /plan <task>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            "[PLAN MODE] Before doing anything, think step by step and present a clear, "
            "numbered plan of how you will accomplish the task. Do not execute until the "
            f"plan is laid out. Task:\n{arg}",
            display_text=f"/plan {arg}",
        )

    def _cmd_deep_research(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /deep-research <question>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            "[DEEP RESEARCH] Investigate the following thoroughly. Consult multiple sources "
            "(use your web/browse tools where available), cross-check claims, and synthesize "
            "a well-structured answer with citations. Question:\n" + arg,
            display_text=f"/deep-research {arg}",
        )

    def _cmd_speak(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /speak <text to synthesize>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            f"Use the speak_text tool to synthesize the following text to audio and tell me "
            f'where the file was saved: "{arg}"',
            display_text=f"/speak {arg}",
        )

    def _cmd_transcribe(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /transcribe <audio file path>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            f"Use the transcribe_and_summarize tool to transcribe and summarize the audio "
            f'file at: "{arg}"',
            display_text=f"/transcribe {arg}",
        )

    def _cmd_notify(self, arg: str) -> None:
        if not arg:
            self._session_note("Usage: /notify <message>", style=theme.WARNING)
            return
        self.set_toko_state_safe("thinking")
        self.submit_prompt(
            f"Use the notify_user tool to send a desktop notification with the title "
            f'"Tokoboros" and body: "{arg}"',
            display_text=f"/notify {arg}",
        )

    def _cmd_schedules(self, _arg: str) -> None:
        try:
            from tokoboros.core.paths import get_tokoboros_paths
            from tokoboros.jobs.scheduler import Scheduler, ScheduleStore

            store = Scheduler(ScheduleStore(get_tokoboros_paths().schedules_file))
            schedules = store.list_schedules()
        except Exception as exc:
            self._session_note(f"Could not read schedules: {exc}", style=theme.ERROR)
            return

        if not schedules:
            self._session_note("No schedules configured. Add one: tokoboros schedule add")
            return

        from datetime import UTC, datetime

        local_tz = datetime.now(UTC).astimezone().tzinfo

        def _fmt_next(next_run_iso: str | None) -> str:
            if not next_run_iso:
                return "(none)"
            try:
                dt = datetime.fromisoformat(next_run_iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                local_dt = dt.astimezone(local_tz)
                return local_dt.strftime("%Y-%m-%d %H:%M %Z")
            except Exception:
                return str(next_run_iso)[:16].replace("T", " ")

        lines = [f"{len(schedules)} schedule(s):"]
        for s in schedules[:10]:
            state = "✓" if getattr(s, "enabled", True) else "⏸"
            kind = getattr(s, "kind", None)
            kind_str = kind.value if kind is not None else "?"
            lines.append(
                f"  {state} [{kind_str}] {s.name!r}"
                f"  → next: {_fmt_next(getattr(s, 'next_run', None))}"
            )
        if len(schedules) > 10:
            lines.append(f"  … and {len(schedules) - 10} more")
        self._session_note("\n".join(lines))

    def _cmd_export(self, arg: str) -> None:
        agent = self._agent
        if agent is None:
            self._session_note("Runtime is still booting.", style=theme.WARNING)
            return
        session_id = getattr(agent, "_session_id", None)
        if not session_id:
            self._session_note("No active session to export.", style=theme.WARNING)
            return
        try:
            from tokoboros.core.paths import get_tokoboros_paths
            from tokoboros.core.session_store import SessionStore

            store = SessionStore()
            messages = store.get_session_messages(session_id, limit=500)
            meta = store.get_session(session_id)
            store.shutdown()
            title = (meta.get("title") or session_id) if meta else session_id
            out_path = (
                Path(arg)
                if arg
                else get_tokoboros_paths().state_dir / f"session-{session_id[:8]}.md"
            )
            lines = [f"# {title}\n"]
            for msg in messages:
                role = msg.get("role", "?")
                content = (msg.get("content") or "").strip()
                ts = (msg.get("timestamp") or "")[:19].replace("T", " ")
                if role == "user":
                    lines.append(f"**You** _{ts}_\n\n{content}\n")
                elif role == "assistant":
                    lines.append(f"**Agent** _{ts}_\n\n{content}\n")
            out_path.write_text("\n---\n\n".join(lines), encoding="utf-8")
            self._session_note(
                f"Exported {len(messages)} messages → {out_path}", style=theme.SUCCESS
            )
        except Exception as exc:
            self._session_note(f"Export failed: {exc}", style=theme.ERROR)

    def set_toko_state_safe(self, state: str) -> None:
        """Drive the brand glyph without raising if the session screen is not active."""
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.set_toko_state(state)

    def _reboot_runtime(self, resume_current: bool = False) -> None:
        """Tear down the live agent and boot a fresh one (e.g. after a model switch).

        When ``resume_current`` is set, the in-flight session id is replayed into the
        new agent so the conversation continues seamlessly with the new settings.
        """
        if self._chat_thread and self._chat_thread.is_alive():
            self._session_note("Finish the current turn before switching.", style=theme.WARNING)
            return

        old_agent = self._agent
        resume_id: str | None = None
        if resume_current and old_agent is not None:
            try:
                resume_id = old_agent.snapshot_session_state().session_id or None
            except Exception:
                resume_id = None

        self._agent = None
        self._boot_error = None
        self._resume_session_id = resume_id

        if old_agent is not None:
            threading.Thread(
                target=lambda a=old_agent: self._safe_shutdown_agent(a),
                name="tui-agent-shutdown",
                daemon=True,
            ).start()

        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.clear_transcript()
            screen._mount_system_line("Rebooting runtime…", style=theme.MUTED)

        self._boot_thread = threading.Thread(
            target=self._boot_runtime, name="tui-boot", daemon=True
        )
        self._boot_thread.start()

    @staticmethod
    def _safe_shutdown_agent(agent: AgentSession) -> None:
        try:
            agent.shutdown()
        except Exception:
            pass

    def action_interrupt(self) -> None:
        self._gateway_stop_event.set()
        self._resolve_all_pending_approvals("Interrupted by operator")
        self.notify(
            "Interrupt requested. Event polling stopped and pending approvals were dismissed.",
            timeout=4,
        )

    def action_copy_last_response(self) -> None:
        """Copy the most recent agent response to the system clipboard (Ctrl+Y / /copy)."""
        if not self.last_agent_text:
            self.notify("No response to copy yet.", severity="warning", timeout=2)
            return
        self.copy_to_clipboard(self.last_agent_text)
        self.notify("Response copied  ·  Ctrl+V or right-click to paste", timeout=2)
        # Re-focus composer so paste lands in the right widget.
        self._refocus_composer()

    def _refocus_composer(self) -> None:
        """Return keyboard focus to the composer Input if it is visible."""
        try:
            from textual.widgets import Input as _Input

            self.screen.query_one("#composer", _Input).focus()
        except Exception:
            pass

    def action_help(self) -> None:
        if isinstance(self._current_screen(), HelpModal):
            return
        self.push_screen(HelpModal())

    def action_refresh_current(self) -> None:
        screen = self._current_screen()
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self.refresh_sidebar()
        self.notify("Refreshed", timeout=1)

    def _refresh_snapshot_mode(self, screen: Screen) -> None:
        if isinstance(screen, SnapshotScreen):
            screen.refresh_from_agent(self._agent)
        if isinstance(screen, AuthoringScreen):
            screen.refresh_targets()
        if isinstance(screen, SetupScreen):
            screen.render_step()

    def on_screen_resume(self, _event: Screen.Resume) -> None:
        screen = self._current_screen()
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self.refresh_sidebar()
        self._persist_ui_state()

    def _switch_mode(self, mode_name: str) -> None:
        self.switch_mode(mode_name)
        self._persist_ui_state()

    def action_mode_session(self) -> None:
        self._switch_mode("session")

    def action_mode_ops(self) -> None:
        self._switch_mode("ops")

    def action_mode_spend(self) -> None:
        self._switch_mode("spend")

    def action_mode_memory(self) -> None:
        self._switch_mode("memory")

    def action_mode_integrations(self) -> None:
        self._switch_mode("integrations")

    def action_mode_authoring(self) -> None:
        self._switch_mode("authoring")

    def action_mode_settings(self) -> None:
        self._switch_mode("settings")

    def action_mode_setup(self) -> None:
        self.ensure_setup_controller()
        self._switch_mode("setup")

    def persist_setup_resume(self) -> None:
        if not self._setup_controller:
            return
        self._hydrated_state = TUIStateSnapshot(
            last_mode=self._hydrated_state.last_mode,
            draft_input=self._hydrated_state.draft_input,
            authoring_target=self._hydrated_state.authoring_target,
            recent_authoring_targets=self._hydrated_state.recent_authoring_targets,
            filters_json=self._hydrated_state.filters_json,
            setup_resume_json=self._setup_controller.serialize_resume(),
        )
        self._state_store.save_snapshot(self._hydrated_state)

    def complete_setup(self) -> None:
        if not self._setup_controller:
            return
        try:
            applied = self._setup_controller.finish()
        except Exception as exc:
            self.notify(f"Setup failed: {exc}", severity="error", timeout=8)
            return

        self.notify(f"Setup complete ({applied.get('tier')}). Booting runtime...", timeout=4)
        self._setup_controller = None
        self._hydrated_state = TUIStateSnapshot(
            last_mode="session",
            draft_input=self._hydrated_state.draft_input,
            authoring_target=self._hydrated_state.authoring_target,
            recent_authoring_targets=self._hydrated_state.recent_authoring_targets,
            filters_json=self._hydrated_state.filters_json,
            setup_resume_json="{}",
        )
        self._state_store.save_snapshot(self._hydrated_state)
        self.switch_mode("session")

        # Re-running setup after the runtime is already up: retire the old agent so
        # the fresh config (provider/permissions/memory) takes effect cleanly.
        old_agent = self._agent
        self._agent = None
        self._boot_error = None
        self._resume_session_id = None
        if old_agent is not None:
            threading.Thread(
                target=lambda a=old_agent: self._safe_shutdown_agent(a),
                name="tui-agent-shutdown",
                daemon=True,
            ).start()
            session_screen = self.screen if isinstance(self.screen, SessionScreen) else None
            if session_screen is not None:
                session_screen.clear_transcript()

        self._boot_thread = threading.Thread(
            target=self._boot_runtime, name="tui-boot", daemon=True
        )
        self._boot_thread.start()

    def record_authoring_target(self, target: str) -> None:
        existing = [
            item for item in self._hydrated_state.recent_authoring_targets if item != target
        ]
        updated = tuple([target, *existing][:12])
        self._hydrated_state = TUIStateSnapshot(
            last_mode=self._hydrated_state.last_mode,
            draft_input=self._hydrated_state.draft_input,
            authoring_target=target,
            recent_authoring_targets=updated,
            filters_json=self._hydrated_state.filters_json,
            setup_resume_json=self._hydrated_state.setup_resume_json,
        )
        self._persist_ui_state()

    def _persist_ui_state(self) -> None:
        composer_value = ""
        current_screen = self._current_screen()
        if isinstance(current_screen, SessionScreen):
            composer_value = current_screen.query_one("#composer", Input).value

        authoring_target = self._hydrated_state.authoring_target
        if isinstance(current_screen, AuthoringScreen):
            authoring_target = current_screen.current_target() or authoring_target

        mode = getattr(self, "current_mode", self.DEFAULT_MODE)
        setup_resume = self._hydrated_state.setup_resume_json
        if self._setup_controller:
            setup_resume = self._setup_controller.serialize_resume()

        snapshot = TUIStateSnapshot(
            last_mode=mode,
            draft_input=composer_value,
            authoring_target=authoring_target,
            recent_authoring_targets=self._hydrated_state.recent_authoring_targets,
            filters_json=self._hydrated_state.filters_json,
            setup_resume_json=setup_resume,
        )
        self._hydrated_state = snapshot
        self._state_store.save_snapshot(snapshot)

    def on_unmount(self) -> None:
        # Teardown must never raise — Textual prints any exception here straight to
        # the terminal after the UI is gone, which is exactly the ugly exit we are
        # avoiding. Each step is isolated so one failure cannot abort the rest.
        try:
            self._gateway_stop_event.set()
        except Exception:
            pass
        try:
            self._resolve_all_pending_approvals("App shutting down")
        except Exception:
            pass
        for thread in (self._boot_thread, self._chat_thread, self._gateway_event_thread):
            try:
                if thread and thread.is_alive():
                    thread.join(timeout=1.0)
            except Exception:
                pass
        try:
            self._persist_ui_state()
        except Exception:
            pass
        if self._agent:
            try:
                self._agent.shutdown()
            except Exception:
                pass
        try:
            self._state_store.shutdown()
        except Exception:
            pass


def run_tui(gateway_base_url: str | None = None, resume_session_id: str | None = None) -> None:
    """Entrypoint used by the CLI command."""
    TokoborosTextualApp(
        gateway_base_url=gateway_base_url, resume_session_id=resume_session_id
    ).run()
