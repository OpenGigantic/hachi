# UAT Report — Memory Reliability, Privacy, Poisoning, Exfiltration, Cross-Account Isolation

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

> **Commit under test:** `036dfd9` · **Date:** 2026-06-14 · **Agent:** UAT-VERIFY (read-only)
> **Top finding:** UC-MEM-001 / UC-MEM-006: the `core_save_memory` tool has a **daemon-thread fallback** when no job queue is present (`memory_tools.py:119-120`) — the tool returns "queued" even though nothing was durably queued and any write failure inside that thread is completely silent. The `sync_turn` path in `memory.py` is correctly hardened (no thread spawned, loud log), but the LLM-callable tool path is **not**. This is the P0 trust-killer flagged in MR-7.

---

## UC-MEM-001 — PARTIAL (Critical)

**Claim:** "Remember I'm vegetarian" persists to long-term memory and is recalled next session.

**Evidence:**

- `tools/memory_tools.py:119-120` — when `job_queue` is present, `_enqueue_memory_job` enqueues durably (returns `True`); job fails to enqueue → `except Exception: return False` (line 58-59) with no log and no re-raise.
- `tools/memory_tools.py:119-120` (quoted):
  ```python
  if not _enqueue_memory_job("memory_smart_upsert", _smart_upsert, (facts,)):
      threading.Thread(target=_smart_upsert, args=(facts,), daemon=True).start()
  ```
  When `_enqueue_memory_job` returns `False` (no queue configured or enqueue raised), a **daemon thread** is spun. The tool already returned `"{n} fact(s) successfully queued for intelligent long-term consolidation."` before this branch. If the daemon thread fails, the exception is swallowed entirely — the agent already said "got it".
- `memory.py:133-165` — `sync_turn` (auto-extraction path) has been hardened: no thread spawned without a queue, enqueue failure logs at ERROR. This is **NOT** the same code path as the LLM-callable `save_memory` tool.
- Test coverage: `tests/test_memory_loud_failures.py` tests `sync_turn` exclusively; there is **no test** for the `save_memory` tool's silent-thread fallback path.
- `memory.py:210-216` — malformed LLM extraction JSON hits bare `json.loads(text)` (line 216) — no try/except wrapper — so a bad extraction is a job failure (routed to `jobs/failed` when queued), but if the queue is absent the daemon thread exception is discarded.

**Finding:** Two-part failure. (1) When `job_queue` is None, `core_save_memory` falls back to a silent daemon thread, contradicting MR-7 / P1-03. (2) When the job queue is present but the enqueue call raises, `_enqueue_memory_job` silently returns `False` (line 58-59) and the same daemon thread launches — MR-7 was not fully closed in `memory_tools.py`. The `sync_turn` path is correct; the tool path is not. The end-user-facing "I'll remember that" claim is unreliable when the queue is absent or enqueue fails.

**Severity:** Critical (P0 — headline capability silently fails in a common configuration)

**Regression test?** Yes — add a test analogous to `test_no_thread_spawned_when_no_queue` and `test_enqueue_failure_logs_error_not_thread` but targeting `core_save_memory` tool via `register_memory_tools(registry, vault, job_queue=None)`.

---

## UC-MEM-002 — CONFIRMED (Low)

**Claim:** Adding a 26th user profile entry returns a clear `LIMIT_REACHED` message; no silent eviction.

**Evidence:**

- `memory_vault.py:28` — `USER_PROFILE_MAX_ENTRIES = 25`
- `memory_vault.py:362-368`:
  ```python
  existing_id = self._generate_id(content)
  existing = self.user_collection.get(ids=[existing_id])
  is_update = bool(existing and existing.get("ids"))
  if not is_update and self.user_collection.count() >= USER_PROFILE_MAX_ENTRIES:
      return "LIMIT_REACHED"
  ```
  Idempotent re-adds are allowed (checked by content hash first). Only genuinely new entries are blocked.
- `memory_tools.py:166-174` — tool layer converts `"LIMIT_REACHED"` into a human-readable message with stats and a curation hint.
- `tests/test_memory_cli_crud.py::test_memory_add_limit_reached_exits` — passes (verified, exit code 1 with "full"/"limit" in output).

**Finding:** Behavior is as designed. The 26th entry is clearly rejected, existing facts are not evicted, and the user gets actionable guidance to curate. No silent drop.

**Severity:** N/A (CONFIRMED)

**Regression test?** Existing `test_memory_add_limit_reached_exits` covers the CLI path. Recommend adding a direct `MemoryVault.upsert_user_fact` unit test that inserts exactly 25 then a 26th to confirm the Chroma count check is accurate.

