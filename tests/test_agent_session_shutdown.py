"""AgentSession.shutdown() must shut down the per-session tool thread pool.

Regression coverage: ``AgentSession.shutdown()`` closes the session store,
LLM clients, browser controller, memory vault, integrations, and MCP manager
— but never called ``self.loop.shutdown()``, which is what actually shuts
down ``ExecutionLoop._tool_pool`` (a ``ThreadPoolExecutor`` with 4 worker
threads created per session). Every session ended this way leaked its tool
thread pool for the lifetime of the process.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hachi.gateway.orchestrator import AgentSession


def _minimal_agent_session() -> AgentSession:
    """Build a minimal AgentSession through the REAL constructor, touching only
    what shutdown() reads.

    T-45 (strengthened, not weakened): this fixture used to build the instance
    via ``AgentSession.__new__`` + manual attribute wiring (mirroring the
    ``_agent_with()`` pattern in tests/test_mcp_lifecycle.py), which bypasses
    ``__init__`` entirely — it stayed green no matter what a constructor
    change did, because it never actually called the constructor. It now goes
    through the real ``AgentSession(...)`` call with only collaborators
    mocked, so the ten previously-stashed-by-boot_agent attributes (schedule
    runner, sub-agent client, browser controller, etc.) come from the
    constructor's own defaults, and a dropped/renamed constructor argument
    would fail this fixture with a real TypeError instead of staying silent.
    """
    loop = MagicMock()
    loop.llm_client = None
    memory = MagicMock()
    memory.history = []
    memory.vault = None
    mcp_manager = MagicMock()
    mcp_manager.server_count = 0

    return AgentSession(
        loop=loop,
        skill_engine=MagicMock(),
        memory=memory,
        integrations=MagicMock(),
        mcp_manager=mcp_manager,
        permission_gate=MagicMock(),
        model_name="test-model",
    )


class TestAgentSessionShutdown:
    def test_shutdown_shuts_down_tool_pool(self) -> None:
        agent = _minimal_agent_session()

        agent.shutdown()

        agent.loop.shutdown.assert_called_once()
