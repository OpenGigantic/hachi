# Google Workspace Integration — QA Report

Tester: QA engineer (live + code-reading). Account: **opsingh861@gmail.com**.
Cloud project: `art-gallery-404021` (project number 418537082803), OAuth desktop client.
Tier: `unrestricted`; capability `google` configured=true; real `GoogleIntegrationHub` (44 tools) mounted.

## Summary

| Service | Status | Notes |
|---|---|---|
| Auth / registration | ✅ PASS (live) | Real hub mounts (44 tools), not the stub. Token auto-refresh verified live. |
| Gmail | ✅ PASS (live) | Profile, labels, list (read-only), draft create→delete all worked. No send performed. |
| Calendar | ⚠️ PARTIAL (live) | create/list/update/delete/get_events_for_date work. **`find_free_time` 403s**, **`create_recurring_event` 400s** — both real bugs. |
| Tasks | ✅ PASS (live) | create/list/complete/delete full cycle works. |
| Planning (`plan_my_day`) | ✅ PASS (live) | Fuses calendar+gmail+tasks correctly. Pretty-date header degrades on Windows (Low). |
| Meet | ⛔ ENV-LIMITED | Space probe 403 (no Workspace/Meet space). Returns "paid tier" message. Fire-and-forget thread (guardrail violation). |
| Drive | ⛔ ENV-LIMITED | **Drive API disabled** in the Cloud project — cannot live-test create/trash. Code-read only. |
| Docs | ⛔ ENV-LIMITED | **Docs API disabled** in the Cloud project — cannot live-test. Code-read only. |
| Sheets | ⛔ ENV-LIMITED | **Sheets API disabled** in the Cloud project — cannot live-test. Code-read only. |
| Contacts | ⛔ ENV-LIMITED | **People API disabled** in the Cloud project — cannot live-test. Code-read only. |
| Mocked tests | ✅ 74 passed | `test_google_integrations / _tasks_planning / _tools_wire / _auth_cmd / test_oauth`. |

**Severity tally:** Critical 0 · High 1 · Medium 2 · Low 3 · Nit 1.

**Cleanup confirmation:** All created artifacts removed and verified.
- Gmail draft `r4144609947281894455` → deleted (confirmed 404 on re-fetch).
- Calendar test events (QA test event, naive, recur PROOF) → all deleted (status `cancelled` / HTTP 204).
- Tasks (QA test task, QA verify task) → deleted (gone from list, `deleted:true`).
- No Drive/Docs/Sheets/Contacts artifacts were created (APIs disabled), so nothing to clean there.
- **No email was ever sent.** Only a draft was created and then deleted.

**Caveats:** Drive, Docs, Sheets, and People (Contacts) APIs are **not enabled** in Cloud project 418537082803, so their create→verify→cleanup flows could not be exercised live. Meet requires a real Workspace Meet space. Those are environment limitations, not code defects — but they also mean the bugs below are the ones reachable with the enabled APIs (Gmail, Calendar, Tasks); analogous error-handling bugs in the disabled services are code-reading only.

---

## Findings

