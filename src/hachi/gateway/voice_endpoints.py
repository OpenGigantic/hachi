"""Voice gateway endpoints — transcribe, speak, and config management.

All functions in this module are **self-contained**: they build the required
speech provider internally from the effective config.  No ``app.state``
plumbing is required — the caller (FastAPI route handler) just calls the
function and returns the result.

Usage::

    from hachi.gateway.voice_endpoints import transcribe, speak, voice_config, set_voice_config

Error contract:
    :class:`VoiceConfigError` is raised when the requested voice feature is not
    configured (e.g. no API key).  Routes map this to HTTP 503 with an
    actionable message so the UI can prompt the user to configure the [voice]
    section.
"""

from __future__ import annotations

import logging
from typing import Any

from hachi.core.providers.speech import build_stt_provider, build_tts_provider

logger = logging.getLogger(__name__)

# Supported TTS providers for the config response.
_SUPPORTED_TTS_PROVIDERS = ["openai", "elevenlabs"]

# Canonical OpenAI TTS voices.
_OPENAI_VOICES = ["alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"]

# Formats the speak endpoint accepts (maps to the provider's fmt argument).
_AUDIO_FORMATS: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg; codecs=opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
}


class VoiceConfigError(RuntimeError):
    """Raised when a voice feature is unavailable because it is not configured.

    Routes catch this and return HTTP 503 with an actionable detail message.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_voice_cfg() -> Any | None:
    """Return the ``VoiceSettings`` from the effective config, or ``None``."""
    try:
        from hachi.core.config_loader import load_effective_config

        cfg = load_effective_config()
        return cfg.voice  # VoiceSettings | None
    except Exception:
        logger.debug("Could not load voice config", exc_info=True)
        return None


def _is_voice_configured(voice_cfg: Any | None) -> bool:
    """Return True when at least one of STT or TTS has a resolvable API key."""
    import os

    if voice_cfg is None:
        # Fall back: any OPENAI_API_KEY / HACHI_STT_API_KEY / HACHI_TTS_API_KEY is enough.
        return bool(
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("HACHI_STT_API_KEY")
            or os.environ.get("HACHI_TTS_API_KEY")
        )
    # Check env var indirection fields.
    for attr in ("stt_api_key_env", "tts_api_key_env", "elevenlabs_api_key_env"):
        env_name = getattr(voice_cfg, attr, None)
        if env_name and os.environ.get(env_name):
            return True
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("HACHI_STT_API_KEY")
        or os.environ.get("HACHI_TTS_API_KEY")
        or os.environ.get("ELEVENLABS_API_KEY")
    )


# ---------------------------------------------------------------------------
# Public endpoint helpers
# ---------------------------------------------------------------------------


def transcribe(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    model: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe *audio_bytes* to text via the configured STT backend.

    Args:
        audio_bytes: Raw audio bytes from the upload.
        filename: Original filename (used to infer the format extension).
        content_type: MIME type of the audio (e.g. ``"audio/webm"``).
        model: Override the configured STT model (e.g. ``"whisper-1"``).
        language: Optional ISO-639-1 language hint (e.g. ``"en"``).

    Returns:
        A dict containing at least ``{"text": "…"}`` plus provider metadata.

    Raises:
        VoiceConfigError: When STT is not configured (no API key).
    """
    voice_cfg = _load_voice_cfg()

    if not _is_voice_configured(voice_cfg):
        raise VoiceConfigError(
            "Speech-to-text is not configured. Set OPENAI_API_KEY or add a [voice] section "
            "to ~/.hachi/settings.toml with stt_api_key_env pointing to your key."
        )

    # If a model override is requested, temporarily shadow the setting.
    effective_cfg: Any = voice_cfg
    if model and voice_cfg is not None:
        try:
            effective_cfg = voice_cfg.model_copy(update={"stt_model": model})
        except AttributeError:
            pass  # Non-pydantic cfg object; provider will use its own default.
    elif model and voice_cfg is None:
        # No voice section at all — create a minimal dict so the provider picks up model.
        effective_cfg = {"stt_model": model}

    stt_provider = build_stt_provider(effective_cfg)
    text: str = stt_provider.transcribe(audio_bytes, media_type=content_type)

    result: dict[str, Any] = {"text": text}
    if language:
        result["language"] = language
    return result


def speak(
    text: str,
    *,
    voice: str | None = None,
    model: str | None = None,
    fmt: str = "mp3",
    provider: str | None = None,
) -> tuple[bytes, str]:
    """Synthesize *text* to audio and return ``(audio_bytes, media_type)``.

    Args:
        text: The text to convert to speech.
        voice: Override the configured TTS voice (provider-specific identifier).
        model: Override the configured TTS model.
        fmt: Output audio format.  One of ``"mp3"`` (default), ``"opus"``,
            ``"aac"``, ``"flac"``, ``"wav"``.
        provider: Force a specific TTS provider (``"openai"`` or
            ``"elevenlabs"``).  Falls back to the configured
            ``tts_provider`` when ``None``.

    Returns:
        ``(audio_bytes, media_type)`` — the raw audio and its MIME type.

    Raises:
        VoiceConfigError: When TTS is not configured.
    """
    voice_cfg = _load_voice_cfg()

    if not _is_voice_configured(voice_cfg):
        raise VoiceConfigError(
            "Text-to-speech is not configured. Set OPENAI_API_KEY or add a [voice] section "
            "to ~/.hachi/settings.toml with tts_api_key_env pointing to your key."
        )

    effective_cfg: Any = voice_cfg
    update_fields: dict[str, Any] = {}
    if model:
        update_fields["tts_model"] = model
    if provider:
        update_fields["tts_provider"] = provider
    if update_fields and voice_cfg is not None:
        try:
            effective_cfg = voice_cfg.model_copy(update=update_fields)
        except AttributeError:
            pass
    elif update_fields and voice_cfg is None:
        effective_cfg = update_fields

    tts_provider = build_tts_provider(effective_cfg)
    audio_bytes: bytes = tts_provider.synthesize(text, voice=voice, fmt=fmt)

    # Normalise format to canonical MIME type.
    media_type = _AUDIO_FORMATS.get(fmt.lower(), "audio/mpeg")
    return audio_bytes, media_type


