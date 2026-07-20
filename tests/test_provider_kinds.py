"""T-12 regression tests: ``ProviderKind`` must match what `create_llm_client` implements.

`core/llm.py`'s `create_llm_client` implements two provider kinds — ``gemini`` and
``chatgpt-web`` — that `core/settings.py`'s `ProviderKind` Literal did not list, so
`ResolvedConfig.model_validate` rejected a documented, factory-supported config value
(`docs/guides/configuration.md:385`) with a pydantic `literal_error`, and
`hachi doctor` (`core/config_validate.py`) independently reported `unknown provider
kind`. See docs/audit/2026-07-19/tasks/T-12-provider-kinds-schema.md and BB-121.
"""

from __future__ import annotations

import ast
import inspect
from typing import get_args

import pytest

from hachi.core.settings import ProviderKind, ResolvedConfig

# Every kind docs/guides/configuration.md:385 promises is "supported".
DOCUMENTED_PROVIDER_KINDS = [
    "anthropic",
    "openai-compatible",
    "openrouter",
    "groq",
    "together",
    "deepseek",
    "ollama",
    "gemini",
    "chatgpt-web",
]


@pytest.mark.parametrize("kind", DOCUMENTED_PROVIDER_KINDS)
def test_documented_provider_kinds_validate(kind: str) -> None:
    """Every kind the docs promise must pass real schema validation, not just parse
    as a Python string — this is `ResolvedConfig.model_validate`, the exact call
    `config_loader.load_effective_config` makes.
    """
    cfg = ResolvedConfig.model_validate({"provider": {"kind": kind, "model": "m"}})
    assert cfg.provider.kind == kind


def test_provider_kind_literal_covers_every_implemented_kind() -> None:
    """The schema must accept every kind `create_llm_client` can actually build.

    Parses `if kind == "...":` comparisons out of `create_llm_client`'s source
    (rather than hardcoding an expected set) so this test stays true when someone
    adds a new provider branch without touching `settings.py` — the exact failure
    mode that produced this bug.
    """
    import hachi.core.llm as llm

    tree = ast.parse(inspect.getsource(llm.create_llm_client))
    implemented = {
        node.comparators[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "kind"
        and isinstance(node.ops[0], ast.Eq)
        and isinstance(node.comparators[0], ast.Constant)
        and isinstance(node.comparators[0].value, str)
    }
    # Sanity check on the extraction itself: if this drops to empty (e.g. the
    # function gets refactored to a dispatch dict), the "not missing" assertion
    # below would pass vacuously and stop being a regression guard.
    assert {"anthropic", "ollama", "gemini", "chatgpt-web"} <= implemented, (
        "AST walk did not find the expected 'kind == \"...\"' comparisons in "
        f"create_llm_client; got {sorted(implemented)}. The extraction logic may "
        "need updating to match a refactor of create_llm_client."
    )

    declared = set(get_args(ProviderKind))
    missing = sorted(implemented - declared)
    assert not missing, f"create_llm_client implements kinds absent from ProviderKind: {missing}"


def test_config_validate_valid_kinds_includes_gemini_and_chatgpt_web() -> None:
    """`hachi doctor`'s `_VALID_KINDS` derives from the `ProviderKind` Literal
    (`config_validate.py:15`), so extending the Literal must fix both halves of
    the bug without a separate edit to `config_validate.py`.
    """
    from hachi.core.config_validate import _VALID_KINDS

    assert "gemini" in _VALID_KINDS
    assert "chatgpt-web" in _VALID_KINDS


def test_chatgpt_web_schema_acceptance_does_not_weaken_tos_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema acceptance of "chatgpt-web" must not make the provider usable
    without the `HACHI_UNOFFICIAL` opt-in.

    The two halves of this task: the config now *validates* (no more silent
    schema rejection), but `create_llm_client` must still refuse to build the
    client — loudly, with the ToS warning — at construction time. Does not
    instantiate `ChatGPTWebClient` itself (the gate raises before that import is
    reached), so this does not depend on the `unofficial` extra being installed.
    """
    monkeypatch.delenv("HACHI_UNOFFICIAL", raising=False)

    # The "make it work" half: the schema accepts the documented value.
    cfg = ResolvedConfig.model_validate({"provider": {"kind": "chatgpt-web"}})
    assert cfg.provider.kind == "chatgpt-web"

    # The "make its failure loud" half: the ToS gate still fires at construction.
    from hachi.core.llm import create_llm_client

    with pytest.raises(ValueError, match="HACHI_UNOFFICIAL"):
        create_llm_client(cfg.provider.model_dump())
