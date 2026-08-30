"""T-77 -- prompt-registry snapshot lock.

``prompt_composer`` used to assemble only the main agent loop's system prompt
(``build_stable_base`` / ``compose``). Eight auxiliary LLM call sites (memory
extraction/consolidation/summarisation, session titling, vision analysis, .og
authoring) built their prompts as inline string literals instead, invisible to
any test. T-77 moves those 17 literals (18 registry entries -- session
titling's user prompt is conditionally assembled and splits into two, see
below) into a named, versioned ``PromptTemplate`` registry.

This is NOT a behavioural regression test -- no behaviour was wrong before this
change. It is the snapshot/pin instrument called for in
docs/audit/2026-07-19/tasks/T-77-consolidate-prompts-into-composer.md §7,
because nothing else in the repo would notice if a prompt's wording changed:

  - The eval corpus is loop-mechanics only. ``ScriptedLLMClient.generate``
    (``core/eval/replay.py:97``) takes ``**kwargs`` and never reads
    ``system_prompt`` or ``messages`` -- the eval gate is green whether these
    prompts are correct, mangled, or empty (§3.5).
  - The pre-existing 18 tests in ``tests/test_prompt_composer.py`` and
    ``tests/test_prompt_cache_ordering.py`` assert substring *placement*
    (cacheable vs. dynamic), never prompt *content*.
  - No test anywhere in ``tests/`` asserted on any of these 17 literals before
    this file was added (verified by grep).

Three kinds of test, per §7:
  (a) ``test_registered_prompts_match_snapshot`` -- the primary lock: every
      registered template's ``.text`` must match a checked-in digest.
  (b) One ``test_..._prompt_text_unchanged_by_move`` per registry entry --
      the move-is-lossless check. Static prompts pin ``.text`` verbatim;
      interpolated prompts pin what ``.render(**sample)`` produces against
      the *original* f-string evaluated with the same sample values, because
      an f-string's "original text" has no single byte value, only a shape
      (see the task's Decision 1). Brace-bearing prompts (sites 4 and 12)
      escape their literal JSON-example braces as ``{{``/``}}`` in ``.text``
      and are exercised only through ``.render()`` (Decision 2).
  (c) ``test_no_inline_system_prompt_literals_at_migrated_sites`` -- the
      completeness lock: the migrated modules no longer contain the literal
      prose, so no one can silently reintroduce an inline prompt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hachi.core.prompt_composer import PROMPT_REGISTRY_VERSION, all_prompts, get_prompt
from tests.conftest import REPO_ROOT


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 7a. The snapshot lock (the primary test).
# ---------------------------------------------------------------------------

# Snapshot as of PROMPT_REGISTRY_VERSION "1". Changing a registered prompt's
# .text REQUIRES updating this map (and bumping PROMPT_REGISTRY_VERSION) in
# the same commit -- prompt wording changes model behaviour, so the diff must
# be visible and deliberate, never silent.
_SNAPSHOT: dict[str, str] = {
    "memory.extract.tool_schema.description": "f2546779e55bb465",
    "memory.extract.typed.user": "f36fc7c1118987b3",
    "memory.extract.typed.system": "89839a00dc64c281",
    "memory.extract.flat.user": "6ecc9711498d50fc",
    "memory.extract.flat.system": "9cb82771fe0dd2fa",
    "memory.flush.user": "7d49d9f4950ffb6d",
    "memory.flush.system": "6468bd59a5fad915",
    "memory.summarize.user": "0a68459cceca2f6b",
    "memory.summarize.system": "e5a10a05fd06d16b",
    "session.title.system": "a1d884d685c0cbc8",
    "session.title.user": "8cdbbda9c4f0bae4",
    "session.title.user.agent_line": "d30821707773dacd",
    "memory.consolidate.user": "4e6c873c65ab826f",
    "memory.consolidate.system": "a95127555b0e60ce",
    "vision.analyze.default_focus": "46211685e4185dc9",
    "vision.analyze.system": "98a7bdfef1248091",
    "authoring.patch.system": "6e28c9b4d8a1178f",
    "authoring.patch.user": "4128ce4e3b267092",
}


def test_registered_prompts_match_snapshot() -> None:
    """T-77: a registered prompt cannot change without a deliberate snapshot update."""
    assert PROMPT_REGISTRY_VERSION == "1"

    live = {name: _digest(t.text) for name, t in all_prompts().items()}
    assert live == _SNAPSHOT, (
        "A registered prompt changed (or the registry's entry set changed). If "
        "intentional: update _SNAPSHOT (and bump PROMPT_REGISTRY_VERSION if a "
        "template's TEXT changed). If not: revert. Prompt text is model behaviour."
    )


# ---------------------------------------------------------------------------
# 7b. The move-is-lossless check -- one per registry entry (18 entries; site
# 11 splits into two templates but is pinned by a single composition test,
# see its docstring below).
# ---------------------------------------------------------------------------

# --- core/memory/manager.py: the typed/tool extraction path -----------------


def test_memory_extract_tool_schema_description_prompt_text_unchanged_by_move() -> None:
    """T-77 site 1 (memory.extract.tool_schema.description): static. This is the
    extract_memory_nodes tool-schema description -- model-facing via the tool
    definition, not a system/user message, but still prompt text that moves."""
    assert get_prompt("memory.extract.tool_schema.description").text == (
        "Extract durable memory nodes from a conversation exchange. "
        "Only extract facts that should persist across sessions. "
        "Omit transient commands, status, and single-use instructions."
    )


def test_memory_extract_typed_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 2 (memory.extract.typed.user): interpolated. Original call site:
    ``content=f"Extract memory from:\\n\\n{snippet}"``. Per Decision 1, pin the
    RENDERED text against the original f-string evaluated with a sample value."""
    snippet = "User: I use FastAPI 0.115.\nAssistant: Noted."
    original = f"Extract memory from:\n\n{snippet}"
    assert get_prompt("memory.extract.typed.user").render(snippet=snippet) == original


