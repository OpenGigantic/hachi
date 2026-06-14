"""
Action Approval Gate

Tier-gated approval system for destructive actions.  Provides a stable
Protocol/ABC for approvers and three concrete implementations:

- ``PolicyApprover``   — no-human connected; allow at ``unrestricted``, deny otherwise.
- ``HandlerApprover``  — TUI path; delegates to an in-process callable.
- ``RegistryApprover`` — gateway/channel path; parks the request in
  ``PendingApprovalRegistry`` and blocks until the gateway endpoint resolves it.

Public API consumed by Agent B (gateway-auth):
    get_approval_registry() -> PendingApprovalRegistry
    registry.list_pending() -> list[dict]
    registry.resolve(request_id: str, decision: PermissionDecision) -> bool
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from tokoboros.core.permissions import (
    PermissionDecision,
    PermissionRequest,
)

# ==========================================
# Protocol
# ==========================================


@runtime_checkable
class ActionApprover(Protocol):
    """Protocol for all action approvers.

    Concrete implementations must implement ``require``.  The return value is
    ``(approved, reason)`` where *reason* is shown to the user or logged.
    """

    def require(self, action: str, summary: str, detail: str = "") -> tuple[bool, str]:
        """Gate a single destructive action.

        Args:
            action:  Canonical action name, e.g. ``"send_email"``, ``"trash_file"``.
            summary: Short human-readable description of what would happen.
            detail:  Optional extended context for the approval UI.

        Returns:
            ``(True, reason)`` if the action is approved, ``(False, reason)`` if denied.
        """
        ...


# ==========================================
# Pending Approval Registry
# ==========================================


class _PendingEntry:
    """Internal entry tracking one pending approval request."""

    __slots__ = ("request", "event", "decision")

    def __init__(self, request: PermissionRequest) -> None:
        self.request = request
        self.event: threading.Event = threading.Event()
        self.decision: PermissionDecision | None = None


class PendingApprovalRegistry:
    """Process-wide store for non-interactive approval requests.

    Used by the gateway/API path when no TUI handler is connected.
    The gateway's ``/approvals`` REST endpoint reads ``list_pending()`` and
    calls ``resolve()`` to unblock the waiting tool call.

    This class is thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _PendingEntry] = {}

    # ── Public API (stable — consumed by Agent B) ──

    def create(self, request: PermissionRequest) -> str:
        """Store *request* and return its ``request_id``.

        A :class:`threading.Event` is created for each entry so that
        :meth:`wait` can block until :meth:`resolve` is called.
        """
        entry = _PendingEntry(request)
        with self._lock:
            self._entries[request.request_id] = entry
        return request.request_id

    def list_pending(self) -> list[dict[str, Any]]:
        """Return all unresolved pending requests as serialisable dicts.

        Each dict contains:
            ``request_id``, ``operation``, ``tool``, ``target``,
            ``reason``, ``policy_summary``.
        """
        with self._lock:
            return [
                {
                    "request_id": e.request.request_id,
                    "operation": e.request.operation,
                    "tool": e.request.tool,
                    "target": e.request.target,
                    "reason": e.request.reason,
                    "policy_summary": e.request.policy_summary,
                }
                for e in self._entries.values()
                if not e.event.is_set()
            ]

    def resolve(self, request_id: str, decision: PermissionDecision) -> bool:
        """Record *decision* and unblock any thread waiting on *request_id*.

        Returns ``True`` if the entry existed and was resolved, ``False``
        if *request_id* is unknown.
        """
        with self._lock:
            entry = self._entries.get(request_id)
        if entry is None:
            return False
        entry.decision = decision
        entry.event.set()
        return True

    def wait(self, request_id: str, timeout: float) -> PermissionDecision:
        """Block until *request_id* is resolved or *timeout* seconds pass.

        On timeout returns a deny decision with rationale ``"approval timed out"``
        so the tool can distinguish a timeout from an explicit user deny.
        """
        with self._lock:
            entry = self._entries.get(request_id)
        if entry is None:
            return PermissionDecision(action="deny", rationale="unknown request_id")
        signalled = entry.event.wait(timeout=timeout)
        if not signalled:
            return PermissionDecision(action="deny", rationale="approval timed out")
        decision = entry.decision
        assert decision is not None  # event is set only after assignment
        return decision

    def _cleanup(self, request_id: str) -> None:
        """Remove a resolved entry. Called internally after wait() returns."""
        with self._lock:
            self._entries.pop(request_id, None)


# ── Module-level singleton ──

_registry: PendingApprovalRegistry | None = None
_registry_lock = threading.Lock()


