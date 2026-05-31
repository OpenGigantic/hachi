from __future__ import annotations

from pathlib import Path

from tokoboros.core.config_loader import load_effective_config
from tokoboros.core.paths import TokoborosPaths


def _paths(tmp_path: Path) -> TokoborosPaths:
    return TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )


def test_config_precedence_layers(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.legacy_dir.mkdir(parents=True, exist_ok=True)

    (paths.config_dir / "settings.toml").write_text(
        """
[provider]
kind = "anthropic"
model = "global-model"
""",
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    (project_dir / ".tokoboros").mkdir(parents=True, exist_ok=True)
    (project_dir / ".tokoboros" / "settings.toml").write_text(
        """
[provider]
model = "project-model"
""",
        encoding="utf-8",
    )
    (project_dir / ".env").write_text(
        "OPENAI_API_KEY=testkey\nOPENAI_MODEL=env-model\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    cfg = load_effective_config(
        project_dir=project_dir,
        cli_overrides={"provider": {"model": "cli-model"}},
        paths=paths,
    )

    assert cfg.provider.kind == "openai-compatible"
    assert cfg.provider.model == "cli-model"


def test_legacy_json_import_when_no_toml(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.legacy_dir.mkdir(parents=True, exist_ok=True)
    (paths.legacy_dir / "tokoboros.json").write_text(
        '{"agent": {"model": "claude-legacy", "max_tokens": 2048, "temperature": 0.2}}',
        encoding="utf-8",
    )

    cfg = load_effective_config(paths=paths)

    assert cfg.provider.kind == "anthropic"
    assert cfg.provider.model == "claude-legacy"
    assert cfg.agent.max_tokens == 2048
