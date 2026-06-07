"""Voice backends (speech-to-text + text-to-speech) with graceful degradation.

:class:`SpeechToText` transcribes a local audio file via an OpenAI-compatible
``/audio/transcriptions`` endpoint; :class:`TextToSpeech` synthesizes speech to a
file via ``/audio/speech`` (OpenAI, Groq, or any compatible server). Both follow
the same test-complete pattern as the browser controller and the Slack/Discord
senders:

- credentials resolve **lazily from the environment** at call time (never at
  import/boot), so this constructs cleanly even with nothing configured;
- the actual HTTP POST goes through an injectable ``transcriber`` so tests run
  fully offline;
- the endpoint URL is **SSRF-validated** via ``net_guard`` before any request;
- it raises :class:`VoiceError` (a readable message) rather than leaking a raw
  HTTP/parse error — callers check :meth:`is_configured` first and surface
  failures as friendly tool results.

:func:`probe_audio_info` is a **keyless** helper that uses only the Python
standard library to inspect a local audio file.  Full metadata (duration,
sample rate, channels) is available for WAV files.  For other formats only
the file size and extension are reported — "format not probeable" is the
friendly response rather than an error.
"""

from __future__ import annotations

import json
import os
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokoboros.core.net_guard import validate_outbound_url

# (url, file_bytes, filename, form_data, headers) -> (status_code, response_text)
Transcriber = Callable[[str, bytes, str, dict[str, str], dict[str, str]], tuple[int, str]]

# (url, json_payload, headers) -> (status_code, audio_bytes)
Synthesizer = Callable[[str, dict[str, object], dict[str, str]], tuple[int, bytes]]

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "whisper-1"

_DEFAULT_TTS_MODEL = "tts-1"
_DEFAULT_TTS_VOICE = "alloy"
# Output formats an OpenAI-compatible /audio/speech endpoint produces, keyed by
# the file extension we map them from.
_TTS_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})

