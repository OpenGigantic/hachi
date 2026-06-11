# Notion Integration — QA Report

Date: 2026-06-11
Environment: Windows, `./.venv/Scripts/python.exe` (Python 3.14.3), `PYTHONPATH=src`, tier=`unrestricted`.
Live credential: vault keyring `notion_api_key` (prefix `ntn_295…`), integration bot **"toko"**, workspace "Aditya Dhanraj's Notion".
Shared test page: `f9491b4e91444c3094a8503bc9b446cd` (object=`page`, title "toko"). Only one object is shared with the integration; no database is shared.

Scope files:
- `src/tokoboros/integrations/notion/service.py`
- `src/tokoboros/integrations/notion/pages.py`
- `src/tokoboros/integrations/notion/databases.py`
- `src/tokoboros/gateway/orchestrator.py` (capability wiring / tool registration)

## Summary

`NOTION_VERSION = "2025-09-03"` is a **real, accepted Notion API version** — `GET /v1/users/me` returns **200** with it (live-verified), so it is NOT an invalid_version. The version pin does not break auth, search, read, create, append, or archive.

HOWEVER, `2025-09-03` removed the `POST /v1/databases/{id}/query` endpoint (replaced by `POST /v1/data_sources/{id}/query`), which **completely breaks `notion_query_database`** — verified live against a real shared database (400 `invalid_request_url` on every call). This is the headline issue.

Secondary issues: tool error strings discard the Notion JSON error body (`code`/`message`) so the agent loses actionable detail; `NotionService.__init__` raises instead of resolving lazily when the key is missing (the documented "lazy per-call" path and its friendly error message are dead code); and read/search/query results are not paginated (silent truncation beyond the first page).

Severity counts: **Critical 1, High 1, Medium 1, Low 2, Nit 1.**

## Findings

### [Critical] `notion_query_database` is broken under the pinned `NOTION_VERSION = 2025-09-03` (endpoint removed)
- **Where:** `src/tokoboros/integrations/notion/databases.py:53` (`self._post(f"/databases/{database_id}/query")`), pinned by `src/tokoboros/integrations/notion/service.py:18`.
- **Status:** live-verified.
- **Repro (live):** created a real database under the test page (id `83b8bf40-73f3-43dd-9b36-6d82dc294dbb`, data_source id `0e279a8e-5aba-4ee5-b236-80e93cbc98a4`), shared via the parent page, then called the tool:
  - `NotionDatabases().query_database("83b8bf40-73f3-43dd-9b36-6d82dc294dbb")`
    → `"Failed to query database: Client error '400 Bad Request' for url 'https://api.notion.com/v1/databases/83b8bf40-73f3-43dd-9b36-6d82dc294dbb/query' …"`
  - Raw `POST /v1/databases/{id}/query` with `Notion-Version: 2025-09-03`:
    `{'object':'error','status':400,'code':'invalid_request_url','message':'Invalid request URL.','request_id':'a73bbb39-…'}`
  - Same call with `Notion-Version: 2022-06-28`: **200** `{'object':'list','results':[],…}`
  - The NEW endpoint `POST /v1/data_sources/{ds_id}/query` with `2025-09-03`: **200** `{'object':'list','results':[],'type':'page_or_data_source',…}`
- **Expected:** `notion_query_database` returns the database rows.
- **Actual:** Always returns a 400 failure string; the tool is non-functional under the pinned version. It would only work if the version were rolled back to a pre-`2025-09-03` value.
- **Impact:** One of the four advertised Notion tools is 100% dead. The agent cannot list any database contents. The 3 default Notion skills (`notion/weekly_review.og`, `project_kickoff.og`, `notion_brain_dump.og`) that rely on querying databases will fail.
- **Suggested fix:** Either (a) pin `NOTION_VERSION` to a stable supported value such as `"2022-06-28"` (matches the version used during setup ping, and `databases/{id}/query` works there), OR (b) migrate `query_database` to the `2025-09-03` data-source model: resolve the database's `data_sources[].id` via `GET /v1/databases/{id}` (the new response includes a `data_sources` array) and call `POST /v1/data_sources/{data_source_id}/query`. Note the response container key also changed `page_or_database` → `page_or_data_source`.

