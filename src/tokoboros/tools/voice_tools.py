"""Voice tools — transcribe audio (STT) and speak text (TTS).

``transcribe_audio`` reads a local audio file and returns its transcript;
``speak_text`` synthesizes speech to a local audio file. Both go through the
OpenAI-compatible voice backends (:class:`SpeechToText` / :class:`TextToSpeech`),
are permission-gated (read/write path checks; disabled in the ``sandbox`` tier),
and gracefully degrade — if no API key is configured they return a friendly hint
rather than failing. Registered under the lazy ``voice`` tool group.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tokoboros.core.voice import SpeechToText, TextToSpeech, VoiceError
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


SPEAK_TEXT_SCHEMA: dict[str, Any] = {
    "name": "speak_text",
    "description": (
        "Convert text to spoken audio and save it to a local file (a voice reply, a "
        "narrated note). Provide the text and an output path ending in .mp3 / .wav / "
        ".flac / .opus / .aac. Optionally choose a voice (e.g. alloy, echo, nova)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to speak."},
            "path": {
                "type": "string",
                "description": "Output audio file path (extension sets the format; default mp3).",
            },
            "voice": {
                "type": "string",
                "description": "Optional voice name (provider-specific; default alloy).",
            },
        },
        "required": ["text", "path"],
    },
}


def _make_speak_text(gate: Any | None, tts: TextToSpeech | None) -> Callable[..., str]:
    def speak_text(text: str, path: str, voice: str = "") -> str:
        tier = _tier_blocked(gate)
        if tier is not None:
            return f"Permission Denied: text-to-speech is disabled in '{tier}' tier."

        target = _normalize_user_path(path)
        if gate is not None:
            check = gate.check_path(str(target), "write")
            if not getattr(check, "allowed", True):
                return f"Permission Denied: {getattr(check, 'reason', 'path not allowed')}"

        active = tts or TextToSpeech.from_env()
        if not active.is_configured():
            return (
                "Text-to-speech is not configured. Set OPENAI_API_KEY (or "
                "TOKOBOROS_TTS_API_KEY) — optionally TOKOBOROS_TTS_BASE_URL / "
                "TOKOBOROS_TTS_MODEL / TOKOBOROS_TTS_VOICE — then try again."
            )
        try:
            out = active.synthesize(text, target, voice=voice or None)
        except VoiceError as exc:
            return f"Speech synthesis failed: {exc}"
        except Exception as exc:  # never propagate; the agent should recover/report
            return f"Speech synthesis failed: {exc}"
        return f"Saved spoken audio to {out}"

    return speak_text


def register_voice_tools(
    registry: Any,
    gate: Any | None = None,
    stt: SpeechToText | None = None,
    tts: TextToSpeech | None = None,
) -> None:
    """Register the voice tools (``transcribe_audio``, ``speak_text``) under ``voice``."""
    registry.register(
        TRANSCRIBE_AUDIO_SCHEMA,
        _make_transcribe_audio(gate, stt),
        group="voice",
    )
    registry.register(
        SPEAK_TEXT_SCHEMA,
        _make_speak_text(gate, tts),
        group="voice",
    )
