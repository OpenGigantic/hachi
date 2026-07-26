"""hachi.core.memory — the memory subsystem.

Public surface (stable): MemoryManager, extract_facts, build_episode_summary.
Everything else is internal to this package; import it from its module.

Note: `hachi.core.user_profile` (UserProfileStore) is also a memory store —
a facade over MemoryVault's user_collection — but it lives outside this
package; see T-49 for why.
"""

from hachi.core.memory.manager import (
    MemoryManager,
    build_episode_summary,
    extract_facts,
)

__all__ = ["MemoryManager", "build_episode_summary", "extract_facts"]
