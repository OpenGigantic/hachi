"""PromptComposer — the single owner of prompt text and system-prompt assembly.

Two related, separately-testable responsibilities live here (T-77):

1. **Assembly.** ``build_stable_base()`` / ``compose()`` assemble the MAIN
   AGENT LOOP's system prompt from pre-rendered section strings (brain text,
   skills, permissions, capabilities, tool groups) into a cache-aware
   (cacheable, dynamic) split. Exactly two call sites:
   ``gateway/orchestrator.py`` and ``core/turn_context.py``.
2. **Registry.** ``register_prompt()`` / ``get_prompt()`` hold the named,
   versioned ``PromptTemplate`` catalogue for the auxiliary one-shot LLM call
   sites: memory extraction / consolidation / summarisation, session titling,
   vision analysis, and .og authoring. Every registered template is
   snapshot- and byte-pinned in ``tests/test_prompt_registry.py``.

Prompt-bearing call sites should draw their text from one of these two
mechanisms rather than concatenating string literals inline. The known,
deliberate exception is Tier B: structural markers assembled *inside* a
larger routine are not standalone prompts and are not migrated here —
``core/sub_agent.py``'s ``PromptBuilder`` (a second, .og-tag-driven composer
with a different input model), the ``ExecutionLoop._build_system_prompt``
fallback concat, and a handful of ``core/memory/manager.py`` context-header
and checkpoint-marker strings. See
``docs/audit/2026-07-19/tasks/T-77-consolidate-prompts-into-composer.md`` §9.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


def _build_time_header() -> str:
    """Generate the dynamic time block (changes every turn — NOT cached)."""
    local_dt = datetime.datetime.now().astimezone()
    current_time = local_dt.strftime("%A, %Y-%m-%d %H:%M:%S")
    tz_offset = local_dt.strftime("%z")
    tz_fmt = f"{tz_offset[:3]}:{tz_offset[3:]}" if len(tz_offset) == 5 else tz_offset
    return (
        "--- CURRENT TIME AND DATE ---\n"
        f"The current local time is: {current_time}\n"
        f"Your Local Timezone Offset is: {tz_fmt}\n"
        "Use this context to resolve relative times like 'today' or 'tomorrow', "
        "and ALWAYS append the timezone offset when creating ISO strings.\n"
    )


def build_stable_base(
    brain_text: str,
    skill_count: int = 0,
    integration_count: int = 0,
    permission_block: str = "",
    capability_block: str = "",
    tool_groups: list[str] | None = None,
) -> str:
    """Build the stable session-level system prompt (called once at boot).

    Returns a string that becomes the base for every turn's cacheable prefix.

    Args:
        brain_text: The raw brain/.og prompt text.
        skill_count: Number of .og skill files loaded.
        integration_count: Number of live service tools connected.
        permission_block: Output of runtime_gate.get_system_prompt_injection().
        capability_block: Stringified capability status snapshot.
        tool_groups: Optional domain tool group names available on demand.

    Returns:
        The fully assembled stable base prompt string.
    """
    parts = [brain_text]

    if skill_count > 0:
        parts.append(
            "\n\n--- IMPORTANT: ObjectGraph Skills Available (3-pass loading) ---\n"
            f"You have {skill_count} skill(s) loaded from .og files. Read them in three passes "
            "so you only spend tokens on what you actually need:\n"
            "1. `search_skills` (Pass 1) — ALWAYS call first to find relevant skills by keyword.\n"
            "2. `peek_skill` (Pass 2) — read the candidates' compressed summaries cheaply to pick "
            "the right one; don't load blind.\n"
            "3. `load_skill` (Pass 3) — load the chosen skill's full instructions (optionally "
            "narrowing to specific `blocks`), then follow them precisely.\n"
            "If a skill has ASSERTIONS, you MUST run each assertion command and verify it passes "
            "before continuing.\n"
        )

    if integration_count > 0:
        parts.append(
            "\n--- IMPORTANT: Live Service Integrations Active ---\n"
            f"You have {integration_count} live service tool(s) connected.\n"
            "These are REAL tools with persistent connections (e.g., email, calendar, APIs).\n"
            "Use them directly when the user's request matches their purpose.\n"
            "Do NOT write scripts or use run_terminal_command for tasks these tools handle.\n"
        )

    if permission_block:
        parts.append(permission_block)

    if capability_block:
        parts.append(
            "\n--- OPTIONAL CAPABILITIES ---\n"
            "Only use tools from capabilities that are enabled and configured.\n"
            f"Status: {capability_block}\n"
        )

    if tool_groups:
        domain_groups_str = ", ".join(sorted(tool_groups))
        parts.append(
            "\n--- OPTIONAL TOOL GROUPS (lazy-loaded) ---\n"
            "To keep your context lean, only core tools are shown by default. These extra "
            f"groups are available on demand: {domain_groups_str}.\n"
            'Call use_tools(group="<name>") to activate a group for the rest of the turn. '
            "load_skill(name=...) auto-activates that skill's own tools.\n"
        )

    return "".join(parts)


def compose(
    stable_base: str,
    user_profile_snapshot: str | None = None,
    agent_profile_snapshot: str | None = None,
    recalled_context: str | None = None,
) -> tuple[str, str]:
    """Compose the cache-aware (cacheable, dynamic) prompt split for a turn.

    Args:
        stable_base: The stable session prompt (from build_stable_base).
        user_profile_snapshot: Session-frozen user profile facts (goes in cacheable).
        agent_profile_snapshot: Session-frozen agent persona (goes in cacheable).
        recalled_context: Per-query vault recall (goes in dynamic — changes every turn).

    Returns:
        (cacheable_system, dynamic_system) where cacheable carries the cache breakpoint
        and dynamic sits after it so volatile content never invalidates the cache.
    """
    # Cacheable prefix: stable across the session.
    cacheable_parts = [stable_base]
    if user_profile_snapshot:
        cacheable_parts.append(
            f"\n\n--- USER PROFILE (captured for this session) ---\n{user_profile_snapshot}"
        )
    if agent_profile_snapshot:
        cacheable_parts.append(
            f"\n\n--- AGENT PROFILE (captured for this session) ---\n{agent_profile_snapshot}"
        )
    cacheable = "".join(cacheable_parts)

    # Dynamic tail: changes every turn — must sit AFTER the cache breakpoint.
    dynamic_parts = [_build_time_header()]
    if recalled_context:
        dynamic_parts.append(f"\n\n--- RECALLED CONTEXT ---\n{recalled_context}")
    dynamic = "".join(dynamic_parts)

    return cacheable, dynamic


# ---------------------------------------------------------------------------
# T-77 — the named prompt-template registry.
#
# This is a second, separately-testable responsibility (see the module
# docstring): a catalogue of the auxiliary one-shot LLM call sites' prompt
# TEXT (memory extraction/consolidation/summarisation, session titling,
# vision analysis, .og authoring), as opposed to the main agent loop's
# system-prompt ASSEMBLY above. Every entry is snapshot-tested in
# tests/test_prompt_registry.py.
# ---------------------------------------------------------------------------

PROMPT_REGISTRY_VERSION = "1"
"""Bumped whenever any registered template's text changes. Snapshot tests pin
the (version, digest) pair — see tests/test_prompt_registry.py."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A named, versioned prompt template.

    Attributes:
        name:    Stable lookup id, e.g. "memory.extract.typed.system".
        text:    The template body. May contain {placeholders} for .render().
        purpose: One line, for humans reading the registry.
    """

    name: str
    text: str
    purpose: str

    def render(self, **kwargs: object) -> str:
        """Substitute placeholders. Raises KeyError on a missing key — never silently."""
        return self.text.format(**kwargs)


_TEMPLATES: dict[str, PromptTemplate] = {}


def register_prompt(template: PromptTemplate) -> None:
    """Register *template*. Raises ValueError on a duplicate name."""
    if template.name in _TEMPLATES:
        raise ValueError(f"Prompt {template.name!r} is already registered.")
    _TEMPLATES[template.name] = template


def get_prompt(name: str) -> PromptTemplate:
    """Return the registered template. Raises KeyError if unknown — never returns a default."""
    try:
        return _TEMPLATES[name]
    except KeyError:
        raise KeyError(f"No prompt registered under name {name!r}.") from None


def all_prompts() -> dict[str, PromptTemplate]:
    """Return a snapshot (shallow copy) of every registered template, keyed by name."""
    return dict(_TEMPLATES)


# ---------------------------------------------------------------------------
# Registrations.
#
# Naming: <domain>.<operation>.<variant>.<role>. Grouped by source module so
# the drift documented in T-77 §3.4 (four memory-extraction prompts, four
# different response-quality gates) is visible at a glance. Text is verbatim
# from the original call site — see tests/test_prompt_registry.py for the
# per-entry byte-equality proof. Two entries (memory.extract.flat.user,
# memory.consolidate.user) escape literal JSON-example braces as {{ }} because
# they are also interpolated (T-77 Decision 2).
# ---------------------------------------------------------------------------

# --- core/session_titler.py --------------------------------------------------

register_prompt(
    PromptTemplate(
        name="session.title.system",
        text=(
            "You write a short, specific title for a conversation between a user and an AI "
            "agent, based on its opening exchange. Respond with ONLY the title: no quotes, "
            "no trailing punctuation, no preamble or explanation. Use Title Case, keep it "
            "to 8 words or fewer, and capture the user's actual intent (e.g. "
            "'Fix Failing Login Tests', 'Plan Q3 Marketing Budget')."
        ),
        purpose="Session titling — system prompt for generate_title().",
    )
)

# Site 11 is the one conditionally-assembled Tier A prompt (T-77 Decision 3):
# the original call site built it as
#     prompt = f"User: {user_message[:600]}"
#     assistant_message = (assistant_message or "").strip()
#     if assistant_message:
#         prompt += f"\nAgent: {assistant_message[:300]}"
#     prompt += "\n\nTitle:"
# The [:600]/[:300] truncation slices and the `if` stay at the call site
# (TEXT moves, LOGIC does not — T-77 §5.3); only the TEXT moved, split into
# two entries so no model-facing text is left inline. generate_title() passes
# an empty string for `agent` when there is no assistant message, and the
# rendered session.title.user.agent_line output otherwise.
register_prompt(
    PromptTemplate(
        name="session.title.user",
        text="User: {user}{agent}\n\nTitle:",
        purpose=(
            'Session titling — user prompt frame. `agent` is either "" or the '
            "rendered session.title.user.agent_line (T-77 Decision 3)."
        ),
    )
)

register_prompt(
    PromptTemplate(
        name="session.title.user.agent_line",
        text="\nAgent: {agent_message}",
        purpose="Session titling — the optional agent-turn line, appended only when present.",
    )
)

# --- tools/vision_tools.py ---------------------------------------------------

register_prompt(
    PromptTemplate(
        name="vision.analyze.default_focus",
        text="Describe what you see and use it to answer the user's request.",
        purpose="view_image — default focus text used when the caller passes no prompt.",
    )
)

register_prompt(
    PromptTemplate(
        name="vision.analyze.system",
        text=(
            "You are a vision analysis assistant. "
            "Describe the image and answer the user's question concisely."
        ),
        purpose="view_image — system prompt for the routed vision-model analysis call.",
    )
)

# --- tools/memory_tools.py: save_memory -> _smart_upsert (consolidation) ----

register_prompt(
    PromptTemplate(
        name="memory.consolidate.user",
        text=(
            "You are a memory consolidation engine.\n"
            "New Compound Fact to save: '{content}'\n\n"
            "Existing Facts in Database:\n"
            "{snippet}\n\n"
            "Analyze if the New Fact completely supersedes, contradicts, or overlaps "
            "with any Existing Facts. "
            "CRITICAL: Break the New Fact into STRICTLY ATOMIC components. "
            "For example: 'Mike is my boss and his email is X' -> "
            "['User\\'s boss is Mike', 'Mike\\'s email is X'].\n"
            "If an atomic component supersedes an old fact "
            "(e.g. Boss was X, now Boss is Y), add the old fact's ID to 'delete_ids'.\n"
            'Output pure JSON only: {{"delete_ids": ["id1", "id2"], '
            '"save_contents": ["atomic fact 1", "atomic fact 2"]}}'
        ),
        purpose=(
            "_smart_upsert — user prompt: new fact + existing candidates to dedup/merge. "
            "Literal JSON-example braces escaped as {{ }} (T-77 Decision 2)."
        ),
    )
)

register_prompt(
    PromptTemplate(
        name="memory.consolidate.system",
        text="You are a data merger. Output pure JSON only.",
        purpose="_smart_upsert — system prompt for the consolidation/dedup call.",
    )
)

# --- gateway/in_process_runtime.py: authoring_generate (.og patch gen) -----

register_prompt(
    PromptTemplate(
        name="authoring.patch.system",
        text=(
            "You are a precise code editor for Hachi .og skill files. "
            "The user will give you the current file content and a description of the change "
            "they want. Respond with ONLY the complete revised file content — no markdown fences, "
            "no explanations, no diff markers. Output the full file verbatim with only the "
            "requested modifications applied."
        ),
        purpose="authoring_generate — system prompt for the .og patch-generation call.",
    )
)

register_prompt(
    PromptTemplate(
        name="authoring.patch.user",
        text=(
            "File: {name}\n\n"
            "Current content:\n{original}\n\n"
            "Requested change: {prompt}\n\n"
            "Output the complete revised file content only."
        ),
        purpose="authoring_generate — user prompt: target name, current content, requested change.",
    )
)

# --- core/memory/manager.py: MemoryManager._generate_structured_summary ----
# (compaction summary; §3.4: NO response validation at all -- untouched here)

register_prompt(
    PromptTemplate(
        name="memory.summarize.system",
        text=(
            "You are a context summarization agent. "
            "Output ONLY the structured summary. Do NOT answer questions. "
            "Do NOT add commentary. Be concise — each section max 3 lines."
        ),
        purpose="_generate_structured_summary — system prompt. No response validation (§3.4).",
    )
)

register_prompt(
    PromptTemplate(
        name="memory.summarize.user",
        text=(
            "Summarize this dropped conversation segment into a structured checkpoint.\n"
            "Output ONLY these sections (skip any that have no content):\n\n"
            "## Active Task\n[The user's most recent request — quote verbatim if possible]\n\n"
            "## Completed Actions\n[Numbered list: what was done, which tool was used, "
            "the outcome]\n\n"
            "## Active State\n[Working directory, modified files, branch, test status if "
            "mentioned]\n\n"
            "## Key Decisions\n[Technical decisions made and WHY]\n\n"
            "## Blocked\n[Any unresolved errors — include exact error messages]\n\n"
            "Conversation transcript:\n{transcript}"
        ),
        purpose="_generate_structured_summary — user prompt: the 5-section checkpoint template.",
    )
)

# --- core/memory/manager.py: MemoryManager._flush_facts_to_vault -----------
# (compaction extraction; §3.4: gated by _is_substantive_fact at :1617)

register_prompt(
    PromptTemplate(
        name="memory.flush.user",
        text=(
            "Extract any permanent, atomic facts from this conversation snippet into a "
            "JSON list. "
            "If the user states a preference, a relationship, an architecture decision, "
            "or an environment detail, extract it. "
            "Do NOT extract tasks, commands, or transient status "
            "(e.g. 'I am coding', 'fix this bug'). "
            'Return ONLY a valid JSON array of strings, e.g. ["User\'s boss is Premraj", '
            '"User prefers Python"]. If none, return [].'
        ),
        purpose=(
            "_flush_facts_to_vault — static user-prompt prefix; the call site appends "
            "the snippet outside the template (retrieved via .text, not .render())."
        ),
    )
)

register_prompt(
    PromptTemplate(
        name="memory.flush.system",
        text="You are an elite data extraction agent. Output pure JSON only.",
        purpose="_flush_facts_to_vault — system prompt.",
    )
)

# --- core/memory/manager.py: extract_facts, flat JSON-bucket path ----------
# (§3.4: the STRONGEST-gated path -- _is_substantive_fact runs on all 3 buckets)

register_prompt(
    PromptTemplate(
        name="memory.extract.flat.user",
        text=(
            "Extract ONLY permanent facts from this exchange into three buckets.\n\n"
            "user_facts: Things about the USER's identity — name, role, timezone, "
            "preferences, habits, relationships, communication style.\n"
            "workspace_facts: Technical/environment facts — architecture decisions, "
            "project structure, API endpoints, tool configurations.\n"
            "agent_facts: BEHAVIORAL CORRECTIONS. Did the user tell YOU (the agent) to change your "
            "behavior? (e.g., 'stop apologizing', 'make it shorter', 'don't use emojis'). "
            "Extract these as clear rules (e.g. 'I do not use emojis').\n\n"
            "Rules:\n"
            "- Each fact must be a single atomic sentence.\n"
            "- Do NOT extract tasks, commands, or transient status.\n"
            "- If nothing is worth extracting, return empty arrays.\n\n"
            'Return ONLY valid JSON: {{"user_facts": [...], "workspace_facts": [...], '
            '"agent_facts": [...]}}\n\n'
            "Exchange:\n{snippet}"
        ),
        purpose=(
            "extract_facts (flat path) — user prompt: the 3-bucket extraction instructions. "
            "Literal JSON-example braces escaped as {{ }} (T-77 Decision 2)."
        ),
    )
)

register_prompt(
    PromptTemplate(
        name="memory.extract.flat.system",
        text="You are a fact extraction engine. Output pure JSON only.",
        purpose="extract_facts (flat path) — system prompt.",
    )
)

# --- core/memory/manager.py: extract_facts, typed/tool-call path -----------
# (§3.4: SHAPE-only gate -- _is_substantive_fact is NEVER called on this path)

register_prompt(
    PromptTemplate(
        name="memory.extract.typed.user",
        text="Extract memory from:\n\n{snippet}",
        purpose="extract_facts (typed path) — user prompt.",
    )
)

register_prompt(
    PromptTemplate(
        name="memory.extract.typed.system",
        text=(
            "You are a precision memory extraction engine. "
            "Extract ONLY durable facts worth keeping across sessions. "
            "Use the extract_memory_nodes tool. "
            "Return an empty nodes list if nothing is worth extracting."
        ),
        purpose=(
            "extract_facts (typed path) — system prompt. Schema-shape gate only; "
            "_is_substantive_fact is NOT applied on this path (§3.4)."
        ),
    )
)

register_prompt(
    PromptTemplate(
        name="memory.extract.tool_schema.description",
        text=(
            "Extract durable memory nodes from a conversation exchange. "
            "Only extract facts that should persist across sessions. "
            "Omit transient commands, status, and single-use instructions."
        ),
        purpose="extract_facts (typed path) — the extract_memory_nodes tool-schema description.",
    )
)