def test_memory_extract_typed_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 3 (memory.extract.typed.system): static. §3.4: this prompt's
    response is validated for SHAPE only -- _is_substantive_fact is never
    called on this path. That gate placement is untouched by this move."""
    assert get_prompt("memory.extract.typed.system").text == (
        "You are a precision memory extraction engine. "
        "Extract ONLY durable facts worth keeping across sessions. "
        "Use the extract_memory_nodes tool. "
        "Return an empty nodes list if nothing is worth extracting."
    )


# --- core/memory/manager.py: the flat JSON-bucket extraction path -----------


def test_memory_extract_flat_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 4 (memory.extract.flat.user): interpolated AND brace-bearing
    (Decision 2). The literal ``{"user_facts": [...], ...}`` JSON example is
    escaped as ``{{``/``}}`` in .text; render() must still reproduce the
    ORIGINAL f-string byte-for-byte, braces included."""
    snippet = "User: my boss's name is Priya.\nAssistant: got it."
    original = (
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
        'Return ONLY valid JSON: {"user_facts": [...], "workspace_facts": [...], '
        '"agent_facts": [...]}\n\n'
        f"Exchange:\n{snippet}"
    )
    assert get_prompt("memory.extract.flat.user").render(snippet=snippet) == original


def test_memory_extract_flat_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 5 (memory.extract.flat.system): static. §3.4: this IS the
    strongest-gated path -- _is_substantive_fact runs on all three buckets."""
    assert get_prompt("memory.extract.flat.system").text == (
        "You are a fact extraction engine. Output pure JSON only."
    )


# --- core/memory/manager.py: _flush_facts_to_vault (compaction extraction) --


def test_memory_flush_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 6 (memory.flush.user): static. The anchor table's note: the
    snippet concatenation (``f"{prompt}\\n\\nSnippet:\\n{snippet}"``) stays at
    the call site -- only the static prefix moves into the registry, retrieved
    via .text (not .render()) and concatenated exactly as before."""
    assert get_prompt("memory.flush.user").text == (
        "Extract any permanent, atomic facts from this conversation snippet into a "
        "JSON list. "
        "If the user states a preference, a relationship, an architecture decision, "
        "or an environment detail, extract it. "
        "Do NOT extract tasks, commands, or transient status "
        "(e.g. 'I am coding', 'fix this bug'). "
        'Return ONLY a valid JSON array of strings, e.g. ["User\'s boss is Premraj", '
        '"User prefers Python"]. If none, return [].'
    )


