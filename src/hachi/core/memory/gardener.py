"""memory_gardener.py — P3-02 M3: nightly dedupe/supersede/expire gardener.

Governance rule: appends-only. The gardener never overwrites existing node content;
it appends ``::changelog`` entries and ``supersedes`` edges.

Auto-applies only whitelisted low-risk changes (exact-name deduplication: the same
node name appearing in more than one file — keep newest, mark older as superseded).
Controversial proposals (near-duplicate content, contradiction detection, expiry) are
returned as ``pending_review`` items.

T-08 (Option A — wired): a resident daemon (``gateway/orchestrator.py``'s
``_start_memory_gardener``, opt-in via ``[memory_gardener] enabled = true``) calls
:meth:`MemoryGardener.run` on an interval. ``pending_review`` proposals are reachable
via ``GET /memory/diffs`` and decided (approve/reject) via
``POST /memory/diffs/{proposal_id}`` — see :meth:`MemoryGardener.decide`, which applies
an approval through the same append-only ``::changelog`` mechanism as the auto-apply
path. The web frontend's Memory screen ("Gardener" tab) is the review surface; there is
no TUI Memory-Diff screen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ProposalKind(StrEnum):
    DEDUP_SAME_NAME = "dedup_same_name"
    MERGE_SIMILAR = "merge_similar"
    SUPERSEDE_CONTRADICTION = "supersede_contradiction"
    EXPIRE_STALE = "expire_stale"


@dataclass
class GardeningProposal:
    """A single proposed memory change from the gardener."""

    kind: str  # ProposalKind value
    name_a: str  # primary node name
    name_b: str = ""  # second node (for merge/supersede proposals)
    reason: str = ""
    confidence: float = 1.0
    file_path_a: str = ""
    file_path_b: str = ""
    auto_applied: bool = False
    # T-08 A3: stable identifier so a client can round-trip GET /memory/diffs ->
    # POST /memory/diffs/{id} without relying on list order (which can shift between
    # two gardener runs). Deterministic from the identifying fields, so the SAME
    # proposal re-detected on the next scheduled tick keeps the same id. Backward
    # compatible with JSON written before this field existed: __post_init__ fills it
    # in when absent (empty string), and GardeningResult.load() passes it straight
    # through once persisted.
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            basis = f"{self.kind}|{self.name_a}|{self.name_b}|{self.file_path_a}|{self.file_path_b}"
            self.id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class GardeningResult:
    """Output of a single gardener run."""

    run_ts: str = ""
    auto_applied: list[GardeningProposal] = field(default_factory=list)
    pending_review: list[GardeningProposal] = field(default_factory=list)

    def save(self, path: Path) -> None:
        """Persist the result to a JSON file for the review surface to read."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "run_ts": self.run_ts,
                    "auto_applied": [asdict(p) for p in self.auto_applied],
                    "pending_review": [asdict(p) for p in self.pending_review],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> GardeningResult:
        """Load a persisted result. Returns empty result if file is missing."""
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            auto = [GardeningProposal(**p) for p in raw.get("auto_applied", [])]
            pending = [GardeningProposal(**p) for p in raw.get("pending_review", [])]
            return cls(run_ts=raw.get("run_ts", ""), auto_applied=auto, pending_review=pending)
        except Exception:
            # T-08 Option A: a swallowed-to-debug corrupt-file read used to mean a
            # user's approve/reject silently landed on an empty result with zero
            # signal. Now wired to a live route, this must be loud (warning level,
            # not debug) — see acceptance criteria in docs/audit/2026-07-19/tasks/
            # T-08-memory-gardener.md §8.
            logger.warning("GardeningResult.load failed for %s", path, exc_info=True)
            return cls()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_NODE_RE = re.compile(r"::node\[([^\]]+)\]", re.MULTILINE)
_EDGE_RE = re.compile(r"->?\s*\[:(\w+)=([^\]]+)\]", re.MULTILINE)
_DENSE_RE = re.compile(r"::dense\n(.+?)(?=\n::|$)", re.DOTALL)
_VALID_UNTIL_RE = re.compile(r"->?\s*\[:valid_until=([^\]]+)\]", re.MULTILINE)