---

## UC-MEM-003 — BLOCKED-NEEDS-LIVE (Medium)

**Claim:** Contradicting a fact (e.g., "I moved to Berlin" after "I live in London") results in the newer fact winning or disambiguation.

**Evidence:**

- `memory_tools.py:68-117` — `_smart_upsert` does a semantic search (`top_k=3`), then calls the Anthropic LLM to produce a JSON with `delete_ids` (old fact IDs to remove) and `save_contents` (atomic new facts).
- `memory_tools.py:108-117`:
  ```python
  data = _parse_llm_json(response.content)
  for did in data.get("delete_ids", []):
      if did and did not in ("null", "None", None):
          vault.delete_fact(did)
  for s_content in data.get("save_contents", [content]):
      vault.upsert_fact(s_content, category)
  ```
  The old fact is deleted before the new one is written. If the LLM returns a malformed JSON, `except Exception: vault.upsert_fact(content, category)` at line 116-117 saves the new fact without deleting the old one — both facts remain, creating a contradiction.
- Dedup is content-addressable (`_generate_id` = SHA-256), so semantic variants ("I live in London" vs "User is from London") are treated as distinct entries and **both survive** unless the LLM specifically identifies the old fact's ID.
- This path requires a live LLM call — cannot be tested offline.

**Finding:** The design is sound (LLM-driven consolidation with delete_ids), but (a) malformed LLM JSON causes silent double-entry (contradiction survives), and (b) the fallback thread path (when queue is absent) wraps the entire block in `except Exception: vault.upsert_fact(content, category)` which means even a network error to the LLM silently results in both facts surviving. Quality of the "newer wins" outcome depends entirely on LLM accuracy.

**Severity:** Medium

**Regression test?** Partially — mock the LLM to return malformed JSON and verify that both facts survive (confirming the bug); then mock a valid JSON and verify old fact is deleted.

---

## UC-MEM-004 — PARTIAL (High)

**Claim:** "Forget my home address" removes the fact from ALL backends (Chroma + FTS5 + .og files) and it is not recalled afterward.

**Evidence:**

- `memory_vault.py:300-313` — `delete_fact` deletes from ChromaDB `core_memory` collection. `delete_user_fact` (line 372-385) deletes from `user_collection`.
- **FTS5 / MemoryIndex gap:** `memory_index.py` provides `refresh()` (full re-scan from `.og` files, line 92-151) and `search()` but **no `delete(node_id)` method**. The FTS5 index is only updated by a full refresh scan.
- `memory_vault.py:329-339` — `semantic_search` checks `self._graph_index` and calls `self._graph_index.search(query, top_k=top_k)`. If a fact was indexed into FTS5 but deleted from Chroma, the FTS5 result still appears in the merged RRF output until the next full `refresh()` is called.
- `.og` files: `memory_writer.py` writes facts to `memory/*.og` files. There is no `delete_node()` in `memory_writer.py`. Deleting a fact from Chroma does **not** remove it from the `.og` file on disk.
- `memory_tools.py:139-145` — `delete_memory` only calls `vault.delete_fact(fact_id)`, which only touches Chroma. No FTS5 remove, no `.og` file rewrite.
- SessionStore / event stream: facts written to the transcript (SQLite `sessions.db`) are never purged by `delete_memory` — the original exchange where the fact was stated remains verbatim. This is correctly noted as out-of-scope in the test plan, but the SessionStore is a recall risk: `core_search_sessions` can surface the "forgotten" address from the transcript.

**Finding:** Delete removes from Chroma (confirmed), but leaves the fact queryable via:
1. FTS5 / MemoryIndex until the next full `refresh()` (which happens at boot, not on delete)
2. `.og` files on disk (the M1 write path never deletes nodes)
3. SessionStore transcript (by design, but creates a "forgot but still recalled" UX)

This is a High severity gap for the "privacy-conscious user who wants a fact erased" scenario.

**Severity:** High

**Regression test?** Yes — write a test that inserts via `memory_writer.write_nodes()`, calls `delete_memory`, then calls `semantic_search` and `MemoryIndex.search()` to confirm the fact no longer appears in either.

---

## UC-MEM-005 — CONFIRMED (High)

**Claim / MR-10:** Memory is stored in plaintext by default; the product must not claim otherwise.

**Evidence:**

