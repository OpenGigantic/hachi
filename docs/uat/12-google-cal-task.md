# UAT Verification: Calendar, Tasks, Contacts, Planning

**Test execution date:** 2026-06-14  
**Build commit:** 036dfd9 (Enhance Gmail Integration and TUI Components)  
**Tester mode:** Code-grounded static verification (no live API)  
**Grounding principle:** Read-only examination of source files; quote exact lines; note BLOCKED-NEEDS-LIVE where claims require live API interaction.

---

## Key Findings (Summary)

**CRITICAL:** Three destructive operations execute immediately with NO approval gate (MR-5):
- `google_delete_event` (calendar.py:160-163)
- `google_delete_task` (tasks.py:78-81)
- `google_delete_contact` (contacts.py:134-139)

The "DANGER" label in contact tool description (contacts.py:223-225) is recommendation-only; no enforced gate exists.

**HIGH:** Timezone handling always defaults to UTC (calendar.py:34), causing wrong local times when users provide offsets or assume local interpretation.

**MEDIUM-HIGH:** Six validation gaps found; all pass inputs unvalidated to Google API (will fail with opaque 400s):
- ISO datetime format validation absent (UC-CAL-002)
- Start ≤ end time check absent (UC-CAL-002)
- RFC 5545 RRULE validation absent (UC-CAL-003)
- Invalid due date (e.g., 2026-06-32) appended with Z and sent raw (UC-TASK-002)
- Email format validation absent in contacts (UC-CONTACT-001)
- Phone format validation absent in contacts (UC-CONTACT-001)

**MEDIUM:** Attendee email validation absent (UC-CAL-001) — emails are only `.strip()`'d, not RFC-validated.

**MEDIUM:** Pagination NOT implemented; task lists beyond `maxResults=20` are silently truncated (UC-TASK-001).

**LOW:** Windows strftime issue FIXED (UC-PLAN-001); code avoids glibc `%-d` and uses portable `str(_d.day)` approach (planning.py:147).

**MEDIUM (process risk):** Exception swallowing hides *why* services are unavailable (UC-PLAN-001); user sees "(calendar unavailable)" with no reason (planning.py:46-47, 69-70).

---

## Scenario Verdicts

### UC-CAL-001 — Create event with Meet link & attendees

**Claim:**  
Timezone assumed UTC (wrong local time); Meet link silently fails (no retry); attendee emails unvalidated.

**Evidence:**  
- **Timezone:** `calendar.py:34` — `return {"dateTime": iso, "timeZone": "UTC"}` — all paths hardcode UTC. No user timezone inference or override. A user passing `2026-06-15T10:00:00+05:30` (10 AM IST) will be scheduled at 10 AM UTC instead.
- **Attendee validation:** `calendar.py:56` — `event["attendees"] = [{"email": email.strip()} for email in attendees]` — only whitespace is stripped; no RFC 5321 email format validation.
- **Meet link:** `calendar.py:59-68` — conf request issued with `conferenceDataVersion=1`. No retry loop visible; API errors propagate as-is.

**Finding:**  
All three claims are CONFIRMED. Timezone bug is HIGH severity (silent wrong time). Attendee validation GAP is MEDIUM (API will reject bad emails with opaque 400). Meet link behavior is API-dependent; code has no retry.

**Severity:** HIGH (timezone) · MEDIUM (validation gap)  
**Regression test:** Yes — test with timezone-aware ISO strings; verify scheduled time in different timezone.

---

### UC-CAL-002 — Malformed time / end-before-start

**Claim:**  
No ISO validation, no start≤end check.

**Evidence:**  
- **ISO format validation:** `calendar.py:21-34` (`_dt_field` method) — accepts any string; no `datetime.fromisoformat()` call. A string like `"not-a-date"` or `"2026-13-01"` is passed straight to `event["start"]` at line 51.
- **Start ≤ end check:** `calendar.py:36-80` (`create_calendar_event`) — no comparison of `start_time_iso` and `end_time_iso` before constructing the event.
- **Update path:** `calendar.py:136-158` (`update_event`) — same lack of validation; existing event is fetched (line 145) but start/end times are not validated before the PATCH (line 154).