@dataclass
class _ParsedNode:
    name: str
    dense: str
    file_path: str
    mtime: float
    valid_until: str = ""


def _parse_dir(memory_dir: Path) -> dict[str, list[_ParsedNode]]:
    """Parse all .og files in memory_dir → name → list of ParsedNode (one per file).

    BB-177: iterated in a stable, filesystem-independent order (sorted by full
    path string), never raw ``Path.rglob()`` order. The stdlib documents glob
    order as unspecified ("the order of the returned list is undefined"), and
    it is realised differently per filesystem — NTFS/ext4 happened to return
    small directories in creation/alphabetical order, which is why callers
    downstream (``_find_similar_content``'s name_a/name_b assignment, and
    hence which file ``decide()`` appends the ``::changelog`` to) worked by
    accident on Windows/Linux; APFS (macOS) does not make that guarantee, so
    the same two files could enumerate in the opposite order there, flipping
    which file received the approved write-back and leaving the file a
    reviewer expected to change looking completely untouched — silently,
    since nothing raises either way.
    """
    index: dict[str, list[_ParsedNode]] = {}
    for og_file in sorted(memory_dir.rglob("*.og"), key=str):
        try:
            text = og_file.read_text(encoding="utf-8", errors="replace")
            mtime = og_file.stat().st_mtime
        except OSError:
            continue

        sections = re.split(r"(?=::node\[)", text)
        for section in sections:
            m_node = re.match(r"::node\[([^\]]+)\]", section)
            if not m_node:
                continue
            name = m_node.group(1).strip()

            m_dense = re.search(r"::dense\n(.+?)(?=\n::|$)", section, re.DOTALL)
            dense = m_dense.group(1).strip()[:300] if m_dense else ""

            m_valid = _VALID_UNTIL_RE.search(section)
            valid_until = m_valid.group(1).strip() if m_valid else ""

            node = _ParsedNode(
                name=name,
                dense=dense,
                file_path=str(og_file),
                mtime=mtime,
                valid_until=valid_until,
            )
            index.setdefault(name, []).append(node)

    return index


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _jaccard(text_a: str, text_b: str) -> float:
    """Word-level Jaccard similarity between two short texts."""
    tok_a = set(re.findall(r"\w+", text_a.lower()))
    tok_b = set(re.findall(r"\w+", text_b.lower()))
    if not tok_a and not tok_b:
        return 1.0
    if not tok_a or not tok_b:
        return 0.0
    return len(tok_a & tok_b) / len(tok_a | tok_b)


def _is_expired(valid_until: str) -> bool:
    """Return True if valid_until is a past ISO-8601 date."""
    if not valid_until:
        return False
    try:
        import datetime

        dt = datetime.datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        return dt < datetime.datetime.now(tz=datetime.UTC)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Changelog writer
# ---------------------------------------------------------------------------


def _append_changelog(file_path: Path, node_name: str, change: str) -> None:
    """Append a ::changelog entry to the file without overwriting any node."""
    if not file_path.exists():
        return
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = f"\n::changelog[{node_name}]\n->[:ts={ts}]\n->[:change={change}]\n"
    with file_path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


# ---------------------------------------------------------------------------
# Gardener
# ---------------------------------------------------------------------------