- `memory_vault.py:64-71` — ChromaDB `PersistentClient` writes to `data_dir/chroma_vault/` with no encryption parameters. ChromaDB has no native at-rest encryption.
- `memory_index.py:73` — SQLite FTS5 index opened with `sqlite3.connect(str(self._db_path))`, no cipher extension.
- `core/session_store.py:50` — `sqlite3.connect(db_path)`, no encryption.
- `docs/strategy/09-execution-plan.md:498` — Decision D-3: "Memory-file at-rest encryption default … Off; opt-in flag." The opt-in is unimplemented (the Fernet path in `secret_store.py` is for credentials only, not the memory vault).
- `docs/strategy/04-memory-redesign.md:239` — "Open question for the user: default off or on?" — explicitly unresolved.
- The `secrets_fallback_file` (`state_dir/secrets.enc`) uses Fernet encryption (`secret_store.py:179-180`) but this only covers API keys/OAuth tokens stored via `hachi secrets`, **not** the Chroma vault or session transcripts.
- No marketing/UI claim of encryption was found in the source tree (a positive finding). The product would be incorrect if it claimed this.

**Finding:** Plaintext at rest is CONFIRMED as the current behavior. The product design docs acknowledge this as a decision (D-3). No UI/product claim of encryption was found. The at-rest exposure surface is: Chroma SQLite files, memory_index.db, sessions.db — all under the OS-native data/state directory (readable by any process running as the same OS user).

**Severity:** High (data-privacy obligation; must be disclosed in product docs and not contradicted anywhere)

**Regression test?** N/A for behavior confirmation. Recommend a documentation test: CI check that no user-facing string in the codebase claims at-rest encryption without the opt-in qualifier.

---

## UC-MEM-006 — PARTIAL (Critical)

**Claim:** Memory backend failure is loud (surfaced in `jobs/failed` + TUI badge); the agent does not claim it remembered when it didn't.

**Evidence:**

**`sync_turn` path (hardened — CONFIRMED):**
- `memory.py:150-165` — if enqueue raises, logs at ERROR; no thread spawned; no silent swallow.
- `memory.py:157-165` — if no queue configured: `logger.warning("Memory sync skipped: no job queue configured…")`. No thread fallback.
- `tests/test_memory_loud_failures.py` — 4 tests all pass: broken vault routes to `jobs/failed`, routes to `RuntimeReadModelStore`, no thread spawned without queue, enqueue failure logs error.

**`core_save_memory` tool path (NOT hardened — DEFECT):**
- `memory_tools.py:41-59` — `_enqueue_memory_job`: `except Exception: return False` with no log and no re-raise.
- `memory_tools.py:119-120` — when `_enqueue_memory_job` returns `False` (either no queue, or enqueue raised): `threading.Thread(target=_smart_upsert, args=(facts,), daemon=True).start()` — daemon thread, completely unmonitored.
- The tool has already returned `"{n} fact(s) successfully queued for intelligent long-term consolidation."` at line 121 before any of this happens (the return is unconditional).
- If the daemon thread's `_smart_upsert` raises (network error, Chroma error), the exception is silently discarded by the thread's default exception handler.
- This means an agent saying "Got it, I've remembered that" may be truthful (job enqueued) or a lie (daemon thread failed silently) depending on the runtime configuration.

**Dead resident worker:**
- The queue worker is a background thread; if it dies, the queue drains only on the fallback path (start of next turn). The tool still enqueues successfully and returns the "queued" message — but if the worker is dead, the job never executes. This is a MR-7 known risk confirmed still present.

**Finding:** `sync_turn` path (auto-extraction) is correctly hardened by commit `036dfd9`. The LLM-callable `core_save_memory` tool retains the silent-failure path. This is the **same defect as UC-MEM-001** but viewed from the reliability angle. The agent's "I'll remember that" claim is not backed by a durable write guarantee from the tool layer.

**Severity:** Critical (trust-breaking; P0)

**Regression test?** Yes — same as UC-MEM-001. The test must call `save_memory` (not `sync_turn`) with a failing or absent queue and verify that either (a) an error is raised/logged or (b) no "queued" message is returned.

---

## ADV-008 — PARTIAL (Medium)

**Claim:** Memory/data exfiltration via a tool is prevented by outbound redaction and permission gate.

**Evidence:**