### [High] Non-403 Google error bodies are swallowed — agent/user sees a generic httpx + MDN-link string instead of Google's actionable message
- **Where:** `src/tokoboros/integrations/google/client.py:79-85` (`_request_with_retry`) — only `403` is routed to `_raise_forbidden`; every other status (`400`, `401`, `404`, `409`, `429`-exhausted, `5xx`) falls through to `response.raise_for_status()`, which raises `httpx.HTTPStatusError` whose message is `"Client error 'NNN ...' for url ... For more information check: https://developer.mozilla.org/...". The structured Google `error.message` in the JSON body is discarded.
- **Repro (live):**
  ```bash
  set -a; source .env; set +a; export PYTHONPATH=src
  ./.venv/Scripts/python.exe -X utf8 -c "
  from tokoboros.integrations.google import GoogleIntegrationHub
  from tokoboros.integrations.google.calendar import CalendarService
  import datetime
  hub=GoogleIntegrationHub(); cal=CalendarService(client=hub.client)
  tom=(datetime.date.today()+datetime.timedelta(days=1)).isoformat()
  # invalid recurring payload -> Google returns a precise 400 body
  cal.create_recurring_event('x',f'{tom}T10:00:00Z',f'{tom}T10:30:00Z','RRULE:FREQ=NONSENSE')
  "
  ```
- **Expected:** Surface Google's body, e.g. `Missing time zone definition for start time.` / `Not Found` / `Invalid attendee email`, so the LLM can self-correct.
- **Actual (live):**
  - Google's real 400 body: `{"error":{... "message":"Missing time zone definition for start time."}}`
  - Tokoboros surfaces: `HTTPStatusError: Client error '400 Bad Request' for url 'https://www.googleapis.com/calendar/v3/calendars/primary/events' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400`
  - Confirmed identically for a 404 (`delete_event('NONEXISTENT404')` → MDN-link string even though Google said `"message":"Not Found"`), and for `read_doc`/`read_sheet` which wrap it as `Failed to read document: ... developer.mozilla.org/.../404`.
- **Impact:** Across **all** services. The OAuth flow (`oauth.py::_exchange_token`) and the 403 path both deliberately parse the body and add hints — but the dominant day-to-day errors (bad input → 400, missing entity → 404) lose all signal. The agent cannot recover from its own malformed calls and the user gets a w3/MDN link instead of "Invalid value", "Not Found", etc.
- **Suggested fix:** Generalize `_raise_forbidden` into a `_raise_for_status_with_body(url, response)` that, for any `response.is_error`, parses `response.json()["error"]["message"]` (fallback to `response.text`) and raises with that text + status; keep the 403 "API not enabled" hint as a special case. Call it instead of the bare `response.raise_for_status()` on lines 81 and 84.

### [Medium] `google_find_free_time` always 403s — `/calendar/v3/freeBusy` requires a broader scope than the declared `calendar.events`
- **Where:** `src/tokoboros/integrations/google/calendar.py:98-125` (`find_free_time`, POST `/calendar/v3/freeBusy`); declared scope at `calendar.py:13-14` is only `https://www.googleapis.com/auth/calendar.events`.
- **Repro (live):**
  ```bash
  set -a; source .env; set +a; export PYTHONPATH=src
  ./.venv/Scripts/python.exe -X utf8 -c "
  from tokoboros.integrations.google import GoogleIntegrationHub
  from tokoboros.integrations.google.calendar import CalendarService
  print(CalendarService(client=GoogleIntegrationHub().client).find_free_time(30,2))"
  ```
- **Expected:** A free/busy summary (the tool is registered and advertised to the LLM).
- **Actual (live):** `PermissionError: Google API returned 403 Forbidden for /calendar/v3/freeBusy: Request had insufficient authentication scopes.` The granted token *does* contain `calendar.events` (verified), but the FreeBusy endpoint needs `calendar` or `calendar.readonly`. Event list/create/update/delete (which use `/events`) all work fine with `calendar.events`.
- **Impact:** `google_find_free_time` and any "when am I free / schedule around my calendar" flow is 100% broken. `google_plan_my_day` is unaffected because it reads `/events`, not `/freeBusy`.
- **Suggested fix:** Add `https://www.googleapis.com/auth/calendar.readonly` (or switch the service scope to `calendar`) to `CalendarService.required_scopes`, OR reimplement `find_free_time` on top of the `/events` list (which works with `calendar.events`) by computing gaps client-side.

### [Medium] `google_create_recurring_event` always 400s — recurring events require a named `timeZone`, but `_dt_field` omits it whenever the time has an offset or `Z`
- **Where:** `src/tokoboros/integrations/google/calendar.py:16-25` (`_dt_field`) used by `create_recurring_event` (`calendar.py:181-183`). `_dt_field` returns `{"dateTime": iso}` (no `timeZone`) when the string contains `+` or ends with `Z`; it only adds `timeZone:"UTC"` for naive strings.
- **Repro (live):**
  ```bash
  set -a; source .env; set +a; export PYTHONPATH=src
  ./.venv/Scripts/python.exe -X utf8 -c "
  from tokoboros.integrations.google import GoogleIntegrationHub
  from tokoboros.integrations.google.calendar import CalendarService
  import datetime
  cal=CalendarService(client=GoogleIntegrationHub().client)
  d=(datetime.date.today()+datetime.timedelta(days=2)).isoformat()
  print(cal.create_recurring_event('r',f'{d}T09:00:00+00:00',f'{d}T09:30:00+00:00','RRULE:FREQ=DAILY;COUNT=2'))"
  ```
