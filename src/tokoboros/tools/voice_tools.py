"""Speech-to-text tool — let the agent transcribe a local audio file.

``transcribe_audio`` reads a local audio file and returns its transcript via an
OpenAI-compatible transcription backend (:class:`SpeechToText`). Permission-gated
(same path check as ``read_file``; disabled in the ``sandbox`` tier) and
gracefully degraded — if no API key is configured it returns a friendly hint
rather than failing. Registered under the lazy ``voice`` tool group.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tokoboros.core.voice import SpeechToText, VoiceError
from tokoboros.tools.os_tools import _normalize_user_path

_BLOCKED_TIERS = {"sandbox"}

TRANSCRIBE_AUDIO_SCHEMA: dict[str, Any] = {
    "name": "transcribe_audio",
    "description": (
        "Transcribe a local audio file (voice note, recording, voicemail) to text so "
        "you can read, summarize, or act on what was said. Supports mp3, m4a, wav, webm, "
        "flac, ogg, and more. Provide the path to the audio file."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Local audio file path (absolute, ~, or relative).",
            },
        },
        "required": ["path"],
    },
}


def _tier_blocked(gate: Any | None) -> str | None:
    if gate is None:
        return None
    tier = getattr(gate, "tier", None)
    return tier if tier in _BLOCKED_TIERS else None


def _make_transcribe_audio(
    gate: Any | None, stt: SpeechToText | None
) -> Callable[[str], str]:
    def transcribe_audio(path: str) -> str:
        tier = _tier_blocked(gate)
        if tier is not None:
            return f"Permission Denied: audio transcription is disabled in '{tier}' tier."

        target = _normalize_user_path(path)
        if gate is not None:
            check = gate.check_path(str(target), "read")
            if not getattr(check, "allowed", True):
                return f"Permission Denied: {getattr(check, 'reason', 'path not allowed')}"

        active = stt or SpeechToText.from_env()
        if not active.is_configured():
            return (
                "Speech-to-text is not configured. Set OPENAI_API_KEY (or "
                "TOKOBOROS_STT_API_KEY) — optionally TOKOBOROS_STT_BASE_URL / "
                "TOKOBOROS_STT_MODEL for a non-OpenAI Whisper endpoint — then try again."
            )
        try:
            text = active.transcribe(target)
        except VoiceError as exc:
            return f"Transcription failed: {exc}"
        except Exception as exc:  # never propagate; the agent should recover/report
            return f"Transcription failed: {exc}"
        return f"Transcript of {target.name}:\n\n{text}"

    return transcribe_audio


def register_voice_tools(
    registry: Any, gate: Any | None = None, stt: SpeechToText | None = None
) -> None:
    """Register the ``transcribe_audio`` tool under the lazy ``voice`` group."""
    registry.register(
        TRANSCRIBE_AUDIO_SCHEMA,
        _make_transcribe_audio(gate, stt),
        group="voice",
    )