def get_approval_registry() -> PendingApprovalRegistry:
    """Return the process-wide :class:`PendingApprovalRegistry` singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PendingApprovalRegistry()
    return _registry


# ==========================================
# Concrete Approvers
# ==========================================


class PolicyApprover:
    """Tier-only approver used when no human approver is connected.

    - ``unrestricted`` tier  → auto-approve.
    - ``guided`` / ``sandbox`` → deny with an actionable message.
    """

    def __init__(self, tier: str) -> None:
        """
        Args:
            tier: The active permission tier (``"unrestricted"``, ``"guided"``,
                  ``"sandbox"``).
        """
        self._tier = tier

    def require(self, action: str, summary: str, detail: str = "") -> tuple[bool, str]:
        """Return ``(True, ...)`` only for ``unrestricted`` tier.

        Args:
            action:  Canonical action name.
            summary: Human-readable action summary.
            detail:  Optional extended context.
        """
        if self._tier == "unrestricted":
            return (True, "auto-approved (unrestricted tier)")
        return (
            False,
            "This action needs human approval. Connect the TUI or web companion to approve, "
            "or set tier=unrestricted in ~/.tokoboros/device-permissions.og.",
        )


class HandlerApprover:
    """TUI-path approver: delegates to an in-process callable.

    Builds a :class:`~tokoboros.core.permissions.PermissionRequest`, emits
    ``permission.requested`` / ``permission.decided`` events, then calls
    *handler* and maps the decision to a ``(bool, str)`` pair.
    """

    def __init__(
        self,
        handler: Callable[[PermissionRequest], PermissionDecision],
        emit: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """
        Args:
            handler: Callable that presents the request to the user (e.g. TUI modal)
                     and returns a :class:`~tokoboros.core.permissions.PermissionDecision`.
            emit:    Event emitter accepting ``(event_name, payload_dict)``.
        """
        self._handler = handler
        self._emit = emit

    def require(self, action: str, summary: str, detail: str = "") -> tuple[bool, str]:
        """Call the TUI handler and return ``(approved, reason)``.

        Args:
            action:  Canonical action name.
            summary: Human-readable action summary.
            detail:  Optional extended context.
        """
        request = PermissionRequest(
            request_id=uuid.uuid4().hex,
            operation="action",
            tool=action,
            target=summary,
            reason=detail or summary,
            policy_summary=f"action:{action}",
        )

        self._emit(
            "permission.requested",
            {
                "request_id": request.request_id,
                "operation": request.operation,
                "tool": request.tool,
                "target": request.target,
                "reason": request.reason,
                "policy_summary": request.policy_summary,
            },
        )

        try:
            decision = self._handler(request)
        except Exception as exc:
            decision = PermissionDecision(action="deny", rationale=f"handler error: {exc}")

        self._emit(
            "permission.decided",
            {
                "request_id": request.request_id,
                "operation": request.operation,
                "tool": request.tool,
                "target": request.target,
                "decision": decision.action,
                "reason": decision.rationale or summary,
            },
        )

        if decision.action in ("approve", "allow_once"):
            return (True, decision.rationale or "approved")
        return (False, decision.rationale or "denied by operator")


class RegistryApprover:
    """Gateway/channel-path approver: parks the request in the registry and blocks.

    Creates a :class:`~tokoboros.core.permissions.PermissionRequest`, stores it
    in the :class:`PendingApprovalRegistry`, emits ``permission.requested``, then
    blocks on :meth:`~PendingApprovalRegistry.wait` until the gateway endpoint calls
    :meth:`~PendingApprovalRegistry.resolve` or the timeout expires.
    """

    def __init__(
        self,
        registry: PendingApprovalRegistry,
        emit: Callable[[str, dict[str, Any]], None],
        timeout: float = 180.0,
    ) -> None:
        """
        Args:
            registry: The process-wide :class:`PendingApprovalRegistry`.
            emit:     Event emitter accepting ``(event_name, payload_dict)``.
            timeout:  Seconds to wait before treating the request as timed-out deny.
        """
        self._registry = registry
        self._emit = emit
        self._timeout = timeout

    def require(self, action: str, summary: str, detail: str = "") -> tuple[bool, str]:
        """Park the request and block until resolved or timed out.

        Args:
            action:  Canonical action name.
            summary: Human-readable action summary.
            detail:  Optional extended context.
        """
        request = PermissionRequest(
            request_id=uuid.uuid4().hex,
            operation="action",
            tool=action,
            target=summary,
            reason=detail or summary,
            policy_summary=f"action:{action}",
        )

        request_id = self._registry.create(request)

        self._emit(
            "permission.requested",
            {
                "request_id": request_id,
                "operation": request.operation,
                "tool": request.tool,
                "target": request.target,
                "reason": request.reason,
                "policy_summary": request.policy_summary,
            },
        )

        decision = self._registry.wait(request_id, self._timeout)
        self._registry._cleanup(request_id)

        self._emit(
            "permission.decided",
            {
                "request_id": request_id,
                "operation": request.operation,
                "tool": request.tool,
                "target": request.target,
                "decision": decision.action,
                "reason": decision.rationale,
            },
        )

        if decision.action in ("approve", "allow_once"):
            return (True, decision.rationale or "approved")
        return (False, decision.rationale or "denied")