def voice_config() -> dict[str, Any]:
    """Return the current voice configuration summary for the UI.

    Returns:
        A dict with keys:
        - ``stt_provider`` — e.g. ``"openai"``
        - ``tts_provider`` — e.g. ``"openai"`` or ``"elevenlabs"``
        - ``tts_voice`` — the configured default TTS voice
        - ``stt_model`` — configured STT model or ``None``
        - ``tts_model`` — configured TTS model or ``None``
        - ``elevenlabs_voice_id`` — ElevenLabs voice ID or ``None``
        - ``available`` — ``{"tts_providers": [...], "voices": [...]}``
        - ``configured`` — ``True`` when at least one provider has a key
    """
    voice_cfg = _load_voice_cfg()
    configured = _is_voice_configured(voice_cfg)

    stt_provider = "openai"
    tts_provider = "openai"
    tts_voice: str | None = None
    stt_model: str | None = None
    tts_model: str | None = None
    elevenlabs_voice_id: str | None = None

    if voice_cfg is not None:
        stt_provider = str(getattr(voice_cfg, "stt_provider", "openai") or "openai")
        tts_provider = str(getattr(voice_cfg, "tts_provider", "openai") or "openai")
        tts_voice = getattr(voice_cfg, "tts_voice", None)
        stt_model = getattr(voice_cfg, "stt_model", None)
        tts_model = getattr(voice_cfg, "tts_model", None)
        elevenlabs_voice_id = getattr(voice_cfg, "elevenlabs_voice_id", None)

    return {
        "stt_provider": stt_provider,
        "tts_provider": tts_provider,
        "tts_voice": tts_voice or "alloy",
        "stt_model": stt_model,
        "tts_model": tts_model,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "available": {
            "tts_providers": _SUPPORTED_TTS_PROVIDERS,
            "voices": _OPENAI_VOICES,
        },
        "configured": configured,
    }


def set_voice_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Persist voice config fields and return the updated config summary.

    Writes changes to ``~/.hachi/settings.toml`` via
    :func:`~hachi.core.config_loader.write_global_config`.

    Accepted *patch* keys (all optional):
        - ``stt_provider`` — ``"openai"``
        - ``tts_provider`` — ``"openai"`` or ``"elevenlabs"``
        - ``tts_voice`` — voice name / ID
        - ``stt_model`` — model name
        - ``tts_model`` — model name
        - ``stt_base_url`` — custom STT endpoint base URL
        - ``tts_base_url`` — custom TTS endpoint base URL
        - ``stt_api_key_env`` — env-var name holding the STT key
        - ``tts_api_key_env`` — env-var name holding the TTS key
        - ``elevenlabs_api_key_env`` — env-var name for ElevenLabs key
        - ``elevenlabs_voice_id`` — ElevenLabs voice ID

    Args:
        patch: Partial voice settings to merge.

    Returns:
        The updated voice config summary from :func:`voice_config`.
    """
    from hachi.core.config_loader import load_effective_config, write_global_config
    from hachi.core.settings import VoiceSettings
    from hachi.gateway._net_guard import is_safe_outbound_url

    # Validate base URLs if provided.
    for url_field in ("stt_base_url", "tts_base_url"):
        url_val = patch.get(url_field)
        if url_val:
            if not is_safe_outbound_url(str(url_val), allow_loopback=False):
                raise VoiceConfigError(
                    f"{url_field} resolves to a private or loopback address — "
                    "only public endpoints are allowed"
                )

    # Validate provider names if provided.
    _VALID_STT_PROVIDERS = {"openai"}
    _VALID_TTS_PROVIDERS = {"openai", "elevenlabs"}
    stt_provider_val = patch.get("stt_provider")
    if stt_provider_val is not None and str(stt_provider_val) not in _VALID_STT_PROVIDERS:
        raise VoiceConfigError(
            f"stt_provider {stt_provider_val!r} is not supported; "
            f"must be one of: {', '.join(sorted(_VALID_STT_PROVIDERS))}"
        )
    tts_provider_val = patch.get("tts_provider")
    if tts_provider_val is not None and str(tts_provider_val) not in _VALID_TTS_PROVIDERS:
        raise VoiceConfigError(
            f"tts_provider {tts_provider_val!r} is not supported; "
            f"must be one of: {', '.join(sorted(_VALID_TTS_PROVIDERS))}"
        )

    current = load_effective_config()
    current_voice = current.voice or VoiceSettings()

    # Apply supported patch fields.
    allowed_fields = {
        "stt_provider",
        "tts_provider",
        "tts_voice",
        "stt_model",
        "tts_model",
        "stt_base_url",
        "tts_base_url",
        "stt_api_key_env",
        "tts_api_key_env",
        "elevenlabs_api_key_env",
        "elevenlabs_voice_id",
    }
    update: dict[str, Any] = {k: v for k, v in patch.items() if k in allowed_fields}
    if not update:
        return voice_config()

    new_voice = current_voice.model_copy(update=update)
    new_config = current.model_copy(update={"voice": new_voice})
    write_global_config(new_config)

    return voice_config()
