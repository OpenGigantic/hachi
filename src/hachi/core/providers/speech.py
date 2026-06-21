"""Speech provider contract and OpenAI-compatible adapter (Initiative F).

Design doc 19 §2-3.  ``SpeechProvider`` is the protocol for speech-to-text and
text-to-speech providers.  ``OpenAICompatibleSpeechProvider`` **wraps** the
existing :class:`~hachi.core.voice.SpeechToText` /
:class:`~hachi.core.voice.TextToSpeech` implementations — no rewrite — so
Deepgram, Groq, and any other OpenAI-compatible Whisper/TTS endpoint continues
to work via env config.

Calls are wrapped by :func:`~hachi.core.resilience.resilient_call` so
transcription and synthesis both gain timeout + bounded retry + circuit-breaker
for free without touching :mod:`~hachi.core.voice`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from hachi.core.resilience import HealthRecorder, RetryPolicy, resilient_call
from hachi.core.routing.capability import Capability

logger = logging.getLogger(__name__)


class OpenAICompatibleSpeechProvider:
    """Adapter that wraps :class:`~hachi.core.voice.SpeechToText` /
    :class:`~hachi.core.voice.TextToSpeech` under the :class:`SpeechProvider`
    protocol.

    This is the *only* class that may import from :mod:`hachi.core.voice` for
    the purpose of speech provider registration.  Keyless playback
    (:class:`~hachi.core.voice.AudioPlayer`) is not part of this adapter — it
    stays directly in the tool layer.

    Args:
        stt: Optional :class:`~hachi.core.voice.SpeechToText` instance.
            When ``None`` the provider advertises no STT capability.
        tts: Optional :class:`~hachi.core.voice.TextToSpeech` instance.
            When ``None`` the provider advertises no TTS capability.
        retry_policy: Retry/circuit-breaker policy for all calls.
            Defaults to the global :class:`~hachi.core.resilience.RetryPolicy`.
        provider_name: Registry key.  Defaults to ``"openai-compat-speech"``.
    """

    def __init__(
        self,
        *,
        stt: Any | None = None,
        tts: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        provider_name: str = "openai-compat-speech",
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._policy = retry_policy or RetryPolicy()
        self._recorder = HealthRecorder(provider_name, self._policy)
        self.name = provider_name

    # ------------------------------------------------------------------
    # SpeechProvider protocol
    # ------------------------------------------------------------------

    def capabilities(self) -> set[Capability]:
        """Return capabilities based on which backends are configured."""
        caps: set[Capability] = set()
        if self._stt is not None and self._stt.is_configured():
            caps.add(Capability.SPEECH_TO_TEXT)
        if self._tts is not None and self._tts.is_configured():
            caps.add(Capability.TEXT_TO_SPEECH)
        return caps

    def transcribe(self, audio: bytes, **opts: Any) -> str:
        """Transcribe raw audio bytes to text via the STT backend.

        Wraps :meth:`~hachi.core.voice.SpeechToText.transcribe` with
        :func:`~hachi.core.resilience.resilient_call`.

        Args:
            audio: Raw audio bytes to transcribe.
            **opts: Unused; reserved for future ``media_type`` / ``language`` kwargs.

        Returns:
            The transcript string.

        Raises:
            RuntimeError: When no STT backend is configured.
            :class:`~hachi.core.voice.VoiceError`: On transcription failure.
        """
        if self._stt is None:
            raise RuntimeError("No STT backend configured on this SpeechProvider")

        import tempfile
        from pathlib import Path

        # SpeechToText.transcribe takes a file path; write bytes to a temp file.
        media_type: str = opts.get("media_type", "audio/wav")
        ext = _media_type_to_ext(media_type)
        _stt = self._stt  # narrowed local for closure

        def _call() -> str:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio)
                tmp_path = Path(tmp.name)
            try:
                result: str = str(_stt.transcribe(tmp_path))
                return result
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        result_str: str = resilient_call(_call, self._policy, self._recorder)
        return result_str

    def synthesize(self, text: str, **opts: Any) -> bytes:
        """Synthesize *text* to audio bytes via the TTS backend.

        Wraps :meth:`~hachi.core.voice.TextToSpeech.synthesize` with
        :func:`~hachi.core.resilience.resilient_call`.

        Args:
            text: The text to convert to speech.
            **opts: Accepted kwargs: ``voice`` (str), ``fmt`` (str, default ``"mp3"``).

        Returns:
            Raw audio bytes.

        Raises:
            RuntimeError: When no TTS backend is configured.
            :class:`~hachi.core.voice.VoiceError`: On synthesis failure.
        """
        if self._tts is None:
            raise RuntimeError("No TTS backend configured on this SpeechProvider")

        import tempfile
        from pathlib import Path

        voice: str | None = opts.get("voice")
        fmt: str = opts.get("fmt", "mp3")
        _tts = self._tts  # narrowed local for closure

        def _call() -> bytes:
            with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                _tts.synthesize(text, tmp_path, voice=voice)
                return bytes(tmp_path.read_bytes())
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        result_bytes: bytes = resilient_call(_call, self._policy, self._recorder)
        return result_bytes

    # ------------------------------------------------------------------
    # Health (used by ProviderRegistry.health())
    # ------------------------------------------------------------------

    def health(self) -> Any:
        """Return health snapshot from the underlying HealthRecorder."""
        return self._recorder.snapshot()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        voice_cfg: Any | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> OpenAICompatibleSpeechProvider:
        """Build from a ``VoiceSettings`` object or plain dict.

        Precedence (matching established HACHI_* env-key pattern):
          1. ``voice_cfg`` fields (``stt_api_key``, ``stt_base_url``, etc.)
          2. ``HACHI_STT_*`` / ``HACHI_TTS_*`` env vars
          3. ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` fallback

        This matches exactly the logic inside
        :meth:`~hachi.core.voice.SpeechToText.from_env` and
        :meth:`~hachi.core.voice.TextToSpeech.from_env`, so the fallback is
        identical regardless of code path.

        Args:
            voice_cfg: A :class:`~hachi.core.settings.VoiceSettings` (or dict)
                from the ``[voice]`` config section, or ``None`` to fall back
                fully to env vars.
            retry_policy: Optional retry/circuit-breaker policy.

        Returns:
            A configured :class:`OpenAICompatibleSpeechProvider`.
        """
        from hachi.core.voice import SpeechToText, TextToSpeech

        cfg: dict[str, Any] = {}
        if voice_cfg is not None:
            if isinstance(voice_cfg, dict):
                cfg = voice_cfg
            else:
                try:
                    cfg = voice_cfg.model_dump()
                except AttributeError:
                    # Fallback for plain objects.
                    for attr in (
                        "stt_api_key",
                        "stt_base_url",
                        "stt_model",
                        "stt_api_key_env",
                        "tts_api_key",
                        "tts_base_url",
                        "tts_model",
                        "tts_voice",
                        "tts_api_key_env",
                    ):
                        val = getattr(voice_cfg, attr, None)
                        if val is not None:
                            cfg[attr] = val

        def _resolve_key(cfg_key: str, env_key: str, fallback_env: str) -> str | None:
            """Return value from config, then env_key, then fallback_env."""
            # Also handle api_key_env indirection: if cfg has stt_api_key_env, read that env.
            key_env_field = cfg_key.replace("_api_key", "_api_key_env")
            key_env_name = cfg.get(key_env_field)
            if key_env_name:
                val = os.environ.get(key_env_name)
                if val:
                    return val
            if cfg.get(cfg_key):
                return str(cfg[cfg_key])
            env_val = os.environ.get(env_key)
            if env_val:
                return env_val
            return os.environ.get(fallback_env)

        stt_api_key = _resolve_key("stt_api_key", "HACHI_STT_API_KEY", "OPENAI_API_KEY")
        stt_base_url = (
            cfg.get("stt_base_url")
            or os.environ.get("HACHI_STT_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
        stt_model = cfg.get("stt_model") or os.environ.get("HACHI_STT_MODEL")

        tts_api_key = _resolve_key("tts_api_key", "HACHI_TTS_API_KEY", "OPENAI_API_KEY")
        tts_base_url = (
            cfg.get("tts_base_url")
            or os.environ.get("HACHI_TTS_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
        tts_model = cfg.get("tts_model") or os.environ.get("HACHI_TTS_MODEL")
        tts_voice = cfg.get("tts_voice") or os.environ.get("HACHI_TTS_VOICE")

        stt = SpeechToText(
            api_key=stt_api_key,
            base_url=stt_base_url,
            model=stt_model,
        )
        tts = TextToSpeech(
            api_key=tts_api_key,
            base_url=tts_base_url,
            model=tts_model,
            voice=tts_voice,
        )

        return cls(stt=stt, tts=tts, retry_policy=retry_policy)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _media_type_to_ext(media_type: str) -> str:
    """Map a MIME media type to a file extension for temp files."""
    _map = {
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/webm": ".webm",
        "audio/flac": ".flac",
        "audio/ogg": ".ogg",
    }
    return _map.get(media_type.lower(), ".wav")
