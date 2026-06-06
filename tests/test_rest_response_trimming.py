"""REST tool output trimming: .og field projection + a generic size cap.

Keeps tool results (and token cost) tight — e.g. fetch_wiki_summary returns only
title/extract instead of the full Wikipedia REST JSON.
"""

from __future__ import annotations

import json

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.rest_adapter import (
    _MAX_REST_RESPONSE_CHARS,
    UniversalRestAdapter,
    _truncate_rest_response,
)


def _register(monkeypatch, payload: dict, api_def: dict) -> ToolRegistry:
    registry = ToolRegistry()

    class _FakeResponse:
        status_code = 200
        text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return payload

    class _FakeClient:
        def __init__(self, **_kwargs) -> None:
            return

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return

        def request(self, **_kwargs):
            return _FakeResponse()

    monkeypatch.setenv("TOKOBOROS_REST_ALLOW_PRIVATE", "true")
    monkeypatch.setattr("tokoboros.core.rest_adapter.httpx.Client", _FakeClient)
    adapter = UniversalRestAdapter(registry, allowed_hosts=[])
    base = {"method": "GET", "endpoint": "http://127.0.0.1:9000/x", "schema": "{}", "headers": "{}"}
    base.update(api_def)
    adapter._register_single_api(tool_name="api", api_def=base, node_name="n")
    return registry


def test_field_projection_returns_only_declared_fields(monkeypatch) -> None:
    payload = {
        "title": "Sunflower",
        "extract": "A tall plant.",
        "extract_html": "<p>huge html blob</p>",
        "thumbnail": {"source": "x"},
        "namespace": {"id": 0},
    }
    registry = _register(monkeypatch, payload, {"fields": "title, extract"})
    result = json.loads(registry._tools["api"]())
    assert result == {"title": "Sunflower", "extract": "A tall plant."}
    assert "extract_html" not in result and "thumbnail" not in result


def test_projection_falls_back_to_full_when_no_field_matches(monkeypatch) -> None:
    payload = {"a": 1, "b": 2}
    registry = _register(monkeypatch, payload, {"fields": "nonexistent"})
    assert json.loads(registry._tools["api"]()) == payload  # not an empty dict


def test_no_fields_directive_returns_full_response(monkeypatch) -> None:
    payload = {"a": 1, "b": 2}
    registry = _register(monkeypatch, payload, {})
    assert json.loads(registry._tools["api"]()) == payload


def test_oversized_response_is_truncated_with_guidance(monkeypatch) -> None:
    payload = {"data": "x" * (_MAX_REST_RESPONSE_CHARS + 5_000)}
    registry = _register(monkeypatch, payload, {})
    out = registry._tools["api"]()
    assert len(out) <= _MAX_REST_RESPONSE_CHARS + 200  # cap plus a short notice
    assert "truncated" in out and "fields=" in out


def test_truncate_helper_passthrough_when_within_limit() -> None:
    assert _truncate_rest_response("short text") == "short text"
