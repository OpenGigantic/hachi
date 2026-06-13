"""P4-02: Shared-services gateway — process singletons.

Tests:
  1. ProcessSharedServices.get_or_create() returns the same instance on repeat calls.
  2. ProcessSharedServices.clear() destroys the singleton.
  3. After clear(), get_or_create() returns a fresh instance.
  4. Singleton has expected fields: skill_engine, mcp_manager, vault, graph_index, memory_writer.
  5. skill_engine field is non-None when init succeeds.
  6. uptime_seconds() increases monotonically.
  7. force_reinit=True creates a new instance even without clear().
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helper: build a fake ProcessSharedServices without touching disk or subprocesses.
# We patch _init() rather than its 5 internal imports so tests stay simple.
# ---------------------------------------------------------------------------


def _make_fake_instance():
    from tokoboros.gateway.shared_services import ProcessSharedServices

    return ProcessSharedServices(
        skill_engine=MagicMock(),
        mcp_manager=MagicMock(),
        vault=None,
        graph_index=None,
        memory_writer=None,
        brain_dir="/tmp/fake_brain",
    )


def _patched_shared(tmp_path_str: str = "/tmp/fake_brain"):
    """Call get_or_create() with _init() mocked so no real services are started."""
    from tokoboros.gateway.shared_services import ProcessSharedServices

    ProcessSharedServices.clear()
    with patch.object(ProcessSharedServices, "_init", return_value=_make_fake_instance()):
        instance = ProcessSharedServices.get_or_create(config={}, brain_dir=tmp_path_str)
    return instance


# ---------------------------------------------------------------------------
# 1. Same instance on repeat calls
# ---------------------------------------------------------------------------


def test_get_or_create_returns_singleton() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    try:
        inst1 = _patched_shared()
        # Second call with no patching — _init should NOT be called again
        inst2 = ProcessSharedServices.get_or_create(config={}, brain_dir="/tmp/fake_brain")
        assert inst1 is inst2, "Second call must return the same instance"
    finally:
        ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# 2. clear() destroys the singleton
# ---------------------------------------------------------------------------


def test_clear_destroys_singleton() -> None:
    import tokoboros.gateway.shared_services as _mod
    from tokoboros.gateway.shared_services import ProcessSharedServices

    _patched_shared()
    ProcessSharedServices.clear()
    assert _mod._INSTANCE is None


# ---------------------------------------------------------------------------
# 3. After clear(), get_or_create() returns a fresh instance
# ---------------------------------------------------------------------------


def test_after_clear_new_instance_is_created() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    inst1 = _patched_shared()
    ProcessSharedServices.clear()
    inst2 = _patched_shared()
    assert inst1 is not inst2


# ---------------------------------------------------------------------------
# 4. Singleton has expected fields
# ---------------------------------------------------------------------------


def test_singleton_has_expected_fields() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    try:
        inst = _patched_shared()
        assert hasattr(inst, "skill_engine")
        assert hasattr(inst, "mcp_manager")
        assert hasattr(inst, "vault")
        assert hasattr(inst, "graph_index")
        assert hasattr(inst, "memory_writer")
        assert hasattr(inst, "brain_dir")
        assert hasattr(inst, "initialized_at")
    finally:
        ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# 5. skill_engine is non-None when init succeeds
# ---------------------------------------------------------------------------


def test_skill_engine_is_present() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    try:
        inst = _patched_shared()
        assert inst.skill_engine is not None
    finally:
        ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# 6. uptime_seconds() increases
# ---------------------------------------------------------------------------


def test_uptime_seconds_increases() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    try:
        inst = _patched_shared()
        t1 = inst.uptime_seconds()
        time.sleep(0.01)
        t2 = inst.uptime_seconds()
        assert t2 > t1
    finally:
        ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# 7. force_reinit=True creates a new instance
# ---------------------------------------------------------------------------


def test_force_reinit_creates_new_instance() -> None:
    from tokoboros.gateway.shared_services import ProcessSharedServices

    try:
        inst1 = _patched_shared()

        with patch.object(ProcessSharedServices, "_init", return_value=_make_fake_instance()):
            inst2 = ProcessSharedServices.get_or_create(
                config={}, brain_dir="/tmp/fake_brain", force_reinit=True
            )

        assert inst1 is not inst2, "force_reinit=True must produce a new instance"
    finally:
        ProcessSharedServices.clear()
