"""Render an ObjectGraph skill dependency graph to Mermaid or Graphviz DOT.

The ``.og`` format *is* an object graph, so make it visible. These pure
functions turn the structure from :meth:`SkillEngine.get_skill_graph` into:

- **Mermaid** — renders inline on GitHub/GitLab/Obsidian/most Markdown viewers
  with no tooling; paste it straight into a README.
- **Graphviz DOT** — pipe to ``dot -Tsvg`` for a publication-quality diagram.

Both color nodes by role and highlight entry-point roots, dependency cycles, and
missing (referenced-but-unloaded) dependencies. Pure (no I/O) → unit-testable.
"""

from __future__ import annotations

import re
from typing import Any

# Role → fill color (GitHub-dark friendly). Unknown roles fall back to grey.
_ROLE_COLORS: dict[str, str] = {
    "skill": "#238636",  # green
    "rule": "#8957e5",  # purple
    "integration": "#1f6feb",  # blue
    "env": "#9e6a03",  # amber
    "general": "#30363d",  # grey
}
_FALLBACK_COLOR = "#30363d"
_ROOT_COLOR = "#1f6feb"
_CYCLE_COLOR = "#f85149"
_MISSING_COLOR = "#f85149"


def _slug(name: str) -> str:
    """A Mermaid-safe node id (Mermaid ids must be alphanumeric/underscore)."""
    return re.sub(r"\W", "_", name)


def _node_color(node: dict[str, Any]) -> str:
    return _ROLE_COLORS.get(str(node.get("role") or "general"), _FALLBACK_COLOR)


def render_mermaid(graph: dict[str, Any]) -> str:
    """Render the skill graph as a Mermaid ``graph TD`` diagram."""
    nodes = graph.get("nodes", [])
    roots = set(graph.get("roots", []))
    cyclic = set(graph.get("cyclic", []))
    missing = sorted(set(graph.get("missing", [])))
    edges = graph.get("edges", [])

    lines = ["graph TD"]
    ids: dict[str, str] = {}

    for node in nodes:
        name = str(node["name"])
        ids[name] = _slug(name)
        if name in cyclic:
            cls = "cycle"
        elif name in roots:
            cls = "root"
        else:
            cls = f"role_{_slug(str(node.get('role') or 'general'))}"
        lines.append(f'    {ids[name]}["{name}"]:::{cls}')

    for name in missing:
        ids[name] = _slug(name)
        lines.append(f'    {ids[name]}["{name} (missing)"]:::missing')

    for src, dst in edges:
        lines.append(f"    {ids.get(src, _slug(src))} --> {ids.get(dst, _slug(dst))}")

    # Style classes (defined after nodes/edges, which Mermaid allows).
    lines.append(f"    classDef root fill:{_ROOT_COLOR},stroke:{_ROOT_COLOR},color:#ffffff;")
    lines.append(
        f"    classDef cycle fill:#3d1d1d,stroke:{_CYCLE_COLOR},"
        "stroke-width:3px,color:#f85149;"
    )
    lines.append(
        f"    classDef missing fill:#2d2d2d,stroke:{_MISSING_COLOR},"
        "color:#f85149,stroke-dasharray:5 3;"
    )
    for role in sorted({str(node.get("role") or "general") for node in nodes}):
        color = _ROLE_COLORS.get(role, _FALLBACK_COLOR)
        lines.append(f"    classDef role_{_slug(role)} fill:{color},stroke:#8b949e,color:#ffffff;")

    return "\n".join(lines)


def render_dot(graph: dict[str, Any]) -> str:
    """Render the skill graph as a Graphviz DOT digraph (pipe to ``dot -Tsvg``)."""
    nodes = graph.get("nodes", [])
    roots = set(graph.get("roots", []))
    cyclic = set(graph.get("cyclic", []))
    missing = sorted(set(graph.get("missing", [])))
    edges = graph.get("edges", [])

    lines = [
        "digraph skills {",
        "    rankdir=LR;",
        "    bgcolor=\"#0d1117\";",
        '    node [shape=box, style="rounded,filled", fontname="sans-serif", '
        'fontcolor="#ffffff", color="#8b949e"];',
        '    edge [color="#8b949e"];',
    ]

    for node in nodes:
        name = str(node["name"])
        if name in cyclic:
            fill = _CYCLE_COLOR
        elif name in roots:
            fill = _ROOT_COLOR
        else:
            fill = _node_color(node)
        lines.append(f'    "{name}" [fillcolor="{fill}"];')

    for name in missing:
        lines.append(
            f'    "{name}" [label="{name}\\n(missing)", style="rounded,dashed", '
            f'fillcolor="#2d2d2d", color="{_MISSING_COLOR}", fontcolor="#f85149"];'
        )

    for src, dst in edges:
        lines.append(f'    "{src}" -> "{dst}";')

    lines.append("}")
    return "\n".join(lines)


def render_graph(graph: dict[str, Any], fmt: str) -> str:
    """Dispatch to the requested renderer. Raises ValueError on unknown ``fmt``."""
    fmt = fmt.lower()
    if fmt == "mermaid":
        return render_mermaid(graph)
    if fmt == "dot":
        return render_dot(graph)
    raise ValueError(f"Unknown format: {fmt!r} (expected 'mermaid' or 'dot')")
