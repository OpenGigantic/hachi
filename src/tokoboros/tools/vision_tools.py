"""Vision input tool — let the agent *look at* an image (local file or URL).

``view_image`` reads an image (a local file path **or** an http(s) URL), base64-
encodes it, and returns it as an Anthropic image content block (wrapped in
:class:`ToolResultBlocks`) so the model sees the actual pixels on its next turn and
can describe / reason about them. This is the multimodal-input capability
(screenshots, photos, diagrams, charts) that brings Tokoboros to parity with
assistants that accept images.

Design notes:
    - **Provider-gated.** Only the Anthropic provider path passes image blocks
      through natively. For any other provider the tool returns a plain-text
      notice instead of an image block, so an OpenAI-compatible/Ollama endpoint is
      never sent an Anthropic-shaped block it can't parse.
    - **Permission-gated.** Local reads go through the same ``PermissionGate`` path
      check as ``read_file``; URL fetches are SSRF-validated via ``net_guard``. The
      whole tool is disabled in the ``sandbox`` tier.
    - **Lean deps.** stdlib ``base64`` + the already-present ``httpx``; media type is
      inferred from the extension or the response Content-Type (Anthropic natively
      decodes PNG/JPEG/GIF/WebP).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from tokoboros.core.net_guard import validate_outbound_url
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
_VALID_MEDIA: set[str] = set(_MEDIA_TYPES.values())

# Hard ceiling on the raw image bytes we will encode. Anthropic rejects images
# above ~5 MB; cap here so we fail fast with a friendly message instead of a 400.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Browsing/vision is meaningless (and disabled) in the read-only sandbox tier.
_BLOCKED_TIERS = {"sandbox"}

VIEW_IMAGE_SCHEMA: dict[str, Any] = {
    "name": "view_image",
    "description": (
        "Look at an image — a local file (screenshot, photo, diagram, chart, scanned "
        "document) OR an http(s) image URL — so you can describe or reason about its "
        "visual contents. Use this whenever the user references an image/screenshot or "
        "asks what something looks like. Supported formats: PNG, JPEG, GIF, WebP."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local image path (absolute, ~, or relative) OR an http(s) image URL."
                ),
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


def _allow_private() -> bool:
    return os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _too_large(size: int, limit: int) -> str:
    return f"Error: image is {size // 1024} KB, exceeding the {limit // 1024} KB limit for viewing."


def _read_local_image(path: str, gate: Any | None) -> tuple[bytes, str, str] | str:
    """Return ``(raw_bytes, media_type, label)`` for a local image, or an error string."""
    target = _normalize_user_path(path)
    if gate is not None:
        check = gate.check_path(str(target), "read")
        if not getattr(check, "allowed", True):
            return f"Permission Denied: {getattr(check, 'reason', 'path not allowed')}"
    if not target.exists() or not target.is_file():
        return f"Error: image file not found: {target}"
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    if media_type is None:
        return (
            f"Error: unsupported image type '{target.suffix or '(none)'}'. "
            f"Supported: {', '.join(sorted(_MEDIA_TYPES))}."
        )
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return f"Error reading image: {exc}"
    gate_cap = getattr(gate, "max_file_size_bytes", None) if gate is not None else None
    limit = min(_MAX_IMAGE_BYTES, gate_cap) if isinstance(gate_cap, int) else _MAX_IMAGE_BYTES
    if len(raw) > limit:
        return _too_large(len(raw), limit)
    return raw, media_type, str(target)


def _fetch_url_image(url: str) -> tuple[bytes, str, str] | str:
    """Download an image from an http(s) URL. Returns ``(raw, media_type, label)`` or an error."""
    ok, reason = validate_outbound_url(url, allow_private=_allow_private())
    if not ok:
        return f"Blocked URL: {reason}"
    try:
        # follow_redirects=False: a redirect to an internal host would bypass the
        # SSRF check above (consistent with the REST adapter / browser policy).
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw = resp.content
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    except httpx.HTTPStatusError as exc:
        return f"Error fetching image: HTTP {exc.response.status_code}"
    except Exception as exc:
        return f"Error fetching image: {exc}"

    media_type = (
        ctype
        if ctype in _VALID_MEDIA
        else _MEDIA_TYPES.get(Path(urlparse(url).path).suffix.lower())
    )
    if media_type is None:
        return (
            f"Error: URL did not return a supported image (content-type: "
            f"{ctype or 'unknown'}). Supported: PNG, JPEG, GIF, WebP."
        )
    if len(raw) > _MAX_IMAGE_BYTES:
        return _too_large(len(raw), _MAX_IMAGE_BYTES)
    return raw, media_type, url


def _make_view_image(
    gate: Any | None, provider_supports_images: bool
) -> Callable[..., ToolResultBlocks | str]:
    """Build the ``view_image`` callable bound to a permission gate and provider."""

    def view_image(path: str, prompt: str = "") -> ToolResultBlocks | str:
        tier = _tier_blocked(gate)
        if tier is not None:
            return f"Permission Denied: viewing images is disabled in '{tier}' tier."

        is_url = path.strip().lower().startswith(("http://", "https://"))
        loaded = _fetch_url_image(path.strip()) if is_url else _read_local_image(path, gate)
        if isinstance(loaded, str):
            return loaded  # error message
        raw, media_type, label = loaded

        size_kb = max(1, len(raw) // 1024)
        focus = prompt.strip() or "Describe what you see and use it to answer the user's request."

        if not provider_supports_images:
            # Non-Anthropic providers don't take Anthropic image blocks; don't risk a
            # malformed request — report the image instead of analyzing it.
            return (
                f"Image '{label}' ({size_kb} KB, {media_type}) was loaded, but inline "
                "image analysis currently requires the Anthropic provider. Switch the "
                "provider to Anthropic to view images."
            )

        data = base64.b64encode(raw).decode("ascii")
        blocks: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            },
            {
                "type": "text",
                "text": f"Loaded image '{label}' ({size_kb} KB, {media_type}). {focus}",
            },
        ]
        return ToolResultBlocks(
            blocks=blocks, summary=f"Loaded image {label} ({media_type}, {size_kb} KB)"
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
