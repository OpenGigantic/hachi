"""Vision input tool — let the agent *look at* a local image.

``view_image`` reads a local image file, base64-encodes it, and returns it as an
Anthropic image content block (wrapped in :class:`ToolResultBlocks`) so the model
sees the actual pixels on its next turn and can describe / reason about them. This
is the multimodal-input capability (screenshots, photos, diagrams, charts) that
brings Tokoboros to parity with assistants that accept images.

Design notes:
    - **Provider-gated.** Only the Anthropic provider path passes image blocks
      through natively. For any other provider the tool returns a plain-text
      notice instead of an image block, so an OpenAI-compatible/Ollama endpoint is
      never sent an Anthropic-shaped block it can't parse.
    - **Permission-gated.** Reading the file goes through the same ``PermissionGate``
      path check as ``read_file``, and the tool is disabled in the ``sandbox`` tier.
    - **No heavy deps.** Uses stdlib ``base64`` only; media type is inferred from
      the extension (Anthropic natively decodes PNG/JPEG/GIF/WebP).
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from tokoboros.core.tool_results import ToolResultBlocks
from tokoboros.tools.os_tools import _normalize_user_path

# Anthropic-decodable image media types, keyed by lowercased file extension.
_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Hard ceiling on the raw image bytes we will encode. Anthropic rejects images
# above ~5 MB; cap here so we fail fast with a friendly message instead of a 400.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Browsing/vision is meaningless (and disabled) in the read-only sandbox tier.
_BLOCKED_TIERS = {"sandbox"}

VIEW_IMAGE_SCHEMA: dict[str, Any] = {
    "name": "view_image",
    "description": (
        "Look at a local image file (screenshot, photo, diagram, chart, scanned "
        "document) so you can describe or reason about its visual contents. Use "
        "this whenever the user references an image/screenshot on disk or asks what "
        "something looks like. Supported formats: PNG, JPEG, GIF, WebP."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the image file (absolute, ~, or relative).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Optional focus for the analysis, e.g. 'read the error message' "
                    "or 'summarize the chart'. Defaults to a general description."
                ),
            },
        },
        "required": ["path"],
    },
}


def _tier_blocked(gate: Any | None) -> str | None:
    """Return the blocking tier name if vision is disallowed, else None."""
    if gate is None:
        return None
    tier = getattr(gate, "tier", None)
    return tier if tier in _BLOCKED_TIERS else None


def _make_view_image(
    gate: Any | None, provider_supports_images: bool
) -> Callable[..., ToolResultBlocks | str]:
    """Build the ``view_image`` callable bound to a permission gate and provider."""

    def view_image(path: str, prompt: str = "") -> ToolResultBlocks | str:
        tier = _tier_blocked(gate)
        if tier is not None:
            return f"Permission Denied: viewing images is disabled in '{tier}' tier."

        target = _normalize_user_path(path)

        if gate is not None:
            check = gate.check_path(str(target), "read")
            if not getattr(check, "allowed", True):
                return f"Permission Denied: {getattr(check, 'reason', 'path not allowed')}"

        if not target.exists() or not target.is_file():
            return f"Error: image file not found: {target}"

        media_type = _MEDIA_TYPES.get(target.suffix.lower())
        if media_type is None:
            supported = ", ".join(sorted(_MEDIA_TYPES))
            return (
                f"Error: unsupported image type '{target.suffix or '(none)'}'. "
                f"Supported: {supported}."
            )

        try:
            size = target.stat().st_size
        except OSError as exc:
            return f"Error reading image: {exc}"

        # Respect both the global image ceiling and any stricter per-gate file cap.
        gate_cap = getattr(gate, "max_file_size_bytes", None) if gate is not None else None
        limit = min(_MAX_IMAGE_BYTES, gate_cap) if isinstance(gate_cap, int) else _MAX_IMAGE_BYTES
        if size > limit:
            return (
                f"Error: image is {size // 1024} KB, exceeding the "
                f"{limit // 1024} KB limit for inline viewing."
            )

        size_kb = max(1, size // 1024)
        focus = prompt.strip() or "Describe what you see and use it to answer the user's request."

        if not provider_supports_images:
            # Non-Anthropic providers don't take Anthropic image blocks; don't risk a
            # malformed request — report the file instead of analyzing it.
            return (
                f"Image '{target}' ({size_kb} KB, {media_type}) is on disk, but inline "
                "image analysis currently requires the Anthropic provider. Switch the "
                "provider to Anthropic to view images."
            )

        try:
            data = base64.b64encode(target.read_bytes()).decode("ascii")
        except OSError as exc:
            return f"Error reading image: {exc}"

        blocks: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            },
            {
                "type": "text",
                "text": f"Loaded image '{target}' ({size_kb} KB, {media_type}). {focus}",
            },
        ]
        return ToolResultBlocks(
            blocks=blocks, summary=f"Loaded image {target} ({media_type}, {size_kb} KB)"
        )

    return view_image


def register_vision_tools(
    registry: Any, gate: Any | None = None, provider_kind: str = "anthropic"
) -> None:
    """Register the ``view_image`` tool under the lazy ``vision`` group.

    Args:
        registry: The ToolRegistry to register into.
        gate: Optional PermissionGate (path checks + tier gating).
        provider_kind: Active provider kind; image blocks only flow to Anthropic.
    """
    provider_supports_images = str(provider_kind).lower() == "anthropic"
    registry.register(
        VIEW_IMAGE_SCHEMA,
        _make_view_image(gate, provider_supports_images),
        group="vision",
    )
