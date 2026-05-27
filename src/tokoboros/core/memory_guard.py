"""
Memory Content Security Scanner

Scans all inbound memory content for prompt injection, role hijacking,
exfiltration patterns, and invisible Unicode tricks before it reaches
the ChromaDB vault.

This is critical because recalled memories are injected directly into the
system prompt — an unsanitized fact becomes a persistent injection vector
on every future turn.

Usage:
    from tokoboros.core.memory_guard import scan

    is_safe, threat_id = scan(content)
    if not is_safe:
        return f"BLOCKED:{threat_id}"
"""

import re
from typing import Optional, Tuple

# --- Threat Pattern Registry ---
# Each entry: (compiled regex, human-readable threat ID)
# Patterns are deliberately broad — false positives are preferable to
# letting a real injection into the permanent memory store.

_THREAT_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (re.compile(r'ignore\s+(previous|all|above|prior|earlier)\s+instructions', re.IGNORECASE),
     "prompt_injection"),

    (re.compile(r'you\s+are\s+now\s+', re.IGNORECASE),
     "role_hijack"),

    (re.compile(r'do\s+not\s+tell\s+the\s+user', re.IGNORECASE),
     "deception"),

    (re.compile(r'(?:forget|disregard|override)\s+(?:your|all|the)\s+(?:rules|instructions|system)', re.IGNORECASE),
     "instruction_override"),

    (re.compile(r'<\s*/?\s*(?:system|prompt|instruction)\s*>', re.IGNORECASE),
     "xml_injection"),

    (re.compile(r'(?:act|behave|respond)\s+as\s+(?:if|though)\s+you\s+(?:are|were)', re.IGNORECASE),
     "persona_override"),
]

# Zero-width and invisible Unicode characters that can be used to hide
# injections inside seemingly benign text.
_INVISIBLE_CHARS = re.compile(r'[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f]')


def scan(content: str) -> Tuple[bool, Optional[str]]:
    """Scan memory content for security threats.

    Args:
        content: The raw fact string to validate.

    Returns:
        A tuple of (is_safe, threat_id).
        is_safe is True when the content passes all checks.
        threat_id is None when safe, otherwise a short identifier
        like "prompt_injection" or "invisible_unicode".
    """
    if not content or not isinstance(content, str):
        return True, None

    # Check for invisible Unicode (must come first — these can hide other patterns)
    if _INVISIBLE_CHARS.search(content):
        return False, "invisible_unicode"

    # Check against all threat patterns
    for pattern, threat_id in _THREAT_PATTERNS:
        if pattern.search(content):
            return False, threat_id

    return True, None
