"""Regression tests for T-09 / BB-96.

``_emit_file_extracted`` (``hachi.core.runtime.job_handlers``) tried to reach the
process EventBus via ``from hachi.gateway.orchestrator import _shared`` — a name
that only ever exists as a local variable inside ``boot_agent``, never at module
scope. The ``ImportError`` was swallowed, so the function fell through to
constructing a brand-new subscriber-less ``EventBus()`` and published into that.
``file.extracted`` reached nobody, ever, while the call site looked like success.

These tests assert *real delivery* to a subscriber registered on the actual
shared ``ProcessSharedServices`` bus — not that ``publish()`` was merely called.
"""

from __future__ import annotations

import logging

import pytest

from hachi.core.runtime.job_handlers import _emit_file_extracted
from hachi.core.runtime_events import FILE_EXTRACTED, RuntimeEvent
from hachi.gateway.shared_services import ProcessSharedServices


@pytest.fixture
def shared_with_bus(tmp_path):
    """A real ProcessSharedServices carrying a real EventBus, like boot_agent builds.

    BB-3: use the in_memory backend so ``_init`` never opens a ChromaDB
    PersistentClient — this test only needs the shared EventBus.
    """
    from hachi.core.event_bus import EventBus

    ProcessSharedServices.clear()
    shared = ProcessSharedServices.get_or_create(
        {"memory": {"backend": "in_memory"}},
        str(tmp_path),
        force_reinit=True,
    )
    shared.event_bus = EventBus()
    yield shared
    ProcessSharedServices.clear()


def test_file_extracted_reaches_the_shared_bus(shared_with_bus) -> None:
    """A subscriber on the real shared bus must actually receive the event."""
    received: list[RuntimeEvent] = []
    shared_with_bus.event_bus.subscribe(FILE_EXTRACTED, received.append)

    _emit_file_extracted("f-1", "report.pdf", "application/pdf", "sidecar-1")

    assert received, (
        "file.extracted never reached the shared EventBus — the emitter published "
        "into a freshly-constructed subscriber-less bus"
    )
    assert received[0].name == FILE_EXTRACTED
    assert received[0].payload["file_id"] == "f-1"
    assert received[0].payload["sidecar_id"] == "sidecar-1"


def test_file_extracted_warns_when_no_shared_bus(caplog) -> None:
    """No shared bus (BB-6: no session booted) must be a loud drop, not silence."""
    ProcessSharedServices.clear()  # BB-6: no singleton at all
    with caplog.at_level(logging.WARNING):
        _emit_file_extracted("f-2", "x.txt", "text/plain", "sidecar-2")
    assert any("file.extracted" in r.message for r in caplog.records), (
        "a dropped event must be loud — this is the 'make failure visible' half of the fix"
    )