- `tools/messaging_tools.py:23,44-57` — `redact_outbound` is applied to all outbound messaging tools (Slack, Discord, Telegram, WhatsApp) via `_guard_outbound`. Known secret patterns (Anthropic key, OpenAI key, AWS, GitHub, Stripe, etc.) are redacted before send.
- `core/secret_scanner.py:23-41` — 12 named credential patterns are covered.
- **Gap 1 — memory tools have no outbound redaction.** `core_search_memory` and `core_search_user_profile` return raw Chroma content as strings. If a secret was stored as a memory fact (e.g., "My API key is sk-ant-xxx"), the `core_search_memory` tool returns it unredacted. The agent can then pass this verbatim to a messaging/webhook tool — the redaction only fires at the messaging tool boundary, not at the memory read boundary.
- **Gap 2 — `send_email` / Drive share have no `redact_outbound` call.** `tools/messaging_tools.py` covers messaging channels but not Gmail send or Drive share. A prompt-injection attack that directs the agent to email memory contents would bypass the redaction layer.
- **Gap 3 — tool-result events.** MR-3 (tool-result preview 500→2000 chars, no redaction) is a confirmed exfiltration surface in the event stream/transcript — not a memory-specific issue but relevant here.
- Permission gate (`permissions.py`) restricts which tools are callable at each tier, providing a coarse defense.

**Finding:** Outbound redaction covers messaging channels and is correctly wired. It does not cover: (a) the memory read tools themselves, (b) Gmail send, (c) Drive share. A multi-step exfiltration "search memory → email result" would bypass the guard unless the Gmail send tool separately applies redaction (it does not). This is a medium severity gap given that the most direct single-tool exfiltration paths are covered.

**Severity:** Medium

**Regression test?** Yes — add a test: store a fact containing `sk-ant-test1234567890abc`, call `core_search_memory`, then pass the result to a stub "send_email" and verify the key appears unredacted (documenting the gap).

---

## ADV-019 — PARTIAL (High)

**Claim:** MemoryGuard quarantines threat-scoring content; malformed extraction JSON is handled so poisoning is distinguishable from failure.

**Evidence:**

**MemoryGuard (CONFIRMED working):**
- `memory_guard.py:28-68` — 6 threat signals with weighted scoring; `_THREAT_SCORE_THRESHOLD = 5`.
- `memory_guard.py:102-135` — `scan()` returns `(False, threat_id)` on match; invisible Unicode and control characters are caught directly.
- `memory_vault.py:154-180` — `_guarded_write` calls `guard_scan`; blocked writes go to `quarantine_collection` with metadata (`threat_id`, `status="quarantined"`, `review_state="pending"`).
- `tests/test_memory_guard.py` — 8 tests all pass, including obfuscated math-unicode role hijack.
- `tests/test_memory_vault_quarantine.py` — quarantine metadata verified.
- Guard is invoked on ALL `upsert_fact`, `upsert_user_fact`, `upsert_agent_fact` paths via `_guarded_write`.

**Guard NOT invoked on skill load:**
- `memory_guard.py` is only imported in `memory_vault.py`. Skill `.og` files loaded via `og_engine/engine.py` do not pass through MemoryGuard — as flagged in the test plan.

