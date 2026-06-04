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

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.app import App, ComposeResult, NoScreen, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static, TextArea

from tokoboros.core.onboarding import OnboardingService, SetupStep
from tokoboros.core.permissions import PermissionDecision, PermissionRequest
from tokoboros.gateway.orchestrator import AgentSession, boot_agent
from tokoboros.gateway.workspace import get_skills_dir, get_workspace_dir, load_config
from tokoboros.tui.authoring import (
    apply_authoring_update,
    discover_authoring_targets,
    generate_unified_patch,
    is_authoring_target,
)
from tokoboros.tui.state_store import TUIStateSnapshot, TUIStateStore

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


APP_CSS = """
Screen {
  layout: vertical;
}

#session-root {
  height: 1fr;
  width: 100%;
}

#composer {
  dock: bottom;
  margin: 1 1;
}

#transcript {
  border: round #4a8f6a;
  margin: 1 1 0 1;
  height: 1fr;
}

#timeline {
  border: round #9a7040;
  margin: 1 1 0 0;
  height: 45%;
}

#runtime-banner {
    margin: 0 1;
    padding: 0 1;
    height: auto;
}

#side-status {
  border: round #5f6fa3;
  margin: 1 1 0 0;
  height: 55%;
}

.mode-title {
  height: auto;
  margin: 1 1;
  color: #f5deb3;
}

#approval-modal {
    align: center middle;
}

#approval-card {
    width: 76;
    height: auto;
    padding: 1 2;
    border: round #a65f2a;
    background: #161514;
}

#approval-actions {
    margin-top: 1;
    height: auto;
}

#authoring-root {
    height: 1fr;
    margin: 1 1;
}

#authoring-targets {
    border: round #7d8ca8;
    width: 34%;
}

#authoring-editor {
    border: round #4a8f6a;
    height: 1fr;
}

#authoring-patch {
    border: round #9a7040;
    height: 1fr;
}

#authoring-actions {
    height: auto;
    margin: 1 1;
}

#authoring-status {
    margin: 0 1 1 1;
    color: #cfcfcf;
}

#setup-body {
    margin: 1 1;
    border: round #5f6fa3;
    padding: 1 2;
    height: 1fr;
}

#setup-options,
#setup-nav {
    margin: 0 1 1 1;
    height: auto;
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


class SetupScreen(Screen):
    """Tiered onboarding wizard with resumable state."""

    def compose(self) -> ComposeResult:
        yield Static("Tokoboros Setup", classes="mode-title")
        yield Static("Preparing setup...", id="setup-body")
        with Horizontal(id="setup-options"):
            yield Button("Lightning", id="setup-tier-lightning")
            yield Button("Recommended", id="setup-tier-recommended")
            yield Button("Enhanced", id="setup-tier-enhanced")
            yield Button("Power", id="setup-tier-power")
            yield Button("Anthropic", id="setup-provider-anthropic")
            yield Button("OpenAI-Compatible", id="setup-provider-openai")
            yield Button("Ollama", id="setup-provider-ollama")
            yield Button("Sandbox", id="setup-perm-sandbox")
            yield Button("Guided", id="setup-perm-guided")
            yield Button("Unrestricted", id="setup-perm-unrestricted")
            yield Button("Memory On", id="setup-memory-on")
            yield Button("Memory Off", id="setup-memory-off")
            yield Button("Google Later", id="setup-google-later")
            yield Button("Google Enable", id="setup-google-enable")
            yield Button("Slack Later", id="setup-slack-later")
            yield Button("Slack Enable", id="setup-slack-enable")
            yield Button("Notion Later", id="setup-notion-later")
            yield Button("Notion Enable", id="setup-notion-enable")
        with Horizontal(id="setup-nav"):
            yield Button("Back", id="setup-back")
            yield Button("Skip for now", id="setup-skip")
            yield Button("Next", id="setup-next", variant="primary")
            yield Button("Finish", id="setup-finish", variant="success")

    def on_mount(self) -> None:
        self.render_step()

    def _controller(self) -> SetupWizardController | None:
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            return app.setup_controller
        return None

    def _set_options_enabled(self, prefix: str, enabled: bool) -> None:
        for button in self.query(Button):
            if button.id and button.id.startswith(prefix):
                button.disabled = not enabled

    def render_step(self) -> None:
        controller = self._controller()
        if not controller:
            return

        step = controller.current_step
        idx = controller.step_index() + 1
        total = len(controller.steps)
        state = controller.state

        body = self.query_one("#setup-body", Static)
        body.update(
            f"Step {idx}/{total}: {step}\n\n"
            f"Tier: {controller.selected_tier}\n"
            f"Completed markers: {', '.join(state.completed_steps) or 'none'}\n"
            f"Detected env: ollama={controller.environment.get('ollama_cli')} "
            f"anthropic_env={controller.environment.get('anthropic_env')} "
            f"openai_env={controller.environment.get('openai_env')}\n\n"
            "Why this matters: setup defines the minimum runtime capabilities and safety "
            "defaults.\n"
            "Use Skip for now on optional steps and reopen setup anytime from settings or "
            "CLI."
        )

        self._set_options_enabled("setup-tier-", step == "welcome")
        self._set_options_enabled("setup-provider-", step == "provider")
        self._set_options_enabled("setup-perm-", step == "safety")
        self._set_options_enabled("setup-memory-", step == "memory")
        self._set_options_enabled("setup-google-", step == "integrations")
        self._set_options_enabled("setup-slack-", step == "integrations")
        self._set_options_enabled("setup-notion-", step == "integrations")

        self.query_one("#setup-back", Button).disabled = controller.step_index() == 0
        self.query_one("#setup-next", Button).disabled = step == "finish"
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
    """Primary session workspace with transcript, composer, and operator timeline."""

    def compose(self) -> ComposeResult:
        yield Static("Tokoboros Session", classes="mode-title")
        with Horizontal(id="session-root"):
            yield RichLog(id="transcript", highlight=True, wrap=True)
            with Vertical():
                yield RichLog(id="timeline", highlight=True, wrap=True)
                yield Static("Status pending boot...", id="side-status")
        yield Input(placeholder="Send a prompt (enter to submit)", id="composer")

    def on_mount(self) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.write("Booting runtime...")
        app = self.app
        if isinstance(app, TokoborosTextualApp):
            self.query_one("#composer", Input).value = app.hydrated_draft

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "composer":
            return

        prompt = event.value.strip()
        event.input.value = ""
        if not prompt:
            return

        app = self.app
        if isinstance(app, TokoborosTextualApp):
            app.submit_prompt(prompt)

    def append_user_prompt(self, text: str) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text.assemble(("You:", "bold cyan"), " ", text))

    def append_agent_response(self, text: str) -> None:
        transcript = self.query_one("#transcript", RichLog)
        cleaned = _sanitize_agent_markdown(text)
        if not cleaned:
            transcript.write(
                Text.assemble(("Tokoboros:", "bold magenta"), " ", ("(no response)", "dim"))
            )
            return

        transcript.write(
            Group(
                Text("Tokoboros:", style="bold magenta"),
                Padding(Markdown(cleaned), (0, 0, 0, 2)),
            )
        )

    def ingest_runtime_event(self, event_type: str, payload: dict[str, Any]) -> None:
        transcript = self.query_one("#transcript", RichLog)
        timeline = self.query_one("#timeline", RichLog)
        status = self.query_one("#side-status", Static)

        if event_type == "boot.progress":
            idx = payload.get("index", "?")
            total = payload.get("total", "?")
            msg = payload.get("message", "Booting")
            transcript.write(Text(f"Boot {idx}/{total}: {msg}", style="dim"))
            return

        if event_type == "boot.complete":
            transcript.write(Text("Runtime ready.", style="green"))
            return

        if event_type == "gateway.connect.completed":
            session_id = str(payload.get("session_id", ""))
            base_url = payload.get("base_url", "")
            transcript.write(Text.assemble(("Gateway connected:", "green"), " ", str(base_url)))
            if session_id:
                timeline.write(
                    Text.assemble(("Gateway session:", "green"), " ", f"{session_id[:12]}...")
                )
            return

        if event_type == "gateway.events.error":
            timeline.write(
                Text.assemble(
                    ("Gateway events error:", "red"),
                    " ",
                    _sanitize_legacy_rich_markup(str(payload.get("error", "unknown"))),
                )
            )
            return

        if event_type == "boot.failed":
            transcript.write(Text("Runtime boot failed.", style="red"))
            return

        if event_type == "thinking.step":
            timeline.write(Text(f"Thinking step {payload.get('iteration', '?')}", style="dim"))
            return

        if event_type == "text.stream.delta":
            text = payload.get("text", "").strip()
            if text:
                transcript.write(Text(_sanitize_legacy_rich_markup(text)[:220], style="dim"))
            return

        if event_type == "tool.call.started":
            timeline.write(
                Text.assemble(("Tool:", "yellow"), " ", str(payload.get("name", "unknown")))
            )
            return

        if event_type == "tool.call.completed":
            preview = payload.get("result_preview", "")
            timeline.write(
                Text.assemble(
                    ("Result:", "green"),
                    " ",
                    _sanitize_legacy_rich_markup(str(preview))[:180],
                )
            )
            return

        if event_type == "permission.decided":
            decision = payload.get("decision", "unknown")
            tool = payload.get("tool", "unknown")
            timeline.write(Text.assemble((f"Permission {decision}", "bold"), " for ", str(tool)))
            return

        if event_type == "permission.requested":
            timeline.write(
                Text.assemble(
                    ("Approval requested:", "bold yellow"),
                    " ",
                    str(payload.get("operation", "operation")),
                    " ",
                    str(payload.get("target", "")),
                )
            )
            status.update("Waiting for operator approval...")
            return

        if event_type == "session.chat.started":
            status.update("Assistant is working...")
            return

        if event_type == "session.chat.completed":
            status.update("Idle")
            return

        if event_type == "job.started":
            status.update(f"Running job: {payload.get('label', 'job')}")
            return

        if event_type == "job.completed":
            status.update("Idle")


class SnapshotScreen(Screen):
    """Generic mode screen that renders snapshots from the active agent session."""

    title_text = "Mode"

    def compose(self) -> ComposeResult:
        yield Static(self.title_text, classes="mode-title")
        yield Static("Waiting for runtime...", id="snapshot-body")

    def on_mount(self) -> None:
        app = self.app
        if hasattr(app, "active_agent"):
            self.refresh_from_agent(app.active_agent())

    def refresh_from_agent(self, agent: AgentSession | None) -> None:
        body = self.query_one("#snapshot-body", Static)
        if not agent:
            app = self.app
            if hasattr(app, "runtime_status_text"):
                body.update(app.runtime_status_text())
            else:
                body.update("Runtime is not ready yet.")
            return

        body.update(self._build_snapshot(agent))

    def _build_snapshot(self, agent: AgentSession) -> str:
        return "No snapshot configured"


class OpsScreen(SnapshotScreen):
    title_text = "Ops"

    def _build_snapshot(self, agent: AgentSession) -> str:
        jobs = [asdict(job) for job in agent.snapshot_active_jobs()]
        failures = [asdict(job) for job in agent.snapshot_failed_jobs(limit=10)]
        mcp = agent.snapshot_mcp_inventory()
        events = [asdict(evt) for evt in agent.snapshot_runtime_events(limit=10)]
        return (
            "Active Jobs:\n"
            + json.dumps(jobs, indent=2)
            + "\n\nFailed Jobs:\n"
            + json.dumps(failures, indent=2)
            + "\n\nMCP Inventory:\n"
            + json.dumps(
                {
                    "server_count": mcp.server_count,
                    "total_tool_count": mcp.total_tool_count,
                    "servers": [asdict(srv) for srv in mcp.servers],
                },
                indent=2,
            )
            + "\n\nRecent Events:\n"
            + json.dumps(events, indent=2)
        )


class MemoryScreen(SnapshotScreen):
    title_text = "Memory"

    def _build_snapshot(self, agent: AgentSession) -> str:
        recalls = [asdict(row) for row in agent.query_memory_recall("recent context", top_k=5)]
        tasks = [asdict(row) for row in agent.snapshot_task_ledger(limit=8)]
        return (
            "Task Ledger:\n"
            + json.dumps(tasks, indent=2)
            + "\n\nRecall:\n"
            + json.dumps(recalls, indent=2)
        )


class IntegrationsScreen(SnapshotScreen):
    title_text = "Integrations"

    def _build_snapshot(self, agent: AgentSession) -> str:
        mcp = agent.snapshot_mcp_inventory()
        return json.dumps(
            {
                "server_count": mcp.server_count,
                "total_tool_count": mcp.total_tool_count,
                "servers": [asdict(srv) for srv in mcp.servers],
            },
            indent=2,
        )


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

    def _build_snapshot(self, agent: AgentSession) -> str:
        state = agent.snapshot_session_state()
        config = load_config()
        sessions = list(agent.query_recent_sessions(limit=8))
        return (
            "Session State:\n"
            + json.dumps(asdict(state), indent=2)
            + "\n\nEffective Config:\n"
            + json.dumps(
                {
                    "provider": config.get("provider", {}),
                    "permissions": config.get("permissions", {}),
                    "memory": config.get("memory", {}),
                    "capabilities": config.get("capabilities", {}),
                },
                indent=2,
            )
            + "\n\nRecent Sessions:\n"
            + json.dumps(sessions, indent=2)
        )


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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Booting runtime...", id="runtime-banner")
        yield Footer()

    def clear_selection(self) -> None:
        """Compatibility shim for Textual startup when no screen is mounted yet."""
        try:
            super().clear_selection()
        except (NoScreen, ScreenStackError):
            # Some Textual versions raise ScreenStackError instead of NoScreen
            # during initial input mount before the first screen is on stack.
            pass

    def on_mount(self) -> None:
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

        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.ingest_runtime_event(event_type, payload)
        self._update_runtime_banner()

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
        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.ingest_runtime_event("boot.complete", {})

        if screen is not None:
            self._refresh_snapshot_mode(screen)

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

        screen = self._current_screen()
        if isinstance(screen, SessionScreen):
            screen.append_user_prompt(prompt)

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
                screen.append_agent_response(f"Error: {error_text}")
            else:
                screen.append_agent_response(response)
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self._update_runtime_banner()
        self._persist_ui_state()

    def action_interrupt(self) -> None:
        self._gateway_stop_event.set()
        self._resolve_all_pending_approvals("Interrupted by operator")
        self._update_runtime_banner()
        self.notify(
            "Interrupt requested. Event polling stopped and pending approvals were dismissed.",
            timeout=4,
        )

    def action_refresh_current(self) -> None:
        screen = self._current_screen()
        if screen is not None:
            self._refresh_snapshot_mode(screen)
        self._update_runtime_banner()
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