- **Expected:** Recurring event created (the tool description's own examples use offset times like `2026-06-09T10:00:00+05:30`).
- **Actual (live):** 400 for BOTH `+00:00`-offset and `Z`-suffixed times. **Root cause proven live:** posting the identical payload with an explicit `"timeZone":"UTC"` field in start/end returns **200** (then cleaned up via HTTP 204). Single (non-recurring) events are unaffected because Google does not require a named zone for them.
- **Impact:** Every `google_create_recurring_event` call fails for the documented/expected input format. Standups, weekly reviews, etc. cannot be created. Compounded by the High finding above, the agent only sees a generic 400 + MDN link, so it cannot diagnose or retry.
- **Suggested fix:** For recurring events, always include a named `timeZone` in the start/end objects (e.g. derive from the offset or default `UTC`), or have `_dt_field` accept a `recurring=True` flag that forces a `timeZone` field even when an offset/`Z` is present.

### [Low] `plan_my_day` pretty-date header silently degrades to raw ISO on Windows (`%-d` is glibc-only)
- **Where:** `src/tokoboros/integrations/google/planning.py:145-148` — `strftime("%A, %B %-d %Y")`.
- **Repro (live, Windows):** `python -c "import datetime; datetime.date(2026,6,11).strftime('%A, %B %-d %Y')"` → `ValueError: Invalid format string`.
- **Expected:** Header `# Daily Plan — Thursday, June 11 2026`.
- **Actual (live):** The `ValueError` is caught and the header falls back to `# Daily Plan — 2026-06-11` (confirmed via live `plan_my_day()` run). Functionality otherwise fine; purely the formatted date is lost on Windows.
- **Impact:** Cosmetic; Windows-only. The rest of `plan_my_day` works (verified live: calendar/inbox/tasks all populated, "and 196 more unread" shown correctly).
- **Suggested fix:** Use a portable format — `%#d` on Windows or, cross-platform, `str(d.day)` interpolated: `d.strftime("%A, %B ") + str(d.day) + d.strftime(" %Y")`.

### [Low] Inconsistent error contract — some tools catch-and-return a string, siblings raise to the caller
- **Where:** Drive `search_drive` / `create_folder` / `move_file` (`drive.py:14-41,75-87`) have **no** try/except and propagate `PermissionError`/`HTTPStatusError`; Drive `upload_file` / `share_file` / `trash_file` catch and return `"Failed to ...: {e}"`. Same split exists in Calendar (`create_calendar_event`, `update_event`, `delete_event`, `get_events_for_date` raise) vs Gmail/Docs/Sheets/Contacts (mostly catch).
- **Repro (live):** `DriveService(...).search_drive("name contains 'x'")` raises `PermissionError` (Drive API disabled here), whereas `DriveService(...).trash_file('x')` would return a string.
- **Expected:** One consistent contract (preferably: let exceptions propagate to the loop's tool runner, which formats them) so tool results are uniform for the LLM.
- **Actual:** Mixed — some failures arrive as tool-result strings, others as raised exceptions caught further up. Hard for the agent to reason about uniformly.
- **Impact:** Inconsistent agent UX and brittle prompt handling. Low because both paths "work", just differently.
- **Suggested fix:** Pick one strategy. Given the centralized error message proposed in the High finding, prefer removing the per-method try/except and letting the client's exception carry the Google message.

### [Nit] `_raise_forbidden` builds a blank API name for absolute-URL services ("the  API is not enabled")
- **Where:** `src/tokoboros/integrations/google/client.py:103` — `api_name = url.split("/")[1]`.
- **Repro (live):** Docs/Sheets/Contacts/Meet pass **absolute** URLs (`https://docs.googleapis.com/...`); `"https://...".split("/")` → `['https:','','docs.googleapis.com',...]`, so index `[1]` is `''`. The 403 hint reads `— the  API is not enabled ...` (double space, empty name). Relative-path services (Gmail/Calendar/Drive/Tasks via `/drive/...`) correctly yield `drive`, `gmail`, etc.
- **Expected:** `— the docs API is not enabled ...`.
- **Actual (live):** Verified for Docs/Sheets/Contacts: `the  API is not enabled` (blank).
- **Impact:** Cosmetic; the URL itself is still in the message so the user can tell which API.
- **Suggested fix:** Parse the host: e.g. `urlsplit(url).hostname.split(".")[0]` when absolute, else `url.strip("/").split("/")[0]`.

### [Informational — environment, not a code bug] Disabled APIs and Meet tier
- Drive, Docs, Sheets, and People (Contacts) APIs are **disabled** in Cloud project 418537082803; every call returns the (well-formed) "API has not been used in project … or it is disabled. Enable it by visiting …" 403. The `_raise_forbidden` "API not enabled" hint correctly fires here — good. These services therefore could not be live-exercised for create→verify→cleanup.
- Google Meet space probe returns `403 PERMISSION_DENIED "Permission denied on resource Space (or it might not exist)"`. `google_monitor_meeting` maps any 403/401 to *"requires a paid Google Workspace tier"*, which is plausible but slightly misleading for a not-found space. (See also the thread note below.)

---

## What passed (per service)

- **Auth / registration (live):** With `GOOGLE_CLIENT_ID` set, `register_google_tools` returns `True`, mounts **44** `google_*` tools, and the `google_not_configured` stub is absent. Cached keyring token (`oauth.google`) silently refreshed (`[oauth] Refreshing Google token silently...`) and authenticated a live Gmail profile call. Granted scopes match the hub's requested union (no spurious re-auth).
- **Gmail (live, gmail.modify):** `GET /users/me/profile` → `opsingh861@gmail.com`, 111,884 messages. `list_labels` returned system + custom labels. `list_recent_emails(2)` returned real From/Subject/Message-ID/Thread-ID (read-only). `create_draft("Tokoboros QA draft")` → draft `r4144609947281894455`, verified via `GET /drafts/{id}`, then **deleted** via `DELETE /drafts/{id}` and confirmed gone (404). **No message was sent.**
- **Calendar (live, calendar.events):** `create_calendar_event` (tomorrow 15:00–15:30 UTC) → created; `get_events_for_date` found it; `update_event` renamed it (verified summary changed); `delete_event` removed it (`status: cancelled`). Single-event creation with a naive (offset-less) time also works (the `_dt_field` `UTC` fallback). `get_upcoming_events` returned real events.
- **Tasks (live, tasks):** `list_task_lists` → "My Tasks". `create_task` → task created with notes + due date; appeared in `list_tasks`; `complete_task` flipped `needsAction`→`completed` (timestamp set); `delete_task` removed it (`deleted:true`, absent from default list).
- **Planning (live):** `plan_my_day()` fused Calendar (no events today), unread inbox (5 snippets + "and 196 more unread"), and 10 pending tasks into one markdown block.
- **Meet (live, reachable):** Tool returns cleanly for an invalid space (no crash); the Meet API is reachable (403 on probe as expected without a Workspace space).
- **Mocked unit tests:** `74 passed` across `test_google_integrations.py`, `test_google_tasks_planning.py`, `test_google_tools_wire.py`, `test_google_auth_cmd.py`, `test_oauth.py`.

### Additional code-reading observations (lower confidence, not separately ranked)
- **Pagination/truncation:** `search_drive` reports `"Found {len(files)} files"` where `len(files)` is capped at `pageSize` (default 5) with no `nextPageToken` follow — the count is "returned", not "total". `list_contacts`/`search_contact` hard-cap at `pageSize` (1000 / arg) with no paging, so accounts with more contacts silently truncate. `plan_my_day` *does* handle the "+N more" case for email correctly.
- **Meet daemon = fire-and-forget thread:** `meet.py:168` `threading.Thread(target=_poll_for_transcript, daemon=True).start()` polls Google for up to ~30 min and writes to `MemoryVault`. This violates the project guardrail "Prefer queued background work (`jobs/queue.py`) over fire-and-forget threads"; on process exit the daemon is killed mid-poll and prints to stdout (`You: `) assuming a REPL.
- **Drive `parent_id: str = None`** (and Contacts `phone`/`email: str = None`) default `None` under a non-Optional type hint — mypy-unclean, harmless at runtime.