class MemoryGardener:
    """M3 nightly memory maintenance: deduplicate, supersede, and expire nodes.

    This class is designed to be run as a background job (via the durable queue)
    once per day.  It never overwrites existing node content — all mutations are
    expressed as ``::changelog`` annotations appended to the end of the affected
    file, or as ``supersedes`` edge appended to the newer node's block.

    Args:
        memory_dir: Root of the ``memory/`` data directory.
        similarity_threshold: Jaccard threshold above which two different-named
            nodes are proposed as merge candidates.
    """

    _PENDING_DIFFS_FILE = "pending_diffs.json"

    def __init__(
        self,
        memory_dir: Path,
        similarity_threshold: float = 0.70,
    ) -> None:
        self._memory_dir = memory_dir
        self._threshold = similarity_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, *, auto_apply: bool = True) -> GardeningResult:
        """Run a full gardening pass.

        Returns:
            GardeningResult with auto_applied and pending_review lists.
            Also writes pending_diffs.json to the memory dir for the review surface
            (``GET /memory/diffs`` — see :meth:`decide` for the write-back path).
        """
        index = _parse_dir(self._memory_dir)
        result = GardeningResult(run_ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

        # 1. Detect proposals
        proposals: list[GardeningProposal] = []
        proposals.extend(self._find_dedup_same_name(index))
        proposals.extend(self._find_similar_content(index))
        proposals.extend(self._find_expired(index))

        # 2. Split: whitelisted auto-apply vs. pending review
        for prop in proposals:
            if auto_apply and prop.kind == ProposalKind.DEDUP_SAME_NAME:
                self._apply_dedup(prop)
                prop.auto_applied = True
                result.auto_applied.append(prop)
            else:
                result.pending_review.append(prop)

        # 3. Persist pending diffs for the review surface (GET /memory/diffs)
        diffs_path = self._memory_dir / self._PENDING_DIFFS_FILE
        result.save(diffs_path)

        logger.info(
            "MemoryGardener: auto_applied=%d pending=%d",
            len(result.auto_applied),
            len(result.pending_review),
        )
        return result

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _find_dedup_same_name(self, index: dict[str, list[_ParsedNode]]) -> list[GardeningProposal]:
        """Find nodes whose name appears in more than one file (safe to auto-merge)."""
        proposals = []
        for name, occurrences in index.items():
            if len(occurrences) < 2:
                continue
            # Sort by mtime desc; keep the newest, supersede the rest.
            by_mtime = sorted(occurrences, key=lambda n: -n.mtime)
            newest = by_mtime[0]
            for older in by_mtime[1:]:
                proposals.append(
                    GardeningProposal(
                        kind=ProposalKind.DEDUP_SAME_NAME,
                        name_a=name,
                        reason=(
                            f"Node '{name}' in both {newest.file_path} and "
                            f"{older.file_path}; keeping newest."
                        ),
                        confidence=1.0,
                        file_path_a=newest.file_path,
                        file_path_b=older.file_path,
                    )
                )
        return proposals

    def _find_similar_content(self, index: dict[str, list[_ParsedNode]]) -> list[GardeningProposal]:
        """Propose merges for nodes with different names but similar dense text."""
        proposals = []
        names = list(index.keys())
        for i, name_a in enumerate(names):
            node_a = index[name_a][0]
            if not node_a.dense:
                continue
            for name_b in names[i + 1 :]:
                node_b = index[name_b][0]
                if not node_b.dense:
                    continue
                sim = _jaccard(node_a.dense, node_b.dense)
                if sim >= self._threshold:
                    proposals.append(
                        GardeningProposal(
                            kind=ProposalKind.MERGE_SIMILAR,
                            name_a=name_a,
                            name_b=name_b,
                            reason=f"Jaccard similarity {sim:.2f} ≥ {self._threshold:.2f}",
                            confidence=sim,
                            file_path_a=node_a.file_path,
                            file_path_b=node_b.file_path,
                        )
                    )
        return proposals

    def _find_expired(self, index: dict[str, list[_ParsedNode]]) -> list[GardeningProposal]:
        """Propose deletion of nodes whose valid_until date has passed."""
        proposals = []
        for name, occurrences in index.items():
            for node in occurrences:
                if _is_expired(node.valid_until):
                    proposals.append(
                        GardeningProposal(
                            kind=ProposalKind.EXPIRE_STALE,
                            name_a=name,
                            reason=f"valid_until={node.valid_until} has passed",
                            confidence=1.0,
                            file_path_a=node.file_path,
                        )
                    )
        return proposals

    # ------------------------------------------------------------------
    # Auto-apply (whitelisted only: DEDUP_SAME_NAME)
    # ------------------------------------------------------------------

    def _apply_dedup(self, prop: GardeningProposal) -> None:
        """Append a ::changelog supersedes entry to the older file."""
        try:
            older_path = Path(prop.file_path_b)
            _append_changelog(
                older_path,
                prop.name_a,
                f"superseded_by={prop.file_path_a}",
            )
        except Exception:
            logger.debug("MemoryGardener._apply_dedup failed", exc_info=True)

    # ------------------------------------------------------------------
    # Manual review (T-08 A3 — approve/reject write-back)
    # ------------------------------------------------------------------

    def load_pending(self) -> GardeningResult:
        """Load the last-persisted GardeningResult for this instance's memory_dir."""
        return GardeningResult.load(self._memory_dir / self._PENDING_DIFFS_FILE)

    def decide(self, proposal_id: str, *, approve: bool) -> GardeningProposal:
        """Resolve one ``pending_review`` proposal by a human decision.

        Loads the persisted ``pending_diffs.json``, finds the proposal whose
        stable :attr:`GardeningProposal.id` matches *proposal_id* in the
        ``pending_review`` list, and either:

        - **approve**: applies it via :func:`_apply_reviewed_proposal` — an
          append-only ``::changelog`` annotation that never overwrites existing
          node content (the module governance rule, see module docstring) —
          moves it into ``auto_applied`` (marked ``auto_applied=True`` to record
          that it is now resolved), and re-persists.
        - **reject**: drops it from ``pending_review`` without touching any
          ``.og`` file, and re-persists.

        Both branches re-write ``pending_diffs.json`` so the decision is durable
        and observable by the next ``GET /memory/diffs`` — this is the write-back
        path that did not exist before T-08.

        Args:
            proposal_id: A :attr:`GardeningProposal.id` from a prior
                :meth:`run` (or :meth:`load_pending`) result.
            approve: ``True`` to apply the proposal, ``False`` to discard it.

        Returns:
            The decided :class:`GardeningProposal` (with ``auto_applied=True``
            when approved).

        Raises:
            KeyError: *proposal_id* is not present in the currently persisted
                ``pending_review`` list (already decided, or superseded by a
                newer gardener run).
        """
        result = self.load_pending()
        match = next((p for p in result.pending_review if p.id == proposal_id), None)
        if match is None:
            raise KeyError(proposal_id)

        result.pending_review = [p for p in result.pending_review if p.id != proposal_id]
        if approve:
            _apply_reviewed_proposal(match)
            match.auto_applied = True
            result.auto_applied.append(match)

        result.save(self._memory_dir / self._PENDING_DIFFS_FILE)
        return match


def _apply_reviewed_proposal(prop: GardeningProposal) -> None:
    """Apply a manually-approved proposal via an append-only ``::changelog`` note.

    Mirrors :meth:`MemoryGardener._apply_dedup`'s append-only pattern for the
    proposal kinds that actually reach ``pending_review`` (``MERGE_SIMILAR``,
    ``EXPIRE_STALE``, and the reserved ``SUPERSEDE_CONTRADICTION``), so an
    operator's decision from the Memory Diffs surface (T-08 A3) is durable and
    auditable without ever overwriting existing node content (the module
    governance rule — see module docstring).

    ``_append_changelog`` itself no-ops when the target file no longer exists,
    so this is safe to call even if the underlying ``.og`` file has since been
    removed.
    """
    if prop.kind == ProposalKind.EXPIRE_STALE:
        _append_changelog(Path(prop.file_path_a), prop.name_a, "expired_confirmed=true")
        return
    # MERGE_SIMILAR / SUPERSEDE_CONTRADICTION / DEDUP_SAME_NAME (defensive — the
    # last only ever auto-applies today, but a future change could route it
    # through here too): annotate the second node as resolved in favour of the
    # first, exactly like the auto-apply dedup path does.
    target_path = Path(prop.file_path_b or prop.file_path_a)
    target_name = prop.name_b or prop.name_a
    _append_changelog(
        target_path,
        target_name,
        f"reviewed_supersede_by={prop.name_a}({prop.file_path_a})",
    )
