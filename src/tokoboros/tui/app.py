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
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib import error, request

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult, NoScreen, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, RichLog, Static, TextArea

from tokoboros.core.onboarding import OnboardingService, SetupStep
from tokoboros.core.permissions import PermissionDecision, PermissionRequest
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
    format_ops,
    format_settings,
)
from tokoboros.tui.state_store import TUIStateSnapshot, TUIStateStore
from tokoboros.tui.theme import TOKOBOROS_THEME
from tokoboros.tui.widgets import (
    AgentMessage,
    ErrorCard,
    SidebarPanel,
    TokoGlyph,
    TokoSplash,
    ToolCard,
    UserBubble,
)

TierName = Literal["lightning", "recommended", "enhanced", "power"]

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


def _looks_like_error(text: str) -> bool:
    """Heuristic: does a tool-result preview describe a failure?"""
    lowered = text.lower()
    return any(token in lowered for token in ("failed", "error:", "traceback", "exception"))


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
  content-align: center middle;
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
  padding: 0 1;
  border-left: thick $accent;
  background: $surface;
}

.tool-card.done {
  border-left: thick $success;
}

.tool-card.failed {
  border-left: thick $error;
}

.tool-header {
  height: auto;
}

.tool-body {
  height: auto;
  color: $text-muted;
}

.system-line {
  height: auto;
  margin: 0 0 1 0;
  color: $text-muted;
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

#setup-body {
  margin: 1 2;
  border: round #30363d;
  background: $surface;
  padding: 1 2;
  height: 1fr;
}

#setup-options {
  height: auto;
  padding: 0 1;
}

#setup-options Button {
  margin: 0 0 1 0;
  width: 100%;
}

#setup-nav {
  margin: 0 2 1 2;
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
    ("?", "Toggle this help"),
    ("Escape", "Close a dialog / deny an approval"),
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
        with Vertical(id="help-card"):
            yield Static(Text("Keyboard shortcuts", style=f"bold {theme.ACCENT}"))
            yield Static(rows)
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
        self.answers.setdefault("tier", tier)

    def step_index(self) -> int:
        return self._steps.index(self.current_step)

    def set_answer(self, key: str, value: Any) -> None:
        self.answers[key] = value
        self.can_resume = True

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
        yield Static("Preparing setup…", id="setup-body")
        with Vertical(id="setup-options"):
            # All option buttons are created once; visibility is toggled per
            # step in render_step to avoid dynamic mount/remove id collisions.
            for options in _SETUP_STEP_OPTIONS.values():
                for button_id, label, desc in options:
                    yield Button(f"  {label} — {desc}", id=button_id)
        with Horizontal(id="setup-nav"):
            yield Button("← Back", id="setup-back")
            yield Button("Skip for now", id="setup-skip")
            yield Button("Next →", id="setup-next", variant="primary")
            yield Button("Finish", id="setup-finish", variant="success")

    def on_mount(self) -> None:
        self.render_step()

    def _controller(self) -> SetupWizardController | None:
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            return app.setup_controller
        return None

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

    def render_step(self) -> None:
        controller = self._controller()
        if not controller:
            return

        step = controller.current_step
        idx = controller.step_index() + 1
        total = len(controller.steps)

        dots = " ".join("●" if i < idx else "○" for i in range(total))
        self.query_one("#setup-progress", Static).update(
            Text.assemble(
                (f"Step {idx}/{total}  ", theme.MUTED),
                (dots, theme.PRIMARY),
            )
        )

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

        self.query_one("#setup-back", Button).disabled = controller.step_index() == 0
        self.query_one("#setup-next", Button).disabled = step == "finish"
        self.query_one("#setup-skip", Button).disabled = step == "finish"
        self.query_one("#setup-finish", Button).disabled = step != "finish"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        controller = self._controller()
        if not controller:
            return

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
            app = self.app
            if isinstance(app, TokoborosTextualApp):
                app.complete_setup()
            return

        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.persist_setup_resume()
        self.render_step()


