"""Outbound secret-leak guard.

Detects and redacts common credential formats (API keys, tokens, private keys)
so the agent can't accidentally exfiltrate a secret it happened to read — e.g. by
posting a config file's contents to a Slack/Discord channel. The patterns are
deliberately specific (provider-prefixed keys, PEM blocks) to keep false positives
low; generic high-entropy heuristics are intentionally avoided.

Pure and dependency-free so it's trivially testable and reusable across any
outbound surface (messaging now; REST/browser are natural follow-ups).
"""

from __future__ import annotations

import os
import re

REDACTION_PLACEHOLDER = "[REDACTED SECRET]"

# (kind, compiled pattern). Order matters: the multi-line PEM block runs first so
# its body is redacted as a unit, and the more specific anthropic key precedes the
# generic openai-style key.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
]


def find_secret_kinds(text: str) -> list[str]:
    """Return the kinds of secret detected in ``text`` (deduped, in pattern order)."""
    return [kind for kind, pat in _PATTERNS if pat.search(text)]


def contains_secret(text: str) -> bool:
    """True if ``text`` contains at least one recognized secret."""
    return any(pat.search(text) for _kind, pat in _PATTERNS)


def redact_secrets(text: str, placeholder: str = REDACTION_PLACEHOLDER) -> tuple[str, int]:
    """Replace every recognized secret in ``text`` with ``placeholder``.

    Args:
        text: The text to scrub.
        placeholder: The replacement string.

    Returns:
        ``(redacted_text, count)`` where ``count`` is how many secrets were replaced.
    """
    total = 0
    redacted = text
    for _kind, pat in _PATTERNS:
        redacted, n = pat.subn(placeholder, redacted)
        total += n
    return redacted, total


def outbound_redaction_disabled() -> bool:
    """True if the user opted out of outbound secret redaction (power-user escape hatch).

    Controlled by ``TOKOBOROS_ALLOW_OUTBOUND_SECRETS`` — the single env var honored by
    every outbound surface (messaging, REST, scheduled-webhook delivery).
    """
    return os.environ.get("TOKOBOROS_ALLOW_OUTBOUND_SECRETS", "").lower() in {"1", "true", "yes"}


def redact_outbound(text: str) -> tuple[str, int]:
    """Redact secrets from outbound text unless the user opted out. Returns ``(text, count)``."""
    if outbound_redaction_disabled():
        return text, 0
    return redact_secrets(text)
