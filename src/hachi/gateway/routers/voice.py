"""``/voice/*`` -- STT/TTS provider endpoints (T-47).

None of these handlers reference ``control_plane`` -- they are pure
pass-throughs to ``hachi.gateway.voice_endpoints``, so nothing here closed
over anything besides the auth dependency.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from hachi.gateway import voice_endpoints
from hachi.gateway.deps import require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, VoiceSpeakBody, _error_response
from hachi.gateway.voice_endpoints import (
    VoiceConfigError,
)
from hachi.gateway.voice_endpoints import (
    set_voice_config as _voice_set_config,
)
from hachi.gateway.voice_endpoints import (
    transcribe as _voice_transcribe,
)
from hachi.gateway.voice_endpoints import (
    voice_config as _voice_config,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB hard ceiling for audio uploads


@router.post("/transcribe", summary="Transcribe audio via STT provider")
async def route_voice_transcribe(audio: UploadFile = File(...)) -> Any:  # noqa: B008
    """Transcribe a multipart audio upload to text via the configured STT backend."""
    if audio.filename is None:
        raise HTTPException(status_code=400, detail="Filename is required")
    # Bounded read — prevents memory exhaustion from oversized uploads.
    raw = await audio.read(_MAX_AUDIO_BYTES + 1)
    if len(raw) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds 25 MB limit")
    content_type = audio.content_type or "audio/webm"
    try:
        result = await anyio.to_thread.run_sync(
            lambda: _voice_transcribe(raw, filename=audio.filename, content_type=content_type)
        )
    except VoiceConfigError as exc:
        return _error_response(503, "voice_not_configured", str(exc))
    except Exception as exc:  # noqa: BLE001
        _log.warning("STT transcription failed: %s", exc)
        return _error_response(
            500,
            "transcription_error",
            "Transcription failed, check voice provider configuration",
        )
    return result


@router.post("/speak", summary="Synthesize text via TTS provider")
async def route_voice_speak(body: VoiceSpeakBody) -> Any:
    """Synthesize text to audio via the configured TTS backend.

    Uses :func:`~hachi.gateway.voice_endpoints.speak_stream` to pipe bytes
    through a generator so the first bytes reach the client before the full
    clip is produced (FIX P1).  Falls back to the buffered path automatically
    for providers that don't support streaming.
    """
    # BB-135: resolved via the `voice_endpoints` module object rather than a
    # name bound at import time, so it is re-read on every call. Tests that
    # monkeypatch hachi.gateway.voice_endpoints.speak_stream after this
    # router module has already been imported (e.g. because an earlier test
    # already built a gateway app) still observe the stub.
    try:
        chunk_iter, media_type = await anyio.to_thread.run_sync(
            lambda: voice_endpoints.speak_stream(
                body.text,
                voice=body.voice,
                model=body.model,
                fmt=body.format,
                provider=body.provider,
            )
        )
    except VoiceConfigError as exc:
        return _error_response(503, "voice_not_configured", str(exc))
    except Exception as exc:  # noqa: BLE001
        _log.warning("TTS synthesis failed: %s", exc)
        return _error_response(
            500,
            "synthesis_error",
            "Synthesis failed, check voice provider configuration",
        )
    return StreamingResponse(
        chunk_iter,
        media_type=media_type,
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/config", summary="Get voice configuration")
async def route_voice_config() -> Any:
    """Return the current voice provider config summary for the UI."""
    return await anyio.to_thread.run_sync(_voice_config)


@router.put("/config", summary="Update voice configuration")
async def route_voice_set_config(patch: dict[str, Any]) -> Any:
    """Persist voice config fields and return the updated summary."""
    try:
        return await anyio.to_thread.run_sync(lambda: _voice_set_config(patch))
    except Exception as exc:  # noqa: BLE001
        return _error_response(500, "config_write_error", str(exc))