def test_memory_flush_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 7 (memory.flush.system): static. §3.4: gated by
    _is_substantive_fact (:1617); fence-strip handles only ```json, not bare
    ``` (deliberate -- see the docstring at _flush_facts_to_vault)."""
    assert get_prompt("memory.flush.system").text == (
        "You are an elite data extraction agent. Output pure JSON only."
    )


# --- core/memory/manager.py: _generate_structured_summary (compaction) ------


def test_memory_summarize_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 8 (memory.summarize.user): interpolated. Original call site:
    ``f"...\\n\\n" f"Conversation transcript:\\n{transcript}"``."""
    transcript = "[USER]: fix the bug\n\n[ASSISTANT]: [tool_call: run_tests]"
    original = (
        "Summarize this dropped conversation segment into a structured checkpoint.\n"
        "Output ONLY these sections (skip any that have no content):\n\n"
        "## Active Task\n[The user's most recent request — quote verbatim if possible]\n\n"
        "## Completed Actions\n[Numbered list: what was done, which tool was used, "
        "the outcome]\n\n"
        "## Active State\n[Working directory, modified files, branch, test status if "
        "mentioned]\n\n"
        "## Key Decisions\n[Technical decisions made and WHY]\n\n"
        "## Blocked\n[Any unresolved errors — include exact error messages]\n\n"
        f"Conversation transcript:\n{transcript}"
    )
    assert get_prompt("memory.summarize.user").render(transcript=transcript) == original


def test_memory_summarize_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 9 (memory.summarize.system): static. §3.4: this path applies
    NO validation at all to the response -- that absence is untouched here."""
    assert get_prompt("memory.summarize.system").text == (
        "You are a context summarization agent. "
        "Output ONLY the structured summary. Do NOT answer questions. "
        "Do NOT add commentary. Be concise — each section max 3 lines."
    )


# --- core/session_titler.py --------------------------------------------------


def test_session_title_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 10 (session.title.system): static. Formerly the module
    constant ``_TITLE_SYSTEM_PROMPT`` -- the only pre-existing named prompt
    constant in the repo, and the site the task's Decision 3 says to do first."""
    assert get_prompt("session.title.system").text == (
        "You write a short, specific title for a conversation between a user and an AI "
        "agent, based on its opening exchange. Respond with ONLY the title: no quotes, "
        "no trailing punctuation, no preamble or explanation. Use Title Case, keep it "
        "to 8 words or fewer, and capture the user's actual intent (e.g. "
        "'Fix Failing Login Tests', 'Plan Q3 Marketing Budget')."
    )


