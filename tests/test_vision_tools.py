"""Offline tests for the vision-input tool (``view_image``) and the
multimodal tool-result pass-through in the execution loop.

No network and no real model: a tiny in-repo PNG exercises base64 encoding,
permission/tier gating, provider awareness, and the ToolResultBlocks flow.
"""

from __future__ import annotations

import base64
from pathlib import Path

from tokoboros.core.llm import LLMResponse, ToolCall
from tokoboros.core.loop import ExecutionLoop, ToolRegistry
from tokoboros.core.sub_agent import SubAgentMemory
from tokoboros.core.tool_results import ToolResultBlocks
from tokoboros.tools.vision_tools import (
    _make_view_image,
    register_vision_tools,
)

# A valid 1x1 transparent PNG (~68 bytes) — enough to exercise real encoding.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class _Result:
    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason


class _Gate:
    """Minimal PermissionGate stand-in."""

    def __init__(
        self,
        tier: str = "guided",
        allowed: bool = True,
        max_file_size_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.tier = tier
        self._allowed = allowed
        self.max_file_size_bytes = max_file_size_bytes

    def check_path(self, path: str, mode: str) -> _Result:
        return _Result(self._allowed, "" if self._allowed else "path not allowed")


def _write_png(tmp_path: Path, name: str = "shot.png") -> Path:
    p = tmp_path / name
    p.write_bytes(_PNG_1X1)
    return p


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_register_vision_tools_registers_view_image_in_vision_group() -> None:
    registry = ToolRegistry()
    register_vision_tools(registry, _Gate("guided"), provider_kind="anthropic")
    names = {schema["name"] for schema in registry.get_schemas()}
    assert "view_image" in names
    assert "vision" in registry.available_groups()


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #
def test_view_image_blocked_in_sandbox_tier(tmp_path: Path) -> None:
    img = _write_png(tmp_path)
    view = _make_view_image(_Gate("sandbox"), provider_supports_images=True)
    out = view(str(img))
    assert isinstance(out, str)
    assert "Permission Denied" in out and "sandbox" in out


def test_view_image_permission_denied_path(tmp_path: Path) -> None:
    img = _write_png(tmp_path)
    view = _make_view_image(_Gate("guided", allowed=False), provider_supports_images=True)
    out = view(str(img))
    assert isinstance(out, str)
    assert "Permission Denied" in out


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_view_image_missing_file(tmp_path: Path) -> None:
    view = _make_view_image(_Gate("guided"), provider_supports_images=True)
    out = view(str(tmp_path / "nope.png"))
    assert isinstance(out, str)
    assert "not found" in out


def test_view_image_unsupported_type(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    view = _make_view_image(_Gate("guided"), provider_supports_images=True)
    out = view(str(txt))
    assert isinstance(out, str)
    assert "unsupported image type" in out


def test_view_image_too_large_for_gate_cap(tmp_path: Path) -> None:
    img = _write_png(tmp_path)
    # Gate caps files at 10 bytes; the 68-byte PNG exceeds it.
    view = _make_view_image(_Gate("guided", max_file_size_bytes=10), provider_supports_images=True)
    out = view(str(img))
    assert isinstance(out, str)
    assert "exceeding" in out


# --------------------------------------------------------------------------- #
# Happy path (Anthropic) and non-Anthropic fallback
# --------------------------------------------------------------------------- #
def test_view_image_returns_image_block(tmp_path: Path) -> None:
    img = _write_png(tmp_path)
    view = _make_view_image(_Gate("guided"), provider_supports_images=True)
    out = view(str(img), prompt="read the screen")

    assert isinstance(out, ToolResultBlocks)
    assert out.summary  # human-readable, no base64
    image_blocks = [b for b in out.blocks if b.get("type") == "image"]
    text_blocks = [b for b in out.blocks if b.get("type") == "text"]
    assert len(image_blocks) == 1 and len(text_blocks) == 1

    source = image_blocks[0]["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    # The encoded payload round-trips back to the original bytes.
    assert base64.b64decode(source["data"]) == _PNG_1X1
    # The custom prompt is carried into the text block.
    assert "read the screen" in text_blocks[0]["text"]


def test_view_image_non_anthropic_returns_text_not_blocks(tmp_path: Path) -> None:
    img = _write_png(tmp_path)
    view = _make_view_image(_Gate("guided"), provider_supports_images=False)
    out = view(str(img))
    assert isinstance(out, str)
    assert "requires the Anthropic provider" in out


def test_view_image_jpeg_media_type(tmp_path: Path) -> None:
    # Extension drives media-type detection (bytes are not validated/decoded here).
    img = tmp_path / "photo.jpeg"
    img.write_bytes(_PNG_1X1)
    view = _make_view_image(_Gate("guided"), provider_supports_images=True)
    out = view(str(img))
    assert isinstance(out, ToolResultBlocks)
    assert out.blocks[0]["source"]["media_type"] == "image/jpeg"


# --------------------------------------------------------------------------- #
# Loop integration: ToolResultBlocks pass through; plain results stringify
# --------------------------------------------------------------------------- #
_EMPTY_SCHEMA = {"type": "object", "properties": {}}


def _tool(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": _EMPTY_SCHEMA}


class _ScriptedLLM:
    model = "fake-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = responses
        self.calls = 0

    def generate(self, **_kwargs) -> LLMResponse:
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp


def _run_with_tool_returning(value) -> SubAgentMemory:
    registry = ToolRegistry()
    registry.register(_tool("emit"), lambda **_kw: value)
    responses = [
        LLMResponse(content="", tool_calls=[ToolCall(id="t1", name="emit", input={})]),
        LLMResponse(content="done", tool_calls=[]),
    ]
    memory = SubAgentMemory()
    loop = ExecutionLoop(_ScriptedLLM(responses), registry, memory, base_system_prompt="x")
    loop.add_user_message("look")
    loop.run(max_iterations=3)
    return memory


def _last_tool_result_content(memory: SubAgentMemory):
    msgs = [m for m in memory.history if m.role == "user" and isinstance(m.content, list)]
    assert msgs, "expected a tool_results user message"
    blocks = [b for b in msgs[-1].content if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert blocks, "expected a tool_result block"
    return blocks[0]["content"]


def test_tool_result_blocks_flow_through_loop() -> None:
    trb = ToolResultBlocks(
        blocks=[
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "Zm9v"},
            },
            {"type": "text", "text": "an image"},
        ],
        summary="an image",
    )
    content = _last_tool_result_content(_run_with_tool_returning(trb))
    assert isinstance(content, list)
    assert any(b.get("type") == "image" for b in content)


def test_plain_tool_result_is_still_stringified() -> None:
    # A dict-returning tool must NOT pass through (would be an invalid tool_result).
    content = _last_tool_result_content(_run_with_tool_returning({"status": "ok"}))
    assert isinstance(content, str)
    assert "status" in content
