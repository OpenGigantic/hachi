"""T-43 round 1 -- characterization tests for ``GatewayControlPlane``.

CLAUDE.md Rule 2 (the regression law) applied to a refactor: there is no bug
here, so the instrument is a characterization test that pins the behaviour of
``GatewayControlPlane``'s stateful methods *before* the constructor is
touched, so the port collapse is provably behaviour-neutral.

The first three tests assert real, observed behaviour that must hold both
BEFORE and AFTER the T-43 round 1 change (introducing ``hachi.gateway.ports``
and wiring it into the constructor as an ADDITIVE alternative to the 74
existing handler kwargs -- none of which are removed in round 1).

``test_runtime_satisfies_every_port`` is the one test in this file that is
expected to fail before the change: it must fail with
``ModuleNotFoundError: No module named 'hachi.gateway.ports'`` until
``ports.py`` exists.

``test_control_plane_helper_matches_legacy_kwargs_form`` proves the new
``control_plane()`` test helper (tests/conftest.py) -- which Round 2 will use
to convert the 99 existing ``GatewayControlPlane(...)`` call sites -- is
behaviourally equivalent to building the control plane with legacy handler
kwargs directly.
"""

from __future__ import annotations

import pytest

from hachi.gateway.control_plane import GatewayControlPlane, GatewayProtocolError


def test_session_limit_is_enforced_at_the_control_plane() -> None:
    """``create_session`` must reject beyond ``max_active_sessions`` (control_plane.py:335-337).

    Characterization: this behaviour lives ONLY in ``GatewayControlPlane``. If
    a future port collapse drops it, no other layer catches it.
    """
    plane = GatewayControlPlane(max_active_sessions=2)

    plane.create_session(client_id="a", surface="test")
    plane.create_session(client_id="b", surface="test")

    with pytest.raises(GatewayProtocolError, match="Maximum active sessions reached"):
        plane.create_session(client_id="c", surface="test")


def test_prompt_length_limit_is_enforced() -> None:
    """``send_message`` must reject prompts over ``max_prompt_chars`` (control_plane.py:416-418)."""
    plane = GatewayControlPlane(
        on_chat=lambda _session_id, prompt: prompt,
        max_prompt_chars=8,
    )
    created = plane.create_session(client_id="a", surface="test")
    session_id = created["session_id"]

    # Exactly at the limit succeeds.
    at_limit = plane.send_message(session_id=session_id, prompt="12345678")
    assert at_limit["response"] == "12345678"

    with pytest.raises(GatewayProtocolError, match="prompt exceeds max length"):
        plane.send_message(session_id=session_id, prompt="123456789")


def test_failed_connect_releases_the_session_slot() -> None:
    """A raising ``on_connect`` must NOT consume a session slot (control_plane.py:347-355).

    Guards the subtlest behaviour in the class -- trivially lost in a
    rewrite: the session slot is registered BEFORE ``on_connect`` runs (so a
    slow boot still counts against ``max_active_sessions`` while it is in
    flight), but released again if ``on_connect`` raises, so repeated failed
    connects don't permanently exhaust capacity.
    """
    calls = {"n": 0}

    def _flaky(_session_id: str, _request: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    plane = GatewayControlPlane(max_active_sessions=1, on_connect=_flaky)

    with pytest.raises(RuntimeError, match="boom"):
        plane.create_session(client_id="a", surface="test")

    # The slot from the failed attempt must have been released, else this
    # would raise GatewayProtocolError("Maximum active sessions reached (1).").
    second = plane.create_session(client_id="b", surface="test")
    assert second["accepted"] is True


def test_runtime_satisfies_every_port() -> None:
    """``_InProcessGatewayRuntime`` must structurally satisfy all 18 ports.

    FAILS before T-43 round 1 with ``ModuleNotFoundError`` (there is no
    ``hachi.gateway.ports`` module yet). Enumerates each Protocol's declared
    members directly off its class ``__dict__`` rather than via
    ``Protocol.__protocol_attrs__`` -- a CPython implementation detail not
    available on every interpreter version (T-43 known trap) -- so the check
    is stable across interpreters.
    """
    import hachi.gateway.ports as ports
    from hachi.gateway.in_process_runtime import _InProcessGatewayRuntime

    runtime = _InProcessGatewayRuntime()
    port_names = [name for name in dir(ports) if name.endswith("Port")]
    assert port_names, "no *Port protocols found in hachi.gateway.ports"

    missing: dict[str, list[str]] = {}
    for name in port_names:
        proto = getattr(ports, name)
        members = [member_name for member_name in vars(proto) if not member_name.startswith("_")]
        assert members, f"{name} declares no members"
        absent = [member for member in members if not hasattr(runtime, member)]
        if absent:
            missing[name] = absent

    assert missing == {}, f"runtime does not satisfy: {missing}"


def test_control_plane_helper_matches_legacy_kwargs_form() -> None:
    """The ``control_plane()`` test helper (tests/conftest.py) must be
    behaviourally equivalent to building ``GatewayControlPlane`` with legacy
    handler kwargs directly.

    Exercises one ``Live*Port`` handler (``on_chat`` -> ``sessions``), one
    support-port handler (``get_config`` -> ``config``), and a tunable
    pass-through (``max_prompt_chars``) -- enough to prove the helper's
    kwarg-to-port grouping and passthrough logic both work, without
    re-asserting all 74 handlers (already covered by
    ``test_runtime_satisfies_every_port`` and the primary contract suite in
    ``test_gateway_control_plane.py``).
    """
    from conftest import control_plane

    def _on_chat(_session_id: str, prompt: str) -> str:
        return f"echo:{prompt}"

    def _get_config() -> dict[str, object]:
        return {"provider": {"kind": "anthropic"}}

    legacy = GatewayControlPlane(on_chat=_on_chat, get_config=_get_config, max_prompt_chars=50)
    via_helper = control_plane(on_chat=_on_chat, get_config=_get_config, max_prompt_chars=50)

    for plane in (legacy, via_helper):
        created = plane.create_session(client_id="a", surface="test")
        response = plane.send_message(session_id=created["session_id"], prompt="hi")
        assert response["response"] == "echo:hi"
        assert plane.get_config() == {"provider": {"kind": "anthropic"}}

    # And the unknown-kwarg guard rail: a typo'd kwarg must fail loudly, not
    # silently construct a control plane missing a handler.
    with pytest.raises(TypeError, match="unknown handler kwarg"):
        control_plane(on_chatt=_on_chat)