def test_session_title_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 11 (session.title.user + session.title.user.agent_line).

    Site 11 is the one conditionally-assembled prompt (Decision 3): the
    original call site built it as::

        prompt = f"User: {user_message[:600]}"
        assistant_message = (assistant_message or "").strip()
        if assistant_message:
            prompt += f"\\nAgent: {assistant_message[:300]}"
        prompt += "\\n\\nTitle:"

    The ``[:600]``/``[:300]`` truncation slices and the ``if`` stay at the call
    site (session_titler.py) -- only the TEXT moved, split into two registry
    entries (``session.title.user`` and the conditional
    ``session.title.user.agent_line``) so that no model-facing text is left
    inline. This single test covers both entries by composing them the way
    ``generate_title()`` now does, in both the with- and without-assistant-
    message branches, and checks the result is byte-identical to the original
    inline f-string in each branch. (A cleaner decomposition than one test per
    entry: the two templates are only meaningful in combination -- see the
    task report for why this was chosen over two independent, less meaningful
    single-entry tests.)
    """
    user_message = "How do I configure the model router for vision calls?"
    assistant_message = "You can set it under [routing.capabilities].vision in settings.toml."

    # Branch 1: an assistant message is present.
    original_with_agent = (
        f"User: {user_message[:600]}" + f"\nAgent: {assistant_message[:300]}" + "\n\nTitle:"
    )
    agent_line = get_prompt("session.title.user.agent_line").render(
        agent_message=assistant_message[:300]
    )
    rendered_with_agent = get_prompt("session.title.user").render(
        user=user_message[:600], agent=agent_line
    )
    assert rendered_with_agent == original_with_agent

    # Branch 2: no assistant message -- the original `if assistant_message:`
    # guard is False, so the "\nAgent: " line is never appended at all.
    original_without_agent = f"User: {user_message[:600]}" + "\n\nTitle:"
    rendered_without_agent = get_prompt("session.title.user").render(
        user=user_message[:600], agent=""
    )
    assert rendered_without_agent == original_without_agent


# --- tools/memory_tools.py: save_memory -> _smart_upsert (consolidation) ----


def test_memory_consolidate_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 12 (memory.consolidate.user): interpolated AND brace-bearing
    (Decision 2), same mechanism as site 4: the literal
    ``{"delete_ids": [...], "save_contents": [...]}`` JSON example is escaped
    as ``{{``/``}}`` in .text; render() must reproduce the original byte-for-byte."""
    content = "User's boss is Mike and his email is mike@example.com"
    snippet = "[abc123] User's boss is Dave"
    original = (
        "You are a memory consolidation engine.\n"
        f"New Compound Fact to save: '{content}'\n\n"
        "Existing Facts in Database:\n"
        f"{snippet}\n\n"
        "Analyze if the New Fact completely supersedes, contradicts, or overlaps "
        "with any Existing Facts. "
        "CRITICAL: Break the New Fact into STRICTLY ATOMIC components. "
        "For example: 'Mike is my boss and his email is X' -> "
        "['User\\'s boss is Mike', 'Mike\\'s email is X'].\n"
        "If an atomic component supersedes an old fact "
        "(e.g. Boss was X, now Boss is Y), add the old fact's ID to 'delete_ids'.\n"
        'Output pure JSON only: {"delete_ids": ["id1", "id2"], '
        '"save_contents": ["atomic fact 1", "atomic fact 2"]}'
    )
    rendered = get_prompt("memory.consolidate.user").render(content=content, snippet=snippet)
    assert rendered == original


def test_memory_consolidate_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 13 (memory.consolidate.system): static."""
    assert get_prompt("memory.consolidate.system").text == (
        "You are a data merger. Output pure JSON only."
    )


# --- tools/vision_tools.py: view_image --------------------------------------


def test_vision_analyze_default_focus_prompt_text_unchanged_by_move() -> None:
    """T-77 site 14 (vision.analyze.default_focus): static. The default focus
    text used when the caller passes no ``prompt`` argument to view_image()."""
    assert get_prompt("vision.analyze.default_focus").text == (
        "Describe what you see and use it to answer the user's request."
    )


def test_vision_analyze_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 15 (vision.analyze.system): static."""
    assert get_prompt("vision.analyze.system").text == (
        "You are a vision analysis assistant. "
        "Describe the image and answer the user's question concisely."
    )


# --- gateway/in_process_runtime.py: authoring_generate (.og patch gen) -----


