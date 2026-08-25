"""Staged builders for `hachi.gateway.orchestrator.boot_agent` (T-44).

`boot_agent` is the composition root that assembles an `AgentSession`: permissions,
config, the skill engine, integrations, MCP, the LLM provider, routing, memory,
tools, the system prompt, and the execution loop. Before T-44 all of that lived in
one ~1,245-line function with three parameters, where every other value was
closure state read and written hundreds of lines apart.

This package holds that logic instead, split into named stages that each take
EXPLICIT typed inputs and return a frozen result (see `boot.stages`). The stage
order mirrors the REAL dependency graph derived empirically for T-44 -- which is
NOT the order the old numbered comments in `orchestrator.py` implied. In
particular, prompt assembly (`boot.assembly.build_prompt`) is downstream of tool
registration (`boot.tools.register_tools`), not the other way around.

Import direction (deliberate, and load-bearing for avoiding a circular import):
`hachi.gateway.orchestrator` imports FROM this package (inside `boot_agent`'s own
function body, so the import is deferred until the module has finished loading).
Modules in this package never import FROM `hachi.gateway.orchestrator` -- the
handful of orchestrator-local helpers a stage needs (`select_action_approver`,
`_configure_mcp_servers`, etc.) are passed in as explicit callable parameters by
`boot_agent`, which already has unqualified access to them. This mirrors the
existing one-way relationship `orchestrator.py` already has with its other
gateway-leaf siblings, `session_read_models.py` (T-46) and `tool_registration.py`
(T-39): both are imported BY `orchestrator.py` and import nothing back from it.

Not every line of the old `boot_agent` moved here -- see the T-44 report for the
two blocks left inline (the event-plumbing closures and the "create session
onward" tail) and exactly which regression tests outside this task's scope forced
that call.
"""

from __future__ import annotations
