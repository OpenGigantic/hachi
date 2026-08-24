"""UsageTracker — persistent per-skill usage counters (T-73).

Extracted from ``SkillEngine``: owns the on-disk usage JSON database, the
write lock, and the flush debounce — the state that used to force
``SkillEngine.__init__`` to resolve a Hachi home directory and read a file
from disk before a single ``.og`` file had been parsed.

``SkillEngine`` still owns which skill nodes exist and which are visible to a
given agent role (that is its whole job); this collaborator owns only the
persisted ``{name_or_qid: {use_count, first_used, last_used}}`` counters and
the mechanics of reading/writing them. Node existence/visibility-aware
queries (``get_stale_skills`` / ``get_usage_report``) still need to join
against the engine's node index, so :meth:`UsageTracker.stale` and
:meth:`UsageTracker.report` take that join information as arguments rather
than reaching back into the engine — this collaborator has no reference to
a ``SkillEngine`` at all.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hachi.core.atomic_io import atomic_write_json, quarantine_corrupt_file

logger = logging.getLogger(__name__)


class UsageTracker:
    """Owns the persistent skill-usage JSON database, its lock, and its flush debounce.

    Constructing a tracker immediately attempts to load ``usage_path`` (matching
    ``SkillEngine``'s pre-T-73 behaviour of reading the usage file eagerly at
    construction) — BB-51: a corrupt file is quarantined, not silently clobbered
    by the next save.
    """

    def __init__(self, usage_path: Path) -> None:
        self.usage_path = usage_path
        self.entries: dict[str, dict[str, Any]] = self.load()
        # Usage-write debounce: skip the disk write when a flush already happened
        # within flush_interval seconds. The first call after boot (or after the
        # interval expires) always flushes so tests and one-shot runs see the file.
        self.last_flush: float = 0.0  # monotonic time of last successful save()
        self.flush_interval: float = 30.0  # seconds between successive writes
        self.lock: threading.Lock = threading.Lock()

    def load(self) -> dict[str, dict[str, Any]]:
        """Load the usage database from disk. Returns empty dict on error."""
        try:
            if self.usage_path.exists():
                return json.loads(self.usage_path.read_text(encoding="utf-8"))
        except Exception:
            # BB-51: quarantine the unparseable file BEFORE returning the empty
            # default, so the next save() can't silently clobber it.
            logger.warning("Failed to load skill usage DB, starting fresh")
            quarantine_corrupt_file(self.usage_path)
        return {}

    def save(self) -> None:
        """Persist the usage database to disk and reset the debounce timer."""
        with self.lock:
            snapshot = dict(self.entries)
            self.last_flush = time.monotonic()
        try:
            atomic_write_json(self.usage_path, snapshot)
        except Exception:
            logger.warning("Failed to persist skill usage DB")

    def record(self, name: str) -> None:
        """Record a skill load event. Called by the execution loop on load_skill.

        Updates use_count, last_used, and first_used timestamps. Writes are
        debounced: the JSON file is flushed at most once per ``flush_interval``
        seconds. The first call after boot (or after the interval expires)
        always writes immediately so one-shot invocations and tests observe the
        file on disk. Call :meth:`flush` to force an immediate sync regardless
        of the timer (e.g. at process shutdown).

        Args:
            name: The exact skill node name that was loaded.
        """
        now = datetime.now(UTC).isoformat()
        with self.lock:
            entry = self.entries.setdefault(
                name,
                {
                    "use_count": 0,
                    "first_used": now,
                    "last_used": now,
                },
            )
            entry["use_count"] += 1
            entry["last_used"] = now
            elapsed = time.monotonic() - self.last_flush
            should_flush = elapsed >= self.flush_interval
        if should_flush:
            self.save()

    def flush(self) -> None:
        """Force an immediate flush of pending usage writes to disk.

        Call at process shutdown (or in tests) to ensure no writes are lost.
        """
        self.save()

    def stale(self, stale_days: int, known: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        """Return staleness rows for the given ``(qualified_id, display_name)`` pairs.

        A skill is "stale" if it has a usage record but ``last_used`` is older
        than ``stale_days`` ago. Skills with no usage record are "never used".

        Args:
            stale_days: Number of days of inactivity before a skill is stale.
            known: ``(qualified_id, display_name)`` for every node the caller
                currently considers visible/loaded — node existence and
                visibility are the caller's (``SkillEngine``'s) concern; this
                only reports what the persisted counters say about each one.

        Returns:
            List of dicts with name, last_used, use_count, days_inactive, status.
        """
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        stale: list[dict[str, Any]] = []

        for qid, name in known:
            # Usage tracking keyed by bare name for backward compat with persisted JSON.
            entry = self.entries.get(name) or self.entries.get(qid)
            if not entry:
                stale.append(
                    {
                        "name": name,
                        "last_used": None,
                        "use_count": 0,
                        "days_inactive": None,
                        "status": "never_used",
                    }
                )
                continue

            try:
                last = datetime.fromisoformat(entry["last_used"])
                if last < cutoff:
                    days = (datetime.now(UTC) - last).days
                    stale.append(
                        {
                            "name": name,
                            "last_used": entry["last_used"],
                            "use_count": entry["use_count"],
                            "days_inactive": days,
                            "status": "stale",
                        }
                    )
            except (ValueError, KeyError):
                continue

        return stale

    def report(
        self,
        *,
        total_skills: int,
        resolve_visible_name: Callable[[str], str | None],
        known: Iterable[tuple[str, str]],
    ) -> dict[str, Any]:
        """Assemble the usage-analytics report.

        Args:
            total_skills: The caller's current visible node count
                (``SkillEngine.node_count``).
            resolve_visible_name: Given a raw usage-dict key, return the current
                display name if it resolves to a visible loaded node, else
                ``None``. Lets tracked entries for skills that were renamed or
                unloaded since their last use fall out of the report exactly as
                before.
            known: Same shape as :meth:`stale`'s ``known`` — used for the
                embedded stale-skill counts (always at the default 30-day
                threshold, matching the pre-T-73 ``get_usage_report``).

        Returns:
            A dict with total_skills, tracked_skills, top_skills (sorted by
            use_count), stale_count, and never_used_count.
        """
        tracked = []
        for name, entry in self.entries.items():
            display_name = resolve_visible_name(name)
            if display_name is not None:
                tracked.append(
                    {
                        "name": display_name,  # always bare name for display
                        "use_count": entry.get("use_count", 0),
                        "first_used": entry.get("first_used"),
                        "last_used": entry.get("last_used"),
                    }
                )

        tracked.sort(key=lambda x: x["use_count"], reverse=True)

        stale_rows = self.stale(30, known)
        never_used = [s for s in stale_rows if s["status"] == "never_used"]

        return {
            "total_skills": total_skills,
            "tracked_skills": len(tracked),
            "never_used_count": len(never_used),
            "stale_count": len([s for s in stale_rows if s["status"] == "stale"]),
            "top_skills": tracked[:10],
        }
