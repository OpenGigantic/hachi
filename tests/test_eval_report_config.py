"""Regression tests for BB-101 — `[eval_reports]` config section registration.

``EvalReportSettings`` existed, the weekly failure-report daemon existed, and
``boot_agent`` read ``config.get("eval_reports", {})`` — but ``ResolvedConfig``
declared no ``eval_reports`` field, so ``ConfigDict(extra="ignore")`` silently
dropped the whole section during ``ResolvedConfig.model_validate()``. The
post-dump dict ``boot_agent`` reads was therefore always ``{}`` for that key,
``enabled`` was always ``False``, and the daemon never started — while the
loader simultaneously warned (or, under ``HACHI_CONFIG_STRICT=1``, raised)
that the user's own documented key was "unknown".

See docs/audit/2026-07-19/tasks/T-07-eval-reports-config-section.md and the
BB-101 entry in docs/bug-bank.md.
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from hachi.core.config_loader import _read_toml, _warn_unknown_keys, load_effective_config
from hachi.core.paths import HachiPaths
from hachi.core.settings import EvalReportSettings, ResolvedConfig


def _paths(tmp_path: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )


def test_eval_reports_section_survives_resolved_config(tmp_path: Path) -> None:
    """The documented [eval_reports] section must reach the exact dict boot_agent reads.

    This does not merely assert that a pydantic field exists — it replays the real
    path: a settings.toml on disk -> load_effective_config() -> model_dump(mode=
    "python", exclude_none=True) (gateway.workspace.load_config()'s own call) ->
    EvalReportSettings.model_validate(config.get("eval_reports", {})) (the exact
    line at orchestrator.py's E5 boot block). Asserting `.enabled is True` after
    that round-trip proves the setting actually changes the flag that gates
    whether the daemon starts, not just that a schema field is declared.
    """
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text(
        "[eval_reports]\nenabled = true\ninterval_seconds = 3600\nlimit = 50\n",
        encoding="utf-8",
    )

    resolved = load_effective_config(paths=paths)

    # gateway.workspace.load_config()'s exact call:
    dumped = resolved.model_dump(mode="python", exclude_none=True)
    assert "eval_reports" in dumped, (
        "the [eval_reports] section was dropped by ConfigDict(extra='ignore') — "
        "ResolvedConfig has no field for it"
    )

    # orchestrator.boot_agent's exact construction of the daemon gate:
    eval_cfg = EvalReportSettings.model_validate(dumped.get("eval_reports", {}))
    assert eval_cfg.enabled is True, (
        "the user's [eval_reports] enabled=true never reached the daemon's gate — "
        "boot_agent would see enabled=False and never start the scheduler"
    )
    assert eval_cfg.interval_seconds == 3600
    assert eval_cfg.limit == 50


def test_eval_reports_is_not_an_unknown_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A settings.toml with only the documented [eval_reports] section must load clean.

    Two assertions, both direct user-facing symptoms of this bug:
      1. No "unknown top-level key" WARNING is logged for a key the docs told the
         user to write (config_loader.py's default/non-strict path).
      2. `_warn_unknown_keys(..., strict=True)` — the HACHI_CONFIG_STRICT=1 boot
         path — does not raise. Before the fix this raised ValueError, so a user
         who follows the docs *and* enables strict mode gets a hard boot failure.
    """
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text("[eval_reports]\nenabled = true\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="hachi.core.config_loader"):
        result = _read_toml(settings_path)
    assert result  # parsed successfully
    assert not any("unknown top-level key" in record.message for record in caplog.records), (
        "the loader warned the user about their own documented [eval_reports] key"
    )

    # The strict / HACHI_CONFIG_STRICT=1 boot-failure path must not raise either.
    _warn_unknown_keys({"eval_reports": {"enabled": True}}, tmp_path / "settings.toml", strict=True)


# ---------------------------------------------------------------------------
# Generalised guard — this is the "make the next one loud" half of BB-101.
#
# T-02 already locks "a key documented in settings.example.toml with no
# ResolvedConfig field" (tests/test_config_loader.py::
# test_settings_example_toml_keys_are_known_config_fields). That only fires if
# someone *also* remembers to add the section to settings.example.toml. The gap
# this bug actually demonstrated is narrower and earlier: a `*Settings` model can
# be built, wired into a reader (`config.get("eval_reports", {})`), documented in
# prose (docs/DECISIONS.md), and reviewed — while nobody ever adds the one field
# to ResolvedConfig, and nothing fails until a user reports it. This test makes
# *that* omission fail at test time, independent of settings.example.toml, so the
# T-08 [memory_gardener] section (and any future one) cannot reproduce T-07/BB-101
# by forgetting the ResolvedConfig field, even if the example-file doc is also
# forgotten.
# ---------------------------------------------------------------------------


def _iter_model_types(annotation: object) -> typing.Iterator[type[BaseModel]]:
    """Yield every pydantic BaseModel subclass embedded in *annotation*.

    Handles arbitrary nesting: a plain model class, ``Model | None``,
    ``dict[str, Model]``, ``list[Model]``, etc.
    """
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _iter_model_types(arg)


def _reachable_settings_models(root: type[BaseModel]) -> set[type[BaseModel]]:
    """Every BaseModel subclass reachable from *root*'s field graph, at any depth."""
    seen: set[type[BaseModel]] = set()
    stack = [root]
    while stack:
        model = stack.pop()
        if model in seen:
            continue
        seen.add(model)
        for info in model.model_fields.values():
            stack.extend(_iter_model_types(info.annotation))
    return seen


def test_boot_agent_logs_when_eval_report_daemon_starts() -> None:
    """T-07 carried-over debt (closes BB-101's last acceptance criterion).

    `boot_agent`'s ``if _eval_cfg.enabled: _start_eval_report_scheduler(...)`` gate was
    silent at the call site — the daemon logs internally once its thread starts
    (``_start_eval_report_scheduler: started ...``), but nothing at the `boot_agent`
    level distinguished "I enabled it and it started" from "I enabled it and the
    config never arrived" (the exact BB-101 symptom). Structural (AST) check, not a
    full boot — see tests/test_routing_boot_failure.py's module docstring for why a
    real `boot_agent()` call is impractical here too.
    """
    import ast
    import inspect

    import hachi.gateway.orchestrator as orch

    tree = ast.parse(inspect.getsource(orch))
    boot_agent = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "boot_agent"
    )
    gate = next(
        (
            node
            for node in ast.walk(boot_agent)
            if isinstance(node, ast.If)
            and any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_start_eval_report_scheduler"
                for n in ast.walk(node)
            )
        ),
        None,
    )
    assert gate is not None, "boot_agent no longer calls _start_eval_report_scheduler(...)"

    logs_at_boot = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "info"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "logger"
        for n in ast.walk(gate)
    )
    assert logs_at_boot, (
        "boot_agent must logger.info(...) at the [eval_reports] enabled gate when the "
        "daemon starts — otherwise a user cannot tell 'I enabled it and it started' from "
        "'the config never arrived' (T-07 section 5 / BB-101)"
    )