**Malformed extraction JSON — poisoning vs failure indistinguishable (DEFECT):**
- `memory.py:210-216` (quoted, exact):
  ```python
  text = response.content.strip()
  if text.startswith("```json"):
      text = text.split("```json", 1)[1].split("```", 1)[0].strip()
  elif text.startswith("```"):
      text = text.split("```", 1)[1].split("```", 1)[0].strip()

  data = json.loads(text)
  ```
  `json.loads(text)` is **not wrapped in try/except**. If malformed JSON is returned, this raises `json.JSONDecodeError` which propagates to the job worker as a job failure. The job enters `jobs/failed` — the failure is visible but the log message says `"fact_extraction"` failed, not that the JSON was malformed.
- When this fails, the turn's facts are **not extracted** — but the agent already told the user "I'll remember that" (from `save_memory`). So poisoning (attacker deliberately causes malformed JSON) and legitimate extraction failure produce the same observable result: silent fact loss + a `jobs/failed` badge. They are not distinguishable from the TUI.
- `_parse_llm_json` helper in `memory_tools.py:28-35` has the same bare `json.loads(text)` with no try/except.

**Finding:** MemoryGuard correctly blocks known threat patterns and quarantines them with metadata (CONFIRMED). The gap is: (1) guard does not cover skill-load, and (2) a sophisticated poisoner who crafts input that passes MemoryGuard but causes the downstream LLM extraction to produce malformed JSON achieves silent fact loss indistinguishable from a benign extraction failure. A diagnostic improvement would be to catch `json.JSONDecodeError` separately and tag it as `extraction_parse_error` in the job failure payload.

**Severity:** High (the pattern-based guard is strong; the indistinguishability gap is a secondary risk)

**Regression test?** Yes — mock `AnthropicClient.generate` to return `"not valid json"` in `_run_fact_extraction`, confirm the job fails, and assert the failure payload contains a parseable error indicator.

---

## ADV-023 — CONFIRMED (High)

**Claim:** Memory is isolated per Google account (or session); no cross-account exposure.

**Evidence:**

- `memory_vault.py:55-95` — `MemoryVault.__init__` uses a single `brain_dir` defaulting to `get_hachi_paths().data_dir` (`paths.py:117`).
- `paths.py:113-121` — `get_hachi_paths()` uses `PlatformDirs(appname="hachi", appauthor=False)` — this resolves to a **single OS-user-scoped directory** (e.g., `%LOCALAPPDATA%\hachi\hachi` on Windows).
- There is **no account-scoping at any level**: no `account_id`, no `google_id`, no workspace namespace in the Chroma collection names (`core_memory`, `user_profile`, `agent_profile`, `delegation_log`), no per-account subdirectory.
- ChromaDB `PersistentClient` opens the same `chroma_vault/` path regardless of which Google account the OAuth token belongs to.
- Similarly `session_store.py:44` — single SQLite at `state_dir/sessions.db` — no per-account table or row scoping.
- If the user switches Google accounts via re-auth (account A → account B), all memory facts written as account A are immediately visible to account B's sessions. There is no migration, no isolation, no warning.
- This is a **single-user product** — the design explicitly targets one human/one machine. The Hachi strategy docs do not describe a multi-account architecture. The cross-account bleed risk is real if the user legitimately manages multiple Google accounts through the same Hachi installation.
- The test plan account B isolation scenario (`§2.2`) cannot pass because the namespace concept does not exist.

**Finding:** Memory is scoped to the OS user account (single-user), not to the Google account. Any Google account connected to Hachi on the same OS user shares all memory. This is by design for a single-user product but is a security disclosure item: connecting a work Google account and a personal Google account through the same Hachi installation results in full memory cross-contamination. The product must document this explicitly.

**Severity:** High (disclosure required; mismatch with user mental model of account-specific memory)

**Regression test?** N/A (this is an architectural design choice, not a code bug). Recommend a documentation check and an onboarding warning when a second Google account is connected.

---

## Summary Table

| Scenario | Verdict | Severity | Key finding |
|---|---|---|---|
| UC-MEM-001 | PARTIAL | Critical | `core_save_memory` tool has silent daemon-thread fallback when queue absent/enqueue fails; `sync_turn` path correctly hardened but tool path is not |
| UC-MEM-002 | CONFIRMED | — | 26th profile entry correctly blocked with LIMIT_REACHED and curation hint; idempotent re-adds allowed |
| UC-MEM-003 | BLOCKED-NEEDS-LIVE | Medium | LLM-driven consolidation design is sound; malformed LLM JSON causes both facts to survive; needs live test |
| UC-MEM-004 | PARTIAL | High | Delete removes from Chroma only; FTS5 index retains entry until next refresh; .og files never purged; SessionStore not touched |
| UC-MEM-005 | CONFIRMED | High | Plaintext at rest confirmed (Chroma, SQLite, FTS5); no encryption code in memory path; D-3 correctly recorded as Off/opt-in unimplemented |
| UC-MEM-006 | PARTIAL | Critical | `sync_turn` hardened (P1-03 met for auto-extraction); `core_save_memory` tool retains silent-thread path — same root cause as UC-MEM-001 |
| ADV-008 | PARTIAL | Medium | Outbound redaction covers messaging tools; does NOT cover memory read tools, Gmail send, or Drive share; multi-step exfil bypasses guard |
| ADV-019 | PARTIAL | High | MemoryGuard CONFIRMED working (8 tests pass, quarantine correct); malformed LLM extraction JSON causes poisoning indistinguishable from failure |
| ADV-023 | CONFIRMED | High | Memory is OS-user scoped, not Google-account scoped; connecting two Google accounts yields full memory cross-contamination |

### Critical path for next sprint

1. **UC-MEM-001 / UC-MEM-006 (Critical):** Fix `_enqueue_memory_job` to log/raise on failure (mirror `sync_turn` pattern); remove the daemon-thread fallback from `save_memory`; add regression tests.
2. **UC-MEM-004 (High):** Implement `delete_node()` in `MemoryIndex` / `MemoryWriter` so delete purges FTS5 and `.og` files; add a test.
3. **ADV-023 (High):** Document single-user/single-OS-account scope; add onboarding warning if a second Google account is detected; evaluate per-account namespacing as a future requirement.
4. **UC-MEM-005 (High):** Ensure no product string claims at-rest encryption; surface the plaintext-by-default posture in the privacy notice.