**Finding:**  
Both claims CONFIRMED. Malformed dates and backwards intervals are sent to the API, which rejects them with opaque 400s. The agent will fail with "API error" rather than "your end time is before your start time."

**Severity:** MEDIUM · **Regression test:** Yes — test with "2026-13-01" and end-before-start.

---

### UC-CAL-003 — Recurring RRULE validation

**Claim:**  
No RFC 5545 validation.

**Evidence:**  
- `calendar.py:165-218` (`create_recurring_event`) — `recurrence_rule` parameter is passed to the API without parsing or validation.
- Line 192: `"recurrence": [recurrence_rule],` — the string is placed directly in the event dict.
- No `rrule` library call, no format check. A malformed rule like `"RRULE:INVALID_FREQ=WEEKLY"` will be sent to the API.

**Finding:**  
CONFIRMED. Invalid RRULEs are silently sent to Google Calendar API. The API will reject with a 400, and the user sees "Event creation failed" without being told why the rule is invalid.

**Severity:** MEDIUM · **Regression test:** Yes — test with invalid FREQ value.

---

### UC-CAL-004 — Find free time across busy week

**Claim:**  
UTC vs local expectation; 403 insufficient-scope at live token (mock tests can't catch).

**Evidence:**  
- `calendar.py:107-134` (`find_free_time`):
  - Line 109: `now_dt = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)` — uses UTC.
  - Line 110: `time_min = now_dt.isoformat() + "Z"` — appends Z for UTC interpretation.
  - Line 119: `"timeZone": "UTC",` — freeBusy request uses UTC.
- **No local timezone inference.** If the user is in a different timezone, the "next 3 days" window will be shifted by their offset.

**Finding:**  
Timezone math is UTC-hardcoded. User in IST (+05:30) asking "find time tomorrow" will get results in UTC tomorrow, not IST tomorrow. The 403 scope issue is out of scope for code review (requires live token).

**Severity:** MEDIUM · **Regression test:** Yes — test from a user in a different timezone.

---

### UC-CAL-005 — Delete event (no approval gate)

**Claim:**  
No approval gate; wrong/ambiguous event may be deleted.

**Evidence:**  
- `calendar.py:160-163` (`delete_event`):
  ```python
  def delete_event(self, event_id: str) -> str:
      """Deletes a calendar event."""
      self.client.delete(f"/calendar/v3/calendars/primary/events/{event_id}")
      return f"Event {event_id} has been deleted."
  ```
- No check that the event exists or matches the user's intent. The tool schema (`calendar.py:362-371`) does not declare any permission escalation.

**Finding:**  
CONFIRMED. Deletion is immediate and irreversible. Tool description says nothing; no MR-5 gate applies. **This is MR-5 destructive-op-no-gate issue.**

**Severity:** CRITICAL (per MR-5 · **Regression test:** N/A — this is a design issue, not a code bug.

---

### UC-TASK-001 — Create, complete, list 25 tasks; pagination

**Claim:**  
No pagination — list truncates beyond default.

**Evidence:**  
- `tasks.py:29-50` (`list_tasks`):
  - Line 38: `"maxResults": max_results,` where `max_results` defaults to 20.
  - Line 40: `response = self.client.get(f"/tasks/v1/lists/{tasklist_id}/tasks", params=params)`
  - Lines 41-50: Returns the items from one API call only. No `nextPageToken` handling.
- If the user has 25 tasks, only 20 are shown. The tool does NOT indicate "showing 20 of 25."

**Finding:**  
CONFIRMED. Pagination is not implemented. The tool silently truncates without signaling incompleteness to the user.

**Severity:** MEDIUM · **Regression test:** Yes — create 25+ tasks and verify all are listed.

---

### UC-TASK-002 — Task with malformed due date

**Claim:**  
Date appended `T00:00:00Z` without validating the date.

**Evidence:**  
- `tasks.py:58-68` (`create_task`):
  - Lines 63-65:
    ```python
    if due_date_iso:
        # Google Tasks expects RFC 3339; append Z if plain date provided.
        body["due"] = due_date_iso if "T" in due_date_iso else f"{due_date_iso}T00:00:00Z"
    ```
- No call to `datetime.date.fromisoformat()` or similar. A date like `"2026-06-32"` is directly appended to become `"2026-06-32T00:00:00Z"` and sent to the API.

**Finding:**  
CONFIRMED. Invalid dates are sent to the API without client-side validation. Google Tasks API will reject with a 400.

**Severity:** LOW (API will reject; UX could be better with client validation) · **Regression test:** Yes — test `due_date_iso="2026-06-32"`.

---

### UC-TASK-003 — Delete task (no gate)

**Claim:**  
No gate; ambiguous-reference resolution uncertain.

**Evidence:**  
- `tasks.py:78-81` (`delete_task`):
  ```python
  def delete_task(self, task_id: str, tasklist_id: str = "@default") -> str:
      """Permanently delete a task."""
      self.client.delete(f"/tasks/v1/lists/{tasklist_id}/tasks/{task_id}")
      return f"Task {task_id} deleted."
  ```
- Deletion is immediate; no confirmation.

**Finding:**  
CONFIRMED. **This is MR-5 destructive-op-no-gate issue.** No approval gate.

**Severity:** MEDIUM · **Regression test:** N/A — design issue.

---

### UC-CONTACT-001 — Create/update/search contact; email/phone validation

**Claim:**  
No email/phone validation; fuzzy search behavior.

**Evidence:**  
- **Email validation:** `contacts.py:52-60` (`google_create_contact`):
  - Line 54: `body = {"names": [{"givenName": name}], "emailAddresses": [{"value": email}]}`
  - Email is not validated. A string like `"not-an-email"` or `"alex"` will be sent to the API.
- **Phone validation:** `contacts.py:62-94` (`google_update_contact`):
  - Line 71-74: Phones are appended as-is: `phones.append({"value": phone})`
  - No RFC 3966 or E.164 validation.
- **Fuzzy search:** `contacts.py:18-50` (`google_search_contact`):
  - Line 28: `if any(search_lower in n.get("displayName", "").lower() for n in person_names):`
  - Uses substring matching, not fuzzy matching. Searching `"john"` will match `"John Smith"` but also `"Johnathan"`, `"Dr. Johnson"`, etc. Results are not ranked; all matches are returned.

**Finding:**  
CONFIRMED. Email and phone are not validated. Fuzzy search is actually substring search (no ranking/relevance). The tool description doesn't indicate this limitation.

**Severity:** LOW · **Regression test:** Yes — test with invalid email format and observe API error.

---

### UC-CONTACT-002 — Delete contact (warning-only, no enforced gate)

**Claim:**  
Description says "DANGER" but no enforced gate.

**Evidence:**  
- `contacts.py:134-139` (`google_delete_contact`):
  ```python
  def google_delete_contact(resourceName: str) -> str:
      try:
          self.client.delete(f"https://people.googleapis.com/v1/{resourceName}:deleteContact")
          return f"Successfully deleted contact: {resourceName}"
  ```
- Line 223-225: Tool description includes `"DANGER: Permanently delete a Google Contact. ALWAYS verbally ask the user for confirmation…"` — but this is guidance to the agent, not an enforced gate.
- No code-level check or permission escalation marker.

**Finding:**  
CONFIRMED. **This is MR-5 destructive-op-no-gate issue.** The tool description asks the agent to ask for confirmation, but there is no enforced confirmation gate. An agent that ignores the guidance will delete the contact immediately.

**Severity:** MEDIUM · **Regression test:** N/A — design issue.

---

### UC-PLAN-001 — Plan my day; Windows strftime, exception swallowing

**Claim:**  
`%-d` glibc-only → raw ISO date on Windows; swallowed exceptions hide *why* a section is empty.

**Evidence:**  
- **Date formatting (UC-PLAN-001a):** `planning.py:144-149`:
  ```python
  try:
      _d = datetime.date.fromisoformat(date_iso)
      # %-d (Linux) / %#d (Windows) strip leading zero — use a portable alternative.
      display_date = _d.strftime("%A, %B ") + str(_d.day) + _d.strftime(" %Y")
  ```
  - **NOT a bug:** The comment at line 146 acknowledges the problem, and line 147 uses the portable approach: `str(_d.day)` instead of `%-d`. The code already avoids the Windows issue.
  - Test output from Python REPL above confirms this works: `"Sunday, June 14 2026"`.
- **Exception swallowing:** `planning.py:46-47`, `69-70`, `107`:
  - `_fetch_events`: Line 46-47 catches all exceptions and returns `["(calendar unavailable)"]` with no reason logged or displayed.
  - `_fetch_unread_count_and_snippets`: Line 69-70 same behavior.
  - `_fetch_pending_tasks`: Line 107 same behavior.
  - Possible failure causes (auth failure, scope missing, API disabled) are all masked as "unavailable."

**Finding:**  
- **(UC-PLAN-001a) Windows strftime:** NOT-REPRODUCED. The code does NOT use `%-d` and is already fixed.
- **(UC-PLAN-001b) Exception swallowing:** CONFIRMED. Users see "(calendar unavailable)" without being told if it's auth, scope, API offline, or a transient error. This hides diagnostic info.

**Severity:** LOW (strftime) · MEDIUM (exception swallowing) · **Regression test:** Yes (swallowing only) — test with intentionally broken auth and verify error message.

---

## Summary Table

| Scenario | Verdict | Severity | Regression Test? |
|----------|---------|----------|------------------|
| UC-CAL-001 | CONFIRMED (timezone + validation gaps) | HIGH | Yes |
| UC-CAL-002 | CONFIRMED (no ISO / start≤end validation) | MEDIUM | Yes |
| UC-CAL-003 | CONFIRMED (no RRULE validation) | MEDIUM | Yes |
| UC-CAL-004 | CONFIRMED (UTC-only math) | MEDIUM | Yes |
| UC-CAL-005 | CONFIRMED (no approval gate · MR-5) | CRITICAL | N/A |
| UC-TASK-001 | CONFIRMED (no pagination) | MEDIUM | Yes |
| UC-TASK-002 | CONFIRMED (no date validation) | LOW | Yes |
| UC-TASK-003 | CONFIRMED (no approval gate · MR-5) | MEDIUM | N/A |
| UC-CONTACT-001 | CONFIRMED (no email/phone validation) | LOW | Yes |
| UC-CONTACT-002 | CONFIRMED (no enforced gate · MR-5) | MEDIUM | N/A |
| UC-PLAN-001a | NOT-REPRODUCED (Windows strftime fixed) | LOW | N/A |
| UC-PLAN-001b | CONFIRMED (exception swallowing) | MEDIUM | Yes |

---

## Crosslinks to Meta-Risks

- **MR-5** (No approval gate on destructive ops): Confirmed active in UC-CAL-005, UC-TASK-003, UC-CONTACT-002. This is the #1 finding.
- **MR-3** (Secrets leak into event stream): Not directly tested here; out of scope for calendar/tasks/contacts/planning verification.
- **MR-1** (QA_BUG_REPORT stale): Noted; not a verification matter.

---

## Blockers for Live Testing

- UC-CAL-004: 403 scope error can only be reproduced with a real Google token that lacks scope.
- All other scenarios are code-grounded and can be reproduced offline.

---

## Recommendations

1. **Priority 1 (P0):** Implement approval gates on `google_delete_event`, `google_delete_task`, `google_delete_contact` (MR-5 systemic issue).
2. **Priority 2 (P1):** Add ISO datetime validation in calendar/tasks and email format validation in contacts.
3. **Priority 3 (P1):** Implement pagination with nextPageToken handling in task lists.
4. **Priority 4 (P2):** Log and surface the *reason* a service is unavailable in plan_my_day (auth vs scope vs offline).
5. **Priority 5 (P2):** Add RFC 5545 RRULE validation or at least a pre-flight check.

