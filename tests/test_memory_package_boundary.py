"""Regression test for T-48 — the memory subsystem lives behind a package boundary.

Locks the structural invariant that ``hachi.core.memory`` is a real Python
*package* (not a flat module) and that the three public entry points
(``MemoryManager``, ``extract_facts``, ``build_episode_summary``) survived the
move unchanged, while none of the old flat ``hachi.core.memory_*`` /
``hachi.core.graph_recall`` / ``hachi.core.conversation_memory`` module paths
remain importable.

Uses ``importlib.util.find_spec`` rather than a bare ``import`` so a missing
module reports a clean assertion instead of an ``ImportError`` traceback, and
so it never has the side effect of importing chromadb-backed modules. Do not
rewrite this to ``patch.dict(sys.modules)`` — see BB-3 in docs/bug-bank.md:
chromadb's PyO3 native extension must load exactly once per process, and
conftest.py's warm-up depends on nothing snapshotting/restoring sys.modules
around it.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

_FLAT_MODULES = [
    "hachi.core.memory_vault",
    "hachi.core.memory_provider",
    "hachi.core.memory_writer",
    "hachi.core.memory_index",
    "hachi.core.memory_graph",
    "hachi.core.memory_gardener",
    "hachi.core.memory_guard",
    "hachi.core.memory_importer",
    "hachi.core.memory_exporter",
    "hachi.core.graph_recall",
    "hachi.core.conversation_memory",
]


def test_memory_package_exists_and_exports_public_surface() -> None:
    """core/memory/ is a package and still exports the three public names.

    Locks T-48: the move must not change the public surface that
    gateway/orchestrator.py and core/runtime/job_handlers.py import.
    """
    mod = importlib.import_module("hachi.core.memory")
    assert hasattr(mod, "__path__"), "hachi.core.memory is a module, not a package"
    for name in ("MemoryManager", "extract_facts", "build_episode_summary"):
        assert hasattr(mod, name), f"public name lost in the move: {name}"


@pytest.mark.parametrize("dotted", _FLAT_MODULES)
def test_no_flat_memory_modules_remain(dotted: str) -> None:
    """No memory module may be importable from the flat core/ namespace."""
    assert importlib.util.find_spec(dotted) is None, f"flat module still importable: {dotted}"
