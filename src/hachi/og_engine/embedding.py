"""EmbeddingRanker — optional ChromaDB cosine-similarity ranking for skill search.

Extracted from ``SkillEngine`` (T-73) so the ChromaDB dependency has its own seam,
separate from parsing/indexing/retrieval. ``chromadb`` is imported lazily inside
:meth:`EmbeddingRanker.enable`, exactly as it was on ``SkillEngine`` before the
extraction, so the ``.og`` engine stays usable in a process that never installs
chromadb — P3-04's hybrid rank is opt-in, not a hard dependency.

Owns no environment state: the caller supplies ``db_path`` explicitly (no
``get_hachi_paths()`` call lives in this module), so this collaborator can be
constructed and driven in complete isolation from Hachi's platform directories.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingRanker:
    """Owns the optional ChromaDB cosine-similarity collection for skill search.

    Disabled (``collection is None``) until :meth:`enable` is called. Degrades
    gracefully when chromadb is not installed: :meth:`enable` logs a warning and
    leaves the ranker disabled; :meth:`search` returns ``{}`` while disabled.
    """

    def __init__(self) -> None:
        self.collection: Any = None  # chromadb.Collection | None

    def enable(
        self,
        documents: Iterable[tuple[str, str]],
        *,
        db_path: Path,
        collection_name: str = "og_skill_index",
    ) -> None:
        """Build (or refresh) the collection from ``(qualified_id, document_text)`` pairs.

        Args:
            documents: ``(qualified_id, document_text)`` pairs for every node to index.
                Consumed once; pass a fresh iterable on each call.
            db_path: Directory for the Chroma persistent client's on-disk database.
            collection_name: Name of the Chroma collection to build/refresh.
        """
        try:
            import chromadb  # type: ignore[import-untyped]
            from chromadb.config import Settings  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("P3-04: chromadb not installed; embedding rank unavailable")
            return

        client = chromadb.PersistentClient(
            path=str(db_path), settings=Settings(anonymized_telemetry=False)
        )
        col = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        ids: list[str] = []
        docs: list[str] = []
        for qid, doc in documents:
            ids.append(qid)
            docs.append(doc)

        if ids:
            col.upsert(ids=ids, documents=docs)
        self.collection = col
        logger.debug("P3-04: embedding index built (%d nodes)", len(ids))

    def search(
        self, query: str, *, known_ids: Collection[str], top_k: int = 10
    ) -> dict[str, float]:
        """Query the Chroma cosine collection; return ``{qualified_id: score}`` in ``[0, 1]``.

        Args:
            query: The natural-language query to embed and search against.
            known_ids: The caller's currently-loaded qualified IDs. Bounds ``n_results``
                and filters out any id the caller no longer recognises.
            top_k: Maximum number of results to request from Chroma.

        Returns:
            An empty dict if the ranker is disabled (no ``enable()`` call yet) or the
            query fails for any reason (e.g. a stale/corrupt collection).
        """
        if self.collection is None:
            return {}
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(top_k, max(1, len(known_ids))),
                include=["distances"],
            )
            ids = results.get("ids", [[]])[0]
            dists = results.get("distances", [[]])[0]
            # Chroma cosine distance is in [0, 2]; convert to similarity in [0, 1].
            return {
                qid: max(0.0, 1.0 - (dist / 2.0))
                for qid, dist in zip(ids, dists, strict=False)
                if dist is not None and qid in known_ids
            }
        except Exception:
            logger.debug("P3-04: embedding search failed", exc_info=True)
            return {}