def test_all_settings_models_are_registered_on_resolved_config() -> None:
    """Every `*Settings` model defined in hachi.core.settings must be reachable
    from ResolvedConfig (directly, or nested inside another registered section).

    An unreachable `*Settings` model is exactly this bug: its TOML section is
    silently dropped by ``ConfigDict(extra="ignore")`` during
    ``ResolvedConfig.model_validate()``, and the loader classifies the user's
    key as unknown. Before the BB-101 fix, this test fails on
    ``EvalReportSettings``. If T-08 (or anyone) adds a new optional config
    section model and forgets to register it on ``ResolvedConfig``, this test
    fails immediately, by name, instead of waiting for a user to hit the
    "unknown top-level key" warning in production.
    """
    import hachi.core.settings as settings_module

    defined_settings_models = {
        obj
        for obj in vars(settings_module).values()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == settings_module.__name__
        and obj.__name__.endswith("Settings")
    }
    reachable = _reachable_settings_models(ResolvedConfig)

    unregistered = defined_settings_models - reachable
    assert not unregistered, (
        "these *Settings models are defined in hachi.core.settings but are not "
        "reachable from ResolvedConfig (directly or nested), so their TOML section "
        f"would be silently dropped by extra='ignore': "
        f"{sorted(m.__name__ for m in unregistered)}. Add a field for each on "
        "ResolvedConfig in the same commit that defines the model (see BB-101)."
    )
