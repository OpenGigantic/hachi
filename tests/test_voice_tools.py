"""Offline tests for speech-to-text: the SpeechToText backend (faked transcriber,
no network) and the transcribe_audio tool (gating + graceful degradation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.voice import SpeechToText, VoiceError
from tokoboros.tools.voice_tools import _make_transcribe_audio, register_voice_tools


class _Result:
    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason


class _Gate:
    def __init__(self, tier: str = "guided", allowed: bool = True) -> None:
        self.tier = tier
        self._allowed = allowed

    def check_path(self, path: str, mode: str) -> _Result:
        return _Result(self._allowed, "" if self._allowed else "path not allowed")


def _wav(tmp_path: Path, name: str = "note.wav") -> Path:
    p = tmp_path / name
    p.write_bytes(b"RIFF....WAVEfmt ")  # bytes don't matter — transcriber is faked
    return p


def _ok_transcriber(text: str = "hello world"):
    def _t(url, file_bytes, filename, form_data, headers):
        assert url.endswith("/audio/transcriptions")
        assert headers.get("Authorization", "").startswith("Bearer ")
        return 200, json.dumps({"text": text})

    return _t


# --------------------------------------------------------------------------- #
# SpeechToText backend
# --------------------------------------------------------------------------- #
def test_stt_not_configured_without_key() -> None:
    stt = SpeechToText()  # no api_key
    assert stt.is_configured() is False


def test_stt_transcribe_happy_path(tmp_path: Path) -> None:
    audio = _wav(tmp_path)
    stt = SpeechToText(api_key="sk-test", transcriber=_ok_transcriber("the meeting is at noon"))
    assert stt.transcribe(audio) == "the meeting is at noon"


def test_stt_transcribe_raises_when_unconfigured(tmp_path: Path) -> None:
    with pytest.raises(VoiceError):
        SpeechToText().transcribe(_wav(tmp_path))


def test_stt_missing_file(tmp_path: Path) -> None:
    stt = SpeechToText(api_key="sk-test", transcriber=_ok_transcriber())
    with pytest.raises(VoiceError, match="not found"):
        stt.transcribe(tmp_path / "nope.wav")


def test_stt_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "note.txt"
    bad.write_bytes(b"not audio")
    stt = SpeechToText(api_key="sk-test", transcriber=_ok_transcriber())
    with pytest.raises(VoiceError, match="unsupported audio format"):
        stt.transcribe(bad)


def test_stt_ssrf_blocks_private_endpoint(tmp_path: Path) -> None:
    audio = _wav(tmp_path)
    stt = SpeechToText(
        api_key="sk-test",
        base_url="http://127.0.0.1:1234/v1",
        transcriber=_ok_transcriber(),
        allow_private=False,
    )
    with pytest.raises(VoiceError, match="blocked transcription endpoint"):
        stt.transcribe(audio)


def test_stt_http_error_status(tmp_path: Path) -> None:
    def _t(url, file_bytes, filename, form_data, headers):
        return 401, "unauthorized"

    stt = SpeechToText(api_key="sk-test", transcriber=_t)
    with pytest.raises(VoiceError, match="HTTP 401"):
        stt.transcribe(_wav(tmp_path))


def test_stt_missing_text_field(tmp_path: Path) -> None:
    def _t(url, file_bytes, filename, form_data, headers):
        return 200, json.dumps({"not_text": "x"})

    stt = SpeechToText(api_key="sk-test", transcriber=_t)
    with pytest.raises(VoiceError, match="text"):
        stt.transcribe(_wav(tmp_path))


def test_stt_from_env_reads_keys(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TOKOBOROS_STT_API_KEY", "sk-env")
    monkeypatch.setenv("TOKOBOROS_STT_MODEL", "whisper-large")
    stt = SpeechToText.from_env(transcriber=_ok_transcriber())
    assert stt.is_configured() is True
    assert stt._model == "whisper-large"


# --------------------------------------------------------------------------- #
# transcribe_audio tool
# --------------------------------------------------------------------------- #
def test_register_voice_tools_registers_in_voice_group() -> None:
    registry = ToolRegistry()
    register_voice_tools(registry, _Gate("guided"))
    names = {schema["name"] for schema in registry.get_schemas()}
    assert "transcribe_audio" in names
    assert "voice" in registry.available_groups()


def test_tool_blocked_in_sandbox(tmp_path: Path) -> None:
    tool = _make_transcribe_audio(_Gate("sandbox"), SpeechToText(api_key="sk-test"))
    out = tool(str(_wav(tmp_path)))
    assert "disabled in 'sandbox'" in out


def test_tool_path_denied(tmp_path: Path) -> None:
    stt = SpeechToText(api_key="sk-test", transcriber=_ok_transcriber())
    tool = _make_transcribe_audio(_Gate("guided", allowed=False), stt)
    out = tool(str(_wav(tmp_path)))
    assert "Permission Denied" in out


def test_tool_not_configured_is_friendly(tmp_path: Path) -> None:
    tool = _make_transcribe_audio(_Gate("guided"), SpeechToText())  # no key
    out = tool(str(_wav(tmp_path)))
    assert "not configured" in out
    assert "OPENAI_API_KEY" in out


def test_tool_happy_path(tmp_path: Path) -> None:
    stt = SpeechToText(api_key="sk-test", transcriber=_ok_transcriber("call me back"))
    tool = _make_transcribe_audio(_Gate("guided"), stt)
    out = tool(str(_wav(tmp_path, "vm.wav")))
    assert "Transcript of vm.wav" in out
    assert "call me back" in out


def test_tool_failure_is_friendly(tmp_path: Path) -> None:
    def _t(url, file_bytes, filename, form_data, headers):
        return 500, "server error"

    stt = SpeechToText(api_key="sk-test", transcriber=_t)
    tool = _make_transcribe_audio(_Gate("guided"), stt)
    out = tool(str(_wav(tmp_path)))
    assert "Transcription failed" in out
