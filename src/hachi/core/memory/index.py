"""
MemoryIndex — P2-04 M0 read-side: FTS5 index over memory/*.og files.

Provides BM25-scored symbolic retrieval over the user's personal memory graph
(profile.og, agent.og, projects/*.og, people/*.og, journal/*.og). Results are
merged with existing Chroma semantic recall via Reciprocal Rank Fusion (RRF)
when ``memory.graph=true`` is set in config.

No data moves at M0 — this is purely additive. Existing Chroma collections are
unchanged; the MemoryIndex only reads from ``memory/*.og`` files.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hachi.core.migrations import SchemaTooNewError, ensure_schema_version, set_schema_version

logger = logging.getLogger(__name__)

# T-54: current schema version for memory_index.db. Unlike the other four live
# SQLite stores, a mismatch here rebuilds instead of raising — see _init_db().
_SCHEMA_VERSION = 1

_FTS5_SCHEMA = """
CREATE TABLE IF NOT EXISTS mem_nodes (
    node_id     TEXT PRIMARY KEY,
    file_stem   TEXT NOT NULL,
    node_type   TEXT NOT NULL DEFAULT 'fact',
    name        TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',
    dense       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    node_id,
    text,
    tokenize='porter ascii'
);
"""


def compute_node_id(memory_dir: Path, og_file: Path, node_name: str) -> str:
    """Return the composite ``<rel-path-without-suffix>:<node-name>`` id this
    index assigns to *node_name* when it lives in *og_file* under *memory_dir*.

    T-51: this is the single source of truth for the id format — used by
    ``refresh()`` to populate the index AND by any external caller (e.g.
    ``MemoryVault.delete_fact``) that needs to resolve a node_id for
    ``delete_node()``. A duplicated, independently-maintained copy of this
    format string is exactly how the original bug happened (the old
    ``MemoryVault._delete_from_fts5`` deleted by a bare Chroma SHA-256 that
    could never equal this composite id — see docs/bug-bank.md).
    """
    rel = og_file.relative_to(memory_dir)
    return f"{rel.with_suffix('')}:{node_name}"


class MemoryIndex:
    """SQLite FTS5 index over the ``memory/`` OG graph files.

    Usage::

        idx = MemoryIndex(memory_dir=paths.memory_dir, db_path=paths.memory_index_db)
        idx.refresh()                          # scan & index all memory/*.og files
        results = idx.search("database migration", top_k=5)

    Results have the same shape as ``MemoryVault.semantic_search()`` so they can
    be merged by ``_rrf_merge``.
    """

    def __init__(
        self,
        memory_dir: Path,
        db_path: Path,
    ) -> None:
        self._memory_dir = memory_dir
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_FTS5_SCHEMA)
            self._ensure_version(conn)

    def _ensure_version(self, conn: sqlite3.Connection) -> None:
        """Stamp/verify schema_version; rebuild (never raise) on ANY mismatch.

        T-54 Decision 4: memory_index.db is a DERIVED CACHE — refresh() always
        wipes and rebuilds it wholesale from memory/*.og (see the class docstring
        and docs/audit/2026-07-19/tasks/T-54-schema-version-tables.md §3.5). A
        version mismatch therefore has a remedy the other four SQLite stores do
        not have: drop the data tables, recreate them, stamp the current version,
        and let the next refresh() repopulate. Do NOT generalise this
        rebuild-on-mismatch behaviour to any other store — it is only safe here
        because this data is disposable.
        """
        try:
            stored = ensure_schema_version(conn, current=_SCHEMA_VERSION)
            mismatch = stored != _SCHEMA_VERSION
        except SchemaTooNewError:
            mismatch = True
        if not mismatch:
            return
        logger.info(
            "MemoryIndex: schema_version mismatch on %s — rebuilding (this is a "
            "derived cache; the next refresh() repopulates it from memory/*.og)",
            self._db_path,
        )
        conn.execute("DROP TABLE IF EXISTS mem_nodes")
        conn.execute("DROP TABLE IF EXISTS mem_fts")
        conn.commit()
        conn.executescript(_FTS5_SCHEMA)
        set_schema_version(conn, _SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def refresh(self) -> int:
        """Re-scan the memory dir and rebuild the FTS5 index. Returns node count."""
        if not self._memory_dir.exists():
            return 0

        og_files = list(self._memory_dir.rglob("*.og"))
        if not og_files:
            return 0

        try:
            from hachi.og_engine.parser import parse_og_file
        except ImportError:
            logger.warning("MemoryIndex: og_engine parser not available — index is empty")
            return 0

        # T-51: a tombstone's `->[:supersedes=<name>]` edge must suppress the
        # superseded node on every rebuild — otherwise write_nodes() calling
        # refresh() right after the tombstone is appended (memory_writer.py)
        # would immediately resurrect the row the tombstone was meant to
        # remove. og_engine's OGNode does not surface arbitrary
        # ->[:key=value] edges (only role/scope/keywords/requires/
        # requires_auth), so `supersedes` is detected via the lightweight
        # memory_graph parser instead, which mirrors memory_graph.py's own
        # `load_memory_dir` global-suppression rule.
        from hachi.core.memory.graph import parse_memory_file

        superseded: set[str] = set()
        for og_file in og_files:
            try:
                for gnode in parse_memory_file(og_file):
                    superseded.update(gnode.supersedes)
            except Exception as exc:
                logger.debug("MemoryIndex: supersede-scan skipped %s — %s", og_file, exc)

        nodes: list[dict[str, str]] = []
        for og_file in og_files:
            rel = og_file.relative_to(self._memory_dir)
            file_stem = rel.stem  # e.g. "profile" from "profile.og"
            try:
                parsed = parse_og_file(str(og_file))
            except Exception as exc:
                logger.debug("MemoryIndex: skipping %s — %s", og_file, exc)
                continue

            for node in parsed.nodes:
                if node.name in superseded:
                    continue
                node_id = compute_node_id(self._memory_dir, og_file, node.name)
                kw_text = " ".join(node.keywords)
                searchable = f"{node.name} {kw_text} {node.dense}".strip()
                nodes.append(
                    {
                        "node_id": node_id,
                        "file_stem": file_stem,
                        "node_type": node.role or "fact",
                        "name": node.name,
                        "keywords": kw_text,
                        "dense": node.dense or "",
                        "updated_at": datetime.now(UTC).isoformat(),
                        "searchable": searchable,
                    }
                )

        with self._conn() as conn:
            conn.execute("DELETE FROM mem_fts")
            conn.execute("DELETE FROM mem_nodes")
            if nodes:
                conn.executemany(
                    "INSERT OR REPLACE INTO mem_nodes"
                    " (node_id, file_stem, node_type, name, keywords, dense, updated_at)"
                    " VALUES (:node_id, :file_stem, :node_type,"
                    " :name, :keywords, :dense, :updated_at)",
                    nodes,
                )
                conn.executemany(
                    "INSERT INTO mem_fts (node_id, text) VALUES (:node_id, :searchable)",
                    nodes,
                )

        logger.debug("MemoryIndex: indexed %d nodes from %d files", len(nodes), len(og_files))
        return len(nodes)

    def delete_node(self, node_id: str) -> bool:
        """Remove a single node from the FTS5 index by its composite node_id.

        T-51: this is the public seam ``MemoryVault._delete_from_fts5`` calls
        instead of opening its own bypass ``sqlite3.connect`` against this
        class's private ``_db_path`` (see docs/bug-bank.md). *node_id* must be
        the ``<rel-path-without-suffix>:<node-name>`` composite id this class
        assigns during ``refresh()`` — use :func:`compute_node_id` to derive
        it rather than reconstructing the format independently.

        Goes through this class's own serialised ``_conn()`` so there is
        never more than one writer touching ``mem_fts``/``mem_nodes``.

        Returns:
            True if a row was removed from ``mem_nodes``; False when the id
            was not present or the delete failed (best-effort — never raises).
        """
        try:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM mem_nodes WHERE node_id = ?", (node_id,))
                conn.execute("DELETE FROM mem_fts WHERE node_id = ?", (node_id,))
                return cur.rowcount > 0
        except Exception as exc:
            logger.debug("MemoryIndex.delete_node: could not remove %s: %s", node_id, exc)
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """BM25-ranked search over memory nodes.

        Returns dicts compatible with ``MemoryVault.semantic_search()`` results:
        ``{id, content, raw_content, category, timestamp}``.
        """
        if not query.strip():
            return []

        # Sanitize: FTS5 MATCH will throw on unbalanced quotes/operators
        safe_query = _sanitize_fts5(query)
        if not safe_query:
            return []

        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT n.node_id, n.node_type, n.dense, n.name
                    FROM mem_fts AS f
                    JOIN mem_nodes n ON n.node_id = f.node_id
                    WHERE mem_fts MATCH ?
                    ORDER BY bm25(mem_fts)
                    LIMIT ?
                    """,
                    (safe_query, top_k),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.debug("MemoryIndex search error (%s): %s", query, exc)
            return []

        return [
            {
                "id": row["node_id"],
                "content": row["dense"] or row["name"],
                "raw_content": row["dense"] or row["name"],
                "category": row["node_type"],
                "timestamp": "",
            }
            for row in rows
        ]

    @property
    def node_count(self) -> int:
        """Number of indexed memory nodes."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM mem_nodes").fetchone()
            return int(row[0]) if row else 0


def _sanitize_fts5(query: str) -> str:
    """Strip FTS5 operator characters that would cause a syntax error."""
    # Remove characters that break FTS5 MATCH syntax
    unsafe = set("\"'()*^~:")
    cleaned = "".join(c for c in query if c not in unsafe)
    # Collapse whitespace and strip
    return " ".join(cleaned.split())


# ---------------------------------------------------------------------------
# RRF merge utility
# ---------------------------------------------------------------------------

_RRF_K = 60


def rrf_merge(
    list_a: list[dict[str, Any]],
    list_b: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion of two ranked result lists.

    Both lists contain dicts with an ``id`` field. The merged list keeps at most
    ``top_k`` results, ranked by combined RRF score (higher = more relevant).
    """
    scores: dict[str, float] = {}
    all_results: dict[str, dict[str, Any]] = {}

    for rank, item in enumerate(list_a):
        iid = item["id"]
        scores[iid] = scores.get(iid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        all_results[iid] = item

    for rank, item in enumerate(list_b):
        iid = item["id"]
        scores[iid] = scores.get(iid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        all_results.setdefault(iid, item)

    merged = sorted(all_results.values(), key=lambda r: scores[r["id"]], reverse=True)
    return merged[:top_k]
