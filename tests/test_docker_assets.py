"""Structural guards for the Docker self-host recipe (no daemon needed)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_runs_the_gateway() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "hachi" in dockerfile
    assert "gateway" in dockerfile
    assert "8787" in dockerfile


def test_dockerignore_excludes_heavy_dirs() -> None:
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    # Without these the build context would balloon to hundreds of MB.
    assert "frontend/node_modules" in ignore
    assert ".venv" in ignore
    assert ".git" in ignore


def test_compose_exposes_gateway_port() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "8787" in compose