class SessionScreen(Screen):
    """Primary chat workspace: scrollable transcript, live sidebar, composer."""

    def __init__(self) -> None:
        super().__init__()
        self._tool_cards: dict[str, list[ToolCard]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="session-root"):
            yield VerticalScroll(id="transcript")
            yield SidebarPanel(id="sidebar")
        yield Input(
            placeholder="Send a prompt  ·  Enter to submit  ·  ? for help",
            id="composer",
        )

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

    def _append(self, widget: Widget, *, scroll: bool = True) -> None:
        transcript = self._transcript()
        transcript.mount(widget)
        if scroll:
            transcript.scroll_end(animate=False)

    def _mount_system_line(self, text: str, *, style: str = theme.MUTED) -> None:
        # Boot/system lines must not scroll the boot splash off-screen; real
        # messages (user/agent) still scroll into view via the default path.
        self._append(Static(Text(text, style=style), classes="system-line"), scroll=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "composer":
            return
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt:
            return
        self.set_toko_state("thinking")
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.submit_prompt(prompt)

    def on_resize(self, _event: object) -> None:
        try:
            root = self.query_one("#session-root", Horizontal)
        except Exception:
            return
        root.set_class(self.size.width < 90, "narrow")

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

    def ingest_runtime_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "text.stream.delta":
            text = str(payload.get("text", "")).strip()
            if text:
                app = self.app
                if isinstance(app, TokoborosTextualApp):
                    app.mark_turn_streamed()
                self.render_assistant_text(text)
            return

        if event_type == "tool.call.started":
            name = str(payload.get("name", "tool"))
            card = ToolCard(name, _event_input_summary(payload.get("input")))
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
        return format_ops(jobs, failures, mcp, events)


class MemoryScreen(SnapshotScreen):
    title_text = "Memory"

    def _build_snapshot(self, agent: AgentSession) -> RenderableType:
        recalls = [asdict(row) for row in agent.query_memory_recall("recent context", top_k=5)]
        tasks = [asdict(row) for row in agent.snapshot_task_ledger(limit=8)]
        return format_memory(tasks, recalls)


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
        "memory": MemoryScreen,
        "integrations": IntegrationsScreen,
        "authoring": AuthoringScreen,
        "settings": SettingsScreen,
        "setup": SetupScreen,
    }
    DEFAULT_MODE = "session"

    BINDINGS = [
        Binding("ctrl+1", "mode_session", "Session"),
        Binding("f1", "mode_session", show=False),
        Binding("alt+1", "mode_session", show=False),
        Binding("ctrl+2", "mode_ops", "Ops"),
        Binding("f2", "mode_ops", show=False),
        Binding("alt+2", "mode_ops", show=False),
        Binding("ctrl+3", "mode_memory", "Memory"),
        Binding("f3", "mode_memory", show=False),
        Binding("alt+3", "mode_memory", show=False),
        Binding("ctrl+4", "mode_integrations", "Integrations"),
        Binding("f4", "mode_integrations", show=False),
        Binding("alt+4", "mode_integrations", show=False),
        Binding("ctrl+5", "mode_authoring", "Authoring"),
        Binding("f5", "mode_authoring", show=False),
        Binding("alt+5", "mode_authoring", show=False),
        Binding("ctrl+6", "mode_settings", "Settings"),
        Binding("f6", "mode_settings", show=False),
        Binding("alt+6", "mode_settings", show=False),
        Binding("ctrl+7", "mode_setup", "Setup"),
        Binding("f7", "mode_setup", show=False),
        Binding("alt+7", "mode_setup", show=False),
        Binding("ctrl+p", "command_palette", "Palette"),
        Binding("ctrl+c", "interrupt", "Interrupt"),
        Binding("ctrl+r", "refresh_current", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, gateway_base_url: str | None = None) -> None:
        super().__init__()
        self._agent: AgentSession | None = None
        self._boot_error: str | None = None
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

    @property
    def hydrated_draft(self) -> str:
        return self._hydrated_state.draft_input

    @property
    def hydrated_authoring_target(self) -> str:
        return self._hydrated_state.authoring_target

    @property
    def setup_controller(self) -> SetupWizardController | None:
        return self._setup_controller

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

    def _provider_summary(self) -> str:
        config = self._runtime_config()
        provider = config.get("provider", {})
        if not isinstance(provider, dict):
            return "unknown / unknown"
        kind = str(provider.get("kind", "unknown"))
        model = str(provider.get("model", "unknown"))
        return f"{kind} / {model}"

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

    def _runtime_banner_text(self) -> str:
        workspace = get_workspace_dir().name
        session_id = self._gateway_session_id
        if not session_id and self._agent:
            try:
                session_id = self._agent.snapshot_session_state().session_id
            except Exception:
                session_id = ""

        session_label = session_id[:12] if session_id else "none"
        return (
            f"workspace={workspace} | session={session_label} | "
            f"provider={self._provider_summary()} | tier={self._permission_tier()} | "
            f"runtime={self.runtime_status_text()} | jobs={self._active_job_count()} | "
            f"approvals={self._pending_approval_count()} | warnings={self._warning_count()} | "
            f"stream={self._event_stream_signal()}"
        )

    def _update_runtime_banner(self) -> None:
        try:
            banner = self.query_one("#runtime-banner", Static)
        except Exception:
            return
        banner.update(self._runtime_banner_text())

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
        screen = self._current_screen()
        if not isinstance(screen, SessionScreen):
            return
        try:
            sidebar = screen.query_one("#sidebar", SidebarPanel)
        except Exception:
            return
        sidebar.update_info(self._sidebar_info())

    def _note_activity(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "session.chat.started":
            self._activity = ("Working…", "running")
        elif event_type == "thinking.step":
            self._activity = (f"Thinking · step {payload.get('iteration', '?')}", "running")
        elif event_type == "tool.call.started":
            name = str(payload.get("name", "tool"))
            self._activity = (f"Running {name}…", "running")
            self._recent_tools.append({"name": name, "status": "running"})
            self._recent_tools = self._recent_tools[-12:]
        elif event_type == "tool.call.completed":
            name = str(payload.get("name", "tool"))
            preview = _sanitize_legacy_rich_markup(str(payload.get("result_preview", "")))
            status = "failed" if _looks_like_error(preview) else "completed"
            for tool in reversed(self._recent_tools):
                if tool["name"] == name and tool["status"] == "running":
                    tool["status"] = status
                    break
        elif event_type == "permission.requested":
            self._activity = ("Waiting for approval", "pending")
        elif event_type == "permission.decided":
            self._activity = ("Working…", "running")
        elif event_type == "usage.updated":
            self._usage = dict(payload)
        elif event_type == "job.started":
            self._activity = (f"Running job: {payload.get('label', 'job')}", "running")
        elif event_type in {"session.chat.completed", "job.completed"}:
            self._activity = ("Idle", "idle")
        elif event_type in {"boot.failed", "job.failed"}:
            self._activity = ("Failed", "failed")

    def compose(self) -> ComposeResult:
        # The Toko brand glyph *is* the header: a centered wordmark whose eye
        # encodes live runtime state. Replaces the stock Header so the two do
        # not collide at the docked top row.
        yield TokoGlyph(id="toko-header")
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

        self._update_runtime_banner()

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

        if not self._queue_ui_callback(self._show_permission_modal, request):
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
        self._update_runtime_banner()
        self.refresh_sidebar()

    def _on_boot_complete(self, agent: AgentSession | None, error_text: str | None) -> None:
        self._agent = agent
        self._boot_error = error_text
        self._update_runtime_banner()

        if error_text:
            self.notify(f"Boot failed: {error_text}", severity="error", timeout=8)
            screen = self._current_screen()
            if isinstance(screen, SessionScreen):
                screen.ingest_runtime_event("boot.failed", {"error": error_text})
            return

        self.notify("Tokoboros is online", timeout=3)
        self._activity = ("Idle", "idle")
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.ingest_runtime_event("boot.complete", {})

        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self.refresh_sidebar()

    def submit_prompt(self, prompt: str) -> None:
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
            screen.append_user_prompt(prompt)
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
        self._update_runtime_banner()
        self.refresh_sidebar()
        self._persist_ui_state()

    def action_interrupt(self) -> None:
        self._gateway_stop_event.set()
        self._resolve_all_pending_approvals("Interrupted by operator")
        self._update_runtime_banner()
        self.notify(
            "Interrupt requested. Event polling stopped and pending approvals were dismissed.",
            timeout=4,
        )

    def action_help(self) -> None:
        if isinstance(self._current_screen(), HelpModal):
            return
        self.push_screen(HelpModal())

    def action_refresh_current(self) -> None:
        screen = self._current_screen()
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self._update_runtime_banner()
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
        self._update_runtime_banner()
        self._persist_ui_state()

    def action_mode_session(self) -> None:
        self._switch_mode("session")

    def action_mode_ops(self) -> None:
        self._switch_mode("ops")

    def action_mode_memory(self) -> None:
        self._switch_mode("memory")

    def action_mode_integrations(self) -> None:
        self._switch_mode("integrations")

    def action_mode_authoring(self) -> None:
        self._switch_mode("authoring")

    def action_mode_settings(self) -> None:
        self._switch_mode("settings")

    def action_mode_setup(self) -> None:
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
        self._update_runtime_banner()

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
        self._update_runtime_banner()
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
        self._update_runtime_banner()

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
        self._update_runtime_banner()

    def on_unmount(self) -> None:
        self._gateway_stop_event.set()
        self._resolve_all_pending_approvals("App shutting down")
        if self._boot_thread and self._boot_thread.is_alive():
            self._boot_thread.join(timeout=1.0)
        if self._chat_thread and self._chat_thread.is_alive():
            self._chat_thread.join(timeout=1.0)
        if self._gateway_event_thread and self._gateway_event_thread.is_alive():
            self._gateway_event_thread.join(timeout=1.0)
        self._persist_ui_state()
        if self._agent:
            try:
                self._agent.shutdown()
            except Exception:
                pass
        self._state_store.shutdown()


def run_tui(gateway_base_url: str | None = None) -> None:
    """Entrypoint used by the CLI command."""
    TokoborosTextualApp(gateway_base_url=gateway_base_url).run()