def test_authoring_patch_system_prompt_text_unchanged_by_move() -> None:
    """T-77 site 16 (authoring.patch.system): static."""
    assert get_prompt("authoring.patch.system").text == (
        "You are a precise code editor for Hachi .og skill files. "
        "The user will give you the current file content and a description of the change "
        "they want. Respond with ONLY the complete revised file content — no markdown fences, "
        "no explanations, no diff markers. Output the full file verbatim with only the "
        "requested modifications applied."
    )


def test_authoring_patch_user_prompt_text_unchanged_by_move() -> None:
    """T-77 site 17 (authoring.patch.user): interpolated. Original call site:
    ``f"File: {name}\\n\\n" f"Current content:\\n{original}\\n\\n"
    f"Requested change: {prompt}\\n\\n" "Output the complete revised file content only."``."""
    name = "greeter.og"
    original_content = "node hello\n  say: hi\n"
    change_prompt = "make it say hello world"
    original = (
        f"File: {name}\n\n"
        f"Current content:\n{original_content}\n\n"
        f"Requested change: {change_prompt}\n\n"
        "Output the complete revised file content only."
    )
    rendered = get_prompt("authoring.patch.user").render(
        name=name, original=original_content, prompt=change_prompt
    )
    assert rendered == original


# ---------------------------------------------------------------------------
# 7c. The completeness lock.
# ---------------------------------------------------------------------------


def test_no_inline_system_prompt_literals_at_migrated_sites() -> None:
    """T-77 §7c: crude and effective -- read each migrated module as text and
    assert its (former) inline prompt literal is no longer there. FAILS before
    the move because the literal IS there today; fails again in the future if
    anyone reintroduces an inline prompt at a migrated site."""
    repo_root = REPO_ROOT
    src = repo_root / "src" / "hachi"

    manager_py = src / "core" / "memory" / "manager.py"
    session_titler_py = src / "core" / "session_titler.py"
    memory_tools_py = src / "tools" / "memory_tools.py"
    vision_tools_py = src / "tools" / "vision_tools.py"
    in_process_runtime_py = src / "gateway" / "in_process_runtime.py"

    checks: list[tuple[Path, str]] = [
        # core/memory/manager.py -- 9 literals, 4 prompts (§3.4 HAZARD 1).
        (manager_py, "Extract durable memory nodes from a conversation exchange."),
        (manager_py, "Extract memory from:"),
        (manager_py, "You are a precision memory extraction engine."),
        (manager_py, "Extract ONLY permanent facts from this exchange into three buckets."),
        (manager_py, "You are a fact extraction engine. Output pure JSON only."),
        (manager_py, "Extract any permanent, atomic facts from this conversation snippet"),
        (manager_py, "You are an elite data extraction agent. Output pure JSON only."),
        (manager_py, "Summarize this dropped conversation segment into a structured checkpoint."),
        (manager_py, "You are a context summarization agent. "),
        # core/session_titler.py -- 2 literals (one conditionally assembled).
        (session_titler_py, "You write a short, specific title for a conversation"),
        (session_titler_py, r"\n\nTitle:"),
        # tools/memory_tools.py -- 2 literals.
        (memory_tools_py, "You are a memory consolidation engine."),
        (memory_tools_py, "You are a data merger. Output pure JSON only."),
        # tools/vision_tools.py -- 2 literals.
        (vision_tools_py, "Describe what you see and use it to answer the user's request."),
        (vision_tools_py, "You are a vision analysis assistant."),
        # gateway/in_process_runtime.py -- 2 literals.
        (in_process_runtime_py, "You are a precise code editor for Hachi .og skill files."),
        (in_process_runtime_py, "Requested change: "),
    ]

    offenders = []
    for path, literal in checks:
        text = path.read_text(encoding="utf-8")
        if literal in text:
            offenders.append(f"{path.relative_to(repo_root).as_posix()}: {literal!r}")

    assert offenders == [], (
        "Inline prompt literal(s) found at a site that should now resolve via "
        f"get_prompt(...) instead: {offenders}"
    )