### [High] Tool error messages discard the Notion JSON error body (`code`/`message`), only surfacing the generic httpx status summary
- **Where:** `databases.py:46,78`; `pages.py:34,237,251,263` (every `except Exception as e: return f"… {str(e)}"`), because `service.py:49,55,61` call `response.raise_for_status()`, whose exception text is only the HTTP status line + an MDN link.
- **Status:** live-verified.
- **Repro (live):** `read_page_blocks("00000000000000000000000000000000")` returns:
  `"Failed to read Notion page: Client error '404 Not Found' for url '…/blocks/…/children' For more information check: https://developer.mozilla.org/…/404"`
  But the actual Notion response body (captured directly) is far more useful:
  `{'object':'error','status':404,'code':'object_not_found','message':'Could not find block with ID: … . Make sure the relevant pages and databases are shared with your integration "toko".','additional_data':{'integration_id':'…'}}`
  Likewise the `2022-06-28` "is a page, not a database. Use the retrieve page API instead" hint is thrown away.
- **Expected:** The returned error string should include Notion's `code` and `message` (e.g. "object_not_found: Could not find block… Make sure it is shared with your integration").
- **Actual:** The LLM only sees "Client error '404 Not Found'… check MDN", losing the actionable "share the page with your integration" guidance — which is the single most common Notion failure mode.
- **Impact:** Agent cannot self-correct (e.g. it can't tell "not shared" from "wrong id" from "is a page not a database"). Degrades every error path of every Notion tool.
- **Suggested fix:** In the service `_get/_post/_patch`, catch `httpx.HTTPStatusError` and re-raise/return with `exc.response.json().get("message")`/`code` when the body is JSON, e.g. `f"{code}: {message}"`. Or have each tool's `except` block inspect `e.response`.

### [Medium] `NotionService.__init__` raises when the key is missing instead of resolving lazily; `_ensure_api_key` fallback + friendly message are dead code
- **Where:** `service.py:25` (`self.api_key = self._vault.get_token("notion_api_key", prompt_if_missing=False)`) vs. `core/credential_vault.py:37-38`.
- **Status:** live-verified (via injected empty secret store).
- **Repro:** Construct `NotionService()` with a vault whose store returns `None` for `notion_api_key`:
  → `ValueError: Credential not found. Set it via tokoboros secrets set notion_api_key` (raised from `__init__`).
- **Expected:** Per the inline comment ("Avoid interactive stdin prompts during TUI boot. We resolve lazily per call.") and `_ensure_api_key`, construction should succeed with `api_key=None` and only fail on first real API call with the message `"Missing Notion API key. Add 'notion_api_key' to ~/.tokoboros/vault.json."`.
- **Actual:** `get_token(prompt_if_missing=False)` does NOT return `None` on a missing key — it **raises** (vault contract changed). So:
  1. `__init__` throws immediately;
  2. the `if not self.api_key: return` guard and the friendly `~/.tokoboros/vault.json` error in `_ensure_api_key` (`service.py:28-34`) are unreachable.
- **Impact:** In the orchestrated path this is non-fatal — `orchestrator.py:1443` wraps `instantiate()` in try/except and falls back to a setup stub — so no crash was observed. But the lazy-resolution design is defeated, the user-facing remediation message is wrong/unreachable, and any code constructing `NotionService` directly (outside that try/except) would crash at construction instead of at call time.
- **Suggested fix:** Either wrap the constructor call in try/except returning `None`, or call `self._vault.get_token(..., prompt_if_missing=False)` through a helper that swallows the missing-credential `ValueError`. Align the `_ensure_api_key` message with the real remediation (`tokoboros secrets set notion_api_key`).

### [Low] No pagination: `read_page_blocks`, `query_database`, and `search_workspace` silently truncate
- **Where:** `pages.py:209` (`_get(.../children)` — ignores `has_more`/`next_cursor`); `databases.py:53,60` (caps to `results[:20]`, no cursor); `databases.py:18,25` (caps to `results[:5]`).
- **Status:** code-reading (confirmed shapes live; not exercised at >100 blocks).
- **Repro:** A page with >100 child blocks, a database with >20 rows, or a workspace with >5 search hits.
- **Expected:** Either paginate or explicitly note truncation.
- **Actual:** Notion returns max 100 results/page; the code reads only the first page and (for query/search) further slices without telling the caller more exist. The agent silently believes it saw everything.
- **Impact:** Incomplete reads on non-trivial pages/databases; agent may act on a partial view.
- **Suggested fix:** Loop on `has_more`/`next_cursor`, or append a "(showing first N; more available)" note when `has_more` is true.

### [Low] `search_workspace` / `query_database` assume `item["object"]` is non-null and title parsing is best-effort
- **Where:** `databases.py:42` (`item_type.upper()` — `AttributeError` if `object` is `None`).
- **Status:** code-reading (live search only ever returned `object="page"`, so not triggered here).
- **Repro:** A search result lacking an `object` field would raise inside the loop; caught by the outer `except` and reported as a generic "Failed to search" string.
- **Expected:** Defensive handling of missing `object`.
- **Actual:** Would surface as an opaque failure rather than skipping/labelling the row.
- **Impact:** Low — Notion reliably sets `object`; mainly a robustness gap.
- **Suggested fix:** `(item.get("object") or "object").upper()`.

### [Nit] `2025-09-03` search returns `data_source` objects whose ids will not work with the (broken) `query_database`
- **Where:** interaction of `databases.py:search_workspace` and `query_database`.
- **Status:** code-reading / live-context.
- **Detail:** Under `2025-09-03`, search and the database-retrieve responses expose `data_source` ids and a `data_sources[]` array. Even after fixing the Critical issue, the agent must be careful to pass a **data_source_id** (not the legacy database_id) to the new query endpoint. The tool descriptions still say "UUID of the Notion database", which will mislead the model once migrated.
- **Suggested fix:** When migrating to `2025-09-03`, update the `notion_query_database` schema/description to talk about data sources, and have `search`/retrieve surface the `data_source_id`.

## What passed

- **NotionService construction via CredentialVault** — resolves the vault key (`ntn_295…`); `headers` emits `Authorization: Bearer …` and `Notion-Version: 2025-09-03`. (live-verified)
- **`NOTION_VERSION = 2025-09-03` is accepted by the live API** — `GET /v1/users/me` → **200** (bot "toko"). It is a valid version, not `invalid_version`. (live-verified) The breakage is endpoint-specific (databases/query), not a blanket version rejection.
- **`notion_search_workspace`** — `query=""` returns the shared page `[PAGE] toko / ID: f9491b4e-9144-4c30-94a8-503bc9b446cd`. (live-verified) Only that one page is shared; no database surfaced — so the database-query path had to be tested with a database I created (see Critical).
- **`notion_read_page`** — works with BOTH the undashed (`f9491b4e91444c3094a8503bc9b446cd`) and dashed (`f9491b4e-9144-4c30-94a8-503bc9b446cd`) id forms; returns identical content (Notion normalizes ids). Neither form fails. (live-verified)
- **`notion_create_page`** — created child page "Tokoboros QA <ts>" under the test page (parent_type=`page`); returned the page URL. Also verified `parent_type="database"` into a real DB still works under `2025-09-03` (Notion auto-resolves the single data source from `database_id`). (live-verified)
- **`notion_append_to_page`** — appended a paragraph; read-back confirmed the new block. (live-verified)
- **`notion_archive_page`** — archived the created page successfully. (live-verified)
- **Error handling is graceful (no crashes)** — non-existent id → 404, malformed id → 400, page-id-as-database → 400; all caught and returned as strings (see High re: lossy detail). (live-verified)
- **Cleanup complete** — archived the QA child page, the temp database (`83b8bf40-…`), and its 3 rows; post-cleanup workspace search shows only the original "toko" page and the test page has 0 child_page/child_database blocks. (live-verified)