# Extensions the common Whisper endpoints accept; checked up front for a friendly
# error instead of an opaque API rejection.
_SUPPORTED_EXTENSIONS = frozenset(
    {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac", ".ogg"}
)

# Hard ceiling on audio bytes (the OpenAI endpoint caps at ~25 MB); fail fast.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def probe_audio_info(path: str | Path) -> dict[str, Any]:
    """Probe a local audio file for metadata without any API key or network call.

    Uses only the Python standard library.  Full metadata (duration in seconds,
    sample rate in Hz, channel count) is available for PCM-WAV files.  For all
    other formats only the file size and format hint (derived from the extension)
    are returned — the ``probeable`` flag tells the caller whether the richer
    fields are present.

    Args:
        path: Absolute or relative path to the audio file.

    Returns:
        A dict with at least:
          - ``file`` — resolved absolute path (str)
          - ``format`` — extension without leading dot, lower-case (e.g. ``"wav"``)
          - ``size_bytes`` — file size in bytes
          - ``probeable`` — ``True`` when duration/sample_rate/channels are present

        When ``probeable`` is ``True`` the dict additionally contains:
          - ``duration_seconds`` — float, e.g. ``3.71``
          - ``sample_rate`` — int, e.g. ``44100``
          - ``channels`` — int, e.g. ``1`` (mono) or ``2`` (stereo)

    Raises:
        VoiceError: If the file does not exist, is not a regular file, or the
            WAV header is corrupt.
    """
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        raise VoiceError(f"audio file not found: {p}")

    fmt = p.suffix.lstrip(".").lower() or "unknown"
    size = p.stat().st_size

    info: dict[str, Any] = {
        "file": str(p),
        "format": fmt,
        "size_bytes": size,
        "probeable": False,
    }

    if fmt == "wav":
        try:
            with wave.open(str(p), "rb") as wf:
                n_channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                duration = n_frames / sample_rate if sample_rate > 0 else 0.0
            info["probeable"] = True
            info["duration_seconds"] = round(duration, 3)
            info["sample_rate"] = sample_rate
            info["channels"] = n_channels
        except wave.Error as exc:
            raise VoiceError(f"could not read WAV header: {exc}") from exc

    return info


class VoiceError(RuntimeError):
    """Raised when a voice backend is misconfigured or rejects the request."""


def _default_transcriber(
    url: str, file_bytes: bytes, filename: str, form_data: dict[str, str], headers: dict[str, str]
) -> tuple[int, str]:
    import httpx

    files = {"file": (filename, file_bytes)}
    with httpx.Client(timeout=120.0, follow_redirects=False) as client:
        resp = client.post(url, files=files, data=form_data, headers=headers)
        return resp.status_code, resp.text


class SpeechToText:
    """Transcribe audio via an OpenAI-compatible transcription endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transcriber: Transcriber | None = None,
        allow_private: bool = False,
    ) -> None:
        self._api_key = api_key or None
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = model or _DEFAULT_MODEL
        self._transcriber = transcriber or _default_transcriber
        self._allow_private = allow_private

    @classmethod
    def from_env(cls, transcriber: Transcriber | None = None) -> SpeechToText:
        """Build from environment: TOKOBOROS_STT_* overrides, else OpenAI defaults."""
        return cls(
            api_key=os.environ.get("TOKOBOROS_STT_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("TOKOBOROS_STT_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
            model=os.environ.get("TOKOBOROS_STT_MODEL"),
            transcriber=transcriber,
            allow_private=os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "").strip().lower()
            in {"1", "true", "yes"},
        )

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe a local audio file to text. Raises VoiceError on any failure."""
        if not self._api_key:
            raise VoiceError(
                "speech-to-text is not configured "
                "(set OPENAI_API_KEY or TOKOBOROS_STT_API_KEY)"
            )
        path = Path(audio_path)
        if not path.exists() or not path.is_file():
            raise VoiceError(f"audio file not found: {path}")
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            raise VoiceError(
                f"unsupported audio format '{path.suffix or '(none)'}'. "
                f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
            )

        url = f"{self._base_url}/audio/transcriptions"
        ok, reason = validate_outbound_url(url, allow_private=self._allow_private)
        if not ok:
            raise VoiceError(f"blocked transcription endpoint: {reason}")

        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise VoiceError(f"could not read audio: {exc}") from exc
        if len(raw) > _MAX_AUDIO_BYTES:
            raise VoiceError(
                f"audio is {len(raw) // (1024 * 1024)} MB, exceeding the "
                f"{_MAX_AUDIO_BYTES // (1024 * 1024)} MB limit"
            )

        headers = {"Authorization": f"Bearer {self._api_key}"}
        status, body = self._transcriber(url, raw, path.name, {"model": self._model}, headers)
        if status != 200:
            raise VoiceError(f"transcription API returned HTTP {status}: {body[:200]}")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise VoiceError(f"could not parse transcription response: {exc}") from exc
        text = parsed.get("text")
        if not isinstance(text, str):
            raise VoiceError("transcription response did not include a 'text' field")
        return text.strip()


def _default_synthesizer(
    url: str, payload: dict[str, object], headers: dict[str, str]
) -> tuple[int, bytes]:
    import httpx

    with httpx.Client(timeout=120.0, follow_redirects=False) as client:
        resp = client.post(url, json=payload, headers=headers)
        return resp.status_code, resp.content


class TextToSpeech:
    """Synthesize speech from text via an OpenAI-compatible /audio/speech endpoint.

    Mirrors :class:`SpeechToText`: lazy env credentials, an injectable
    ``synthesizer`` for offline tests, SSRF-validated endpoint, readable
    :class:`VoiceError` on failure. The output audio format is inferred from the
    target file's extension (defaulting to mp3).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        synthesizer: Synthesizer | None = None,
        allow_private: bool = False,
    ) -> None:
        self._api_key = api_key or None
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = model or _DEFAULT_TTS_MODEL
        self._voice = voice or _DEFAULT_TTS_VOICE
        self._synthesizer = synthesizer or _default_synthesizer
        self._allow_private = allow_private

    @classmethod
    def from_env(cls, synthesizer: Synthesizer | None = None) -> TextToSpeech:
        """Build from environment: TOKOBOROS_TTS_* overrides, else OpenAI defaults."""
        return cls(
            api_key=os.environ.get("TOKOBOROS_TTS_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("TOKOBOROS_TTS_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
            model=os.environ.get("TOKOBOROS_TTS_MODEL"),
            voice=os.environ.get("TOKOBOROS_TTS_VOICE"),
            synthesizer=synthesizer,
            allow_private=os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "").strip().lower()
            in {"1", "true", "yes"},
        )

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def synthesize(self, text: str, out_path: str | Path, voice: str | None = None) -> Path:
        """Synthesize ``text`` to an audio file at ``out_path``. Raises VoiceError on failure."""
        if not self._api_key:
            raise VoiceError(
                "text-to-speech is not configured (set OPENAI_API_KEY or TOKOBOROS_TTS_API_KEY)"
            )
        if not text.strip():
            raise VoiceError("nothing to speak: text is empty")

        path = Path(out_path)
        fmt = path.suffix.lstrip(".").lower() or "mp3"
        if fmt not in _TTS_FORMATS:
            raise VoiceError(
                f"unsupported audio format '{path.suffix or '(none)'}'. "
                f"Supported: {', '.join(sorted(_TTS_FORMATS))}"
            )

        url = f"{self._base_url}/audio/speech"
        ok, reason = validate_outbound_url(url, allow_private=self._allow_private)
        if not ok:
            raise VoiceError(f"blocked speech endpoint: {reason}")

        payload: dict[str, object] = {
            "model": self._model,
            "input": text,
            "voice": voice or self._voice,
            "response_format": fmt,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        status, audio = self._synthesizer(url, payload, headers)
        if status != 200:
            detail = audio[:200].decode("utf-8", "replace") if audio else ""
            raise VoiceError(f"speech API returned HTTP {status}: {detail}")
        if not audio:
            raise VoiceError("speech API returned no audio")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
        except OSError as exc:
            raise VoiceError(f"could not write audio file: {exc}") from exc
        return path
