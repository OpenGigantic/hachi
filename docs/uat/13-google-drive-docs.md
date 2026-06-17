# UAT Verification: Google Drive, Docs, Sheets, Meet

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

## Hachi commit 036dfd9

**Verification Date:** 2026-06-14  
**Verifier:** Claude Code UAT Agent (read-only code inspection)  
**Scope:** 5 scenarios (UC-DRIVE-001, UC-DRIVE-002, UC-DRIVE-003, UC-DOCS-001, UC-DOCS-002, UC-SHEET-001, UC-MEET-001)  

---

## Summary of Findings

**Critical (2):** UC-MEET-001 (daemon thread + stdout printing + silent timeout + error swallowing); UC-DRIVE-002 (no email validation + no approval gate)  
**High (4):** UC-DRIVE-001 (no size limit), UC-DOCS-001 (fragile end-index math), UC-DOCS-002 (no pagination), UC-SHEET-001 (no range validation + no pagination)  
**Medium (1):** UC-DRIVE-003 (error handling is actually consistent, not a defect in current code)  

**Top-line:** The Google integrations lack input validation, pagination guards, and approval gates. UC-MEET-001 is a reliability nightmare (daemon thread with stdout print("You: ") corruption, silent 30-min timeout, error swallowing).

---

## Scenario Details

### UC-DRIVE-001 — Upload file (no size limit)

**Verdict:** CONFIRMED (defect present)  
**Severity:** High  
**Regression test:** Yes  

**Claim:** No size limit, no path/symlink validation; memory blowup on large multipart upload.

**Evidence (file:line):**  
`src/hachi/integrations/google/drive.py:57-87` (`upload_file` method)

**Code quote:**
```python
def upload_file(self, file_path: str, parent_id: str = None) -> str:
    """Uploads a local file to Google Drive."""
    import mimetypes
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        return f"File not found: {file_path}"

    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "application/octet-stream"

    metadata = {"name": path.name}
    if parent_id:
        metadata["parents"] = [parent_id]

    try:
        # Note: We must use the /upload/drive/v3/files endpoint for multipart uploads
        response = self.client.upload_multipart(
            "/upload/drive/v3/files",
            metadata=metadata,
            file_path=file_path,
            mime_type=mime_type,
            params={"uploadType": "multipart", "fields": "id, name, webViewLink"},
        )
        file_id = response.get("id")
        link = response.get("webViewLink", "N/A")
        return f"File '{path.name}' uploaded successfully. ID: {file_id} [Link: {link}]"
    except Exception as e:
        return f"Failed to upload file: {str(e)}"
```

**Finding:**  
- Line 62-64: Only checks `path.exists()`, does NOT check file size.
- Line 76-82: Calls `self.client.upload_multipart()` with no `maxFileSize` parameter or cap.
- Symlinks are accepted (pathlib.Path.exists() returns True for symlinks without resolving them).
- No permission gate at this level; gate would be at the `PermissionGate` wrapper.
- A 10 GB multipart upload will attempt to load the entire file into memory for the multipart request.

**Regression test:** Create a 5 GB file and attempt upload; observe memory usage and timeout behavior. Verify size cap is implemented before committing fix.

---

### UC-DRIVE-002 — Share file (no email validation + no approval gate)

**Verdict:** CONFIRMED (defect present)  
**Severity:** Critical (P0 per plan)  
**Regression test:** Yes  

**Claim:** No approval gate + no email validation → accidental external share.

**Evidence (file:line):**  
`src/hachi/integrations/google/drive.py:103-110` (`share_file` method)

**Code quote:**
```python
def share_file(self, file_id: str, email: str, role: str = "writer") -> str:
    """Shares a file with a specific user."""
    try:
        permission = {"type": "user", "role": role, "emailAddress": email}
        self.client.post(f"/drive/v3/files/{file_id}/permissions", json_data=permission)
        return f"Successfully shared file {file_id} with {email} as {role}."
    except Exception as e:
        return f"Failed to share file: {str(e)}"
```

**Finding:**  
- Line 106: Constructs permission dict directly with user-provided `email` with no validation.
- No RFC 5321 email validation (e.g., rejects `alex@`, `alex example.com`, `@example.com`).
- Line 107: Posts to the Drive API synchronously, **immediately**, with no confirmation loop or human-in-the-loop gate.
- The `role` enum is enforced in the tool schema (["reader", "commenter", "writer"]), but the `email` parameter is unconstrained.
- Per the UAT plan (MR-5), there is no action-bound approval gate; the TUI interactive gate (`tui/app.py:2200`) is the only approver, and it is TUI-only (API/channel sessions bypass it).

**Regression test:** Attempt to share with malformed emails (`alex@`, `@example.com`, etc.); verify rejection before API call. Verify a mock approval gate is wired before GA.

---

### UC-DRIVE-003 — Move/trash file (error handling)

**Verdict:** NOT-REPRODUCED (control is present, consistent in current code)  
**Severity:** N/A  
**Regression test:** No  

**Claim:** Inconsistent error contract (some raise, some return strings).

**Evidence (file:line):**  
`src/hachi/integrations/google/drive.py:89-121` (`move_file` and `trash_file` methods)

**Code quote:**
```python
def move_file(self, file_id: str, new_parent_id: str) -> str:
    """Moves a file to a new folder."""
    try:
        file_meta = self.client.get(f"/drive/v3/files/{file_id}", params={"fields": "parents"})
        previous_parents = ",".join(file_meta.get("parents", []))
        self.client.patch(
            f"/drive/v3/files/{file_id}",
            params={"addParents": new_parent_id, "removeParents": previous_parents},
            json_data={},
        )
        return f"File {file_id} moved to folder {new_parent_id}."
    except Exception as e:
        return f"Failed to move file: {str(e)}"

def trash_file(self, file_id: str) -> str:
    """Safely moves a file to the trash instead of permanently deleting it."""
    try:
        self.client.patch(f"/drive/v3/files/{file_id}", json_data={"trashed": True})
        return (
            f"Successfully moved file {file_id} to the trash. It will be permanently "
            "deleted in 30 days."
        )
    except Exception as e:
        return f"Failed to trash file: {str(e)}"
```

**Finding:**  
Both methods use the same error-handling pattern:
- Line 100 (`move_file`): `except Exception as e: return f"Failed to move file: {str(e)}"`
- Line 120 (`trash_file`): `except Exception as e: return f"Failed to trash file: {str(e)}"`

Both return error strings; neither raises an exception. The error contract is **consistent**, not inconsistent. The UAT plan's concern (LOW-17..21) about inconsistency is not validated by the current code.

**Regression test:** None required; error handling is uniform.

---

### UC-DOCS-001 — Create and append to Doc (fragile end-index calc)

**Verdict:** CONFIRMED (defect present)  
**Severity:** High  
**Regression test:** Yes  

**Claim:** Fragile end-index calc on empty/complex docs → text inserted in wrong place.

**Evidence (file:line):**  
`src/hachi/integrations/google/docs.py:47-66` (`append_doc_text` method)

**Code quote:**
```python
def append_doc_text(self, doc_id: str, text: str) -> str:
    """Appends text to the end of a Google Document."""
    try:
        # Get the current document to find the end index
        doc = self.client.get(f"https://docs.googleapis.com/v1/documents/{doc_id}")
        content = doc.get("body", {}).get("content", [])

        # The last element's endIndex is the end of the document
        end_index = 1
        if content:
            end_index = content[-1].get("endIndex", 2) - 1

        requests = [{"insertText": {"location": {"index": end_index}, "text": "\n" + text}}]
        self.client.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            json_data={"requests": requests},
        )
        return f"Successfully appended text to document {doc_id}."
    except Exception as e:
        return f"Failed to append to document: {str(e)}"
```

**Finding:**  
- Line 55-57 (the end-index calculation):
  ```python
  end_index = 1
  if content:
      end_index = content[-1].get("endIndex", 2) - 1
  ```
- **Edge case 1 (empty doc):** If `content` is empty, `end_index = 1`, which is correct (insert at position 1, after the initial document start).
- **Edge case 2 (document with one character):** If the last element has `endIndex = 2`, the calculation gives `2 - 1 = 1`, which would insert at position 1 (before/at the same position as the character, causing duplication or insertion in the wrong place).
- **Edge case 3 (complex docs with multiple paragraphs):** The Docs API structure has nested elements. The last content element's `endIndex` is not necessarily the end of the document body (there may be trailing structural elements). Subtracting 1 is unreliable.
- The Google Docs API expects `insertText.location.index` to be the position **before which** to insert. The formula `content[-1].get("endIndex", 2) - 1` is fragile and off-by-one prone.

**Regression test:** Create an empty doc and append; append to a doc with a single character; append to a multi-paragraph doc. Verify text is appended at the actual end, not duplicated or misplaced.

---

### UC-DOCS-002 — Read very large doc (no pagination)

**Verdict:** CONFIRMED (defect present)  
**Severity:** High  
**Regression test:** Yes  

**Claim:** No pagination; silent truncation / memory pressure.

**Evidence (file:line):**  
`src/hachi/integrations/google/docs.py:30-45` (`read_doc` method)

**Code quote:**
```python
def read_doc(self, doc_id: str) -> str:
    """Reads text content from a Google Document."""
    try:
        doc = self.client.get(f"https://docs.googleapis.com/v1/documents/{doc_id}")
        content = doc.get("body", {}).get("content", [])
        text_chunks = []

        for element in content:
            if "paragraph" in element:
                for element_item in element["paragraph"].get("elements", []):
                    if "textRun" in element_item:
                        text_chunks.append(element_item["textRun"]["content"])

        return "".join(text_chunks)
    except Exception as e:
        return f"Failed to read document: {str(e)}"
```

**Finding:**  
- Line 33: Calls `self.client.get()` to fetch the entire document at once (one API call, no pagination).
- Line 34: Extracts the full `content` array, which contains all structural elements.
- Line 37-41: Iterates through **all** content elements with no row/page limit, collecting all text.
- Line 43: Returns the concatenated text as a single string with no size indication.
- A 100-page Google Doc (e.g., 50k+ characters) is loaded entirely into memory. If a Doc is larger than the LLM's context window or causes memory pressure, there is **no truncation notice** and **no pagination** to fetch it in chunks.
- The function silently succeeds and returns a potentially massive string, which may OOM the agent or exceed the LLM's input limit.

**Regression test:** Create a 100-page Google Doc and call `read_doc`. Measure memory usage and verify either (a) a size cap is enforced, (b) pagination chunks the read, or (c) a truncation/overflow notice is returned.

---

### UC-SHEET-001 — Read & append Sheet (no range validation + no pagination)

**Verdict:** CONFIRMED (defect present)  
**Severity:** High  
**Regression test:** Yes  

**Claim:** No range-syntax validation, no pagination on large read.

**Evidence (file:line):**  
`src/hachi/integrations/google/sheets.py:37-53` (`read_sheet` method)

**Code quote:**
```python
def read_sheet(self, spreadsheet_id: str, range_name: str) -> str:
    """Reads data from a Google Spreadsheet."""
    try:
        response = self.client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}"
        )
        values = response.get("values", [])
        if not values:
            return "No data found in the specified range."

        # Format as CSV for the LLM to read easily
        rows = []
        for row in values:
            rows.append(", ".join([str(cell) for cell in row]))
        return "\n".join(rows)
    except Exception as e:
        return f"Failed to read spreadsheet: {str(e)}"
```

**Finding:**  
- Line 40-42: The `range_name` parameter is passed directly into the URL with **no validation**. Malformed ranges such as `Sheet1!A:Z`, `Sheet1!A1:`, `ZZZ`, `!invalid:`, and other syntactically invalid ranges are sent to the API.
- Line 43: All results are retrieved in a single API call (no pagination via `pageToken` or batching).
- Line 49-51: All rows are formatted as CSV with no row limit. A 10k+ row sheet is loaded entirely into a single string.
- If the sheet is very large (e.g., 100k rows), the string may exceed memory or LLM context limits, with no warning or chunking.
- The function silently succeeds and returns potentially oversized data.

**Range validation gaps:**
- No check for well-formed `A1` notation (e.g., `Sheet1!A1:B10`).
- No check for invalid characters (e.g., `!@#$`).
- No check for logical validity (e.g., `A10:A5` where end is before start).

**Regression test:** Call `read_sheet` with malformed ranges (`Sheet1!`, `A:`, `ZZZ`, etc.) and verify they are rejected before API call. Create a 10k-row sheet and call `read_sheet` with the full range; verify either (a) pagination is implemented, (b) a size cap is enforced, or (c) a truncation notice is returned.

---

### UC-MEET-001 — Monitor meeting (daemon thread, stdout, silent timeout, error swallowing)

**Verdict:** CONFIRMED (all defects present)  
**Severity:** Critical (P1 per plan)  
**Regression test:** Yes (required before GA per plan)  

**Claim:** Fire-and-forget daemon thread killed on exit; `print("You: ")` to stdout assuming a REPL; silent 30-min timeout; transcript absent if not enabled; errors swallowed.

**Evidence (file:line):**  
`src/hachi/integrations/google/meet.py:25-229` (full `google_monitor_meeting` function and daemon deployment)

---

**DEFECT 1: Daemon thread is fire-and-forget with daemon=True**

Code quote (lines 182-184):
```python
_worker = JobWorker(_q)
threading.Thread(
    target=_worker.execute_one, daemon=True, name=f"meet:{space_id}"
).start()
```

**Finding:**
- `daemon=True` means the thread is a daemon thread and will be **killed immediately when the main process exits**, regardless of completion state.
- The thread is not stored in a list or registry, so there is no way to:
  - Join it (wait for completion)
  - Signal it to stop cleanly
  - Monitor its status
  - Recover if it crashes
- If the agent exits while the transcript is being fetched, the thread is terminated and the transcript is lost.
- This is classified as "fire-and-forget" — the tool returns immediately (line 185-190) while the daemon runs unsupervised.

---

**DEFECT 2: Stdout print("You: ") corrupts non-interactive output**

Code quote (lines 163-165):
```python
print("  [MeetBot Daemon] 🧠 Transcript securely saved to Memory Vault.")
print(
    "  [MeetBot Daemon] (You can now ask Hachi to summarize"
    " or email the notes.)\nYou: ",
    end="",
    flush=True,
)
```

**Finding:**
- The daemon thread calls `print("You: ", end="", flush=True)` (line 163-165) directly to stdout.
- This assumes the agent is running in a **REPL context** where the user is typing at the prompt.
- If the agent is running in a **TUI** (Textual UI) or **API gateway** (non-interactive), this print statement will corrupt the output or interfere with the UI rendering.
- The `"You: "` prompt is inappropriate outside of a REPL and will confuse users or break the TUI display.
- Multiple other print statements (lines 39-42, 65-68, 76-79, 85, 99-102, 109, 143-147, 150) also print to stdout, further corrupting output.

---

**DEFECT 3: Silent 30-minute timeout with no user signal**

Code quote (lines 49, 52-80):
```python
max_start_retries = 60  # 30 minutes at 30s intervals
attempts = 0

# 1. Wait for meeting to start and generate a conference record
while not conference_record_name and attempts < max_start_retries:
    try:
        res = httpx.get(
            "https://meet.googleapis.com/v2/spaces/"
            f"{space_id}/conferenceRecords",
            headers=headers,
        )
        res.raise_for_status()
        data = res.json()
        if data.get("conferenceRecords"):
            # Grab the most recent meeting instance
            conference_record_name = data["conferenceRecords"][0]["name"]
            print(
                f"\n  [MeetBot Daemon] 🟢 Meeting started!"
                f" Tracking {conference_record_name} in the background..."
            )
            break
    except Exception:
        pass
    attempts += 1
    time.sleep(30)  # Poll every 30 seconds

if not conference_record_name:
    print(
        f"\n  [MeetBot Daemon] 🔴 TIMEOUT: Meeting {space_id} never started"
        " after 30 minutes. Shutting down monitor."
    )
    return
```

**Finding:**
- Line 49: `max_start_retries = 60  # 30 minutes at 30s intervals` — waits for up to 60 × 30s = 1800s = 30 minutes.
- Line 73: `time.sleep(30)` — pauses for 30 seconds between each attempt.
- Line 75-80: After 60 failed attempts, prints a timeout message to stdout and returns **silently**.
- The daemon **does not signal the user** that the timeout occurred beyond the stdout print (which may not be visible in a TUI/API context).
- The tool returns `"SUCCESS: ..."` (line 185-190) even if the timeout fired, so the user may believe the daemon is still running when it has already given up.

---

**DEFECT 4: Transcript absent if not manually enabled, no persistent failure signal**

Code quote (lines 142-148):
```python
if not transcript_text:
    print(
        "\n  [MeetBot Daemon] 🔴 ERROR: Meeting ended, but Google generated"
        " no transcript. You forgot to click 'Start Transcription'"
        " in the Meet UI!"
    )
    return
```

**Finding:**
- The daemon waits for the transcript to be available (lines 113-140).
- If the transcript is missing (because the user did not manually enable transcription in the Meet UI), line 142 is true and the function returns **without storing anything**.
- The user is told upfront (lines 43-46) that they "MUST physically click 'Activities > Transcripts > Start Transcription'", but:
  - This critical requirement is buried in a `print()` statement (may not be visible in non-REPL contexts).
  - If the user forgets, the daemon silently fails with only a stdout print message.
  - There is **no persistent failure signal** (e.g., a failed job in the jobs queue, a memory fact, or an error surfaced to the chat context).
- The user may not realize the meeting was monitored at all if they are not watching stdout.

---

**DEFECT 5: Exceptions swallowed silently in three polling loops**

Code quote (lines 70-71, 104-105, 138-139):
```python
except Exception:
    pass
```

Appears in three places:
1. **Meeting-start loop** (lines 70-71): If the `conferenceRecords` API call fails (auth error, rate limit, malformed response), the exception is swallowed and the daemon retries silently.
2. **Meeting-end check loop** (lines 104-105): If the `conferenceRecord` query fails, the exception is swallowed.
3. **Transcript-fetch loop** (lines 138-139): If the `transcripts` or `entries` API call fails, the exception is swallowed.

**Finding:**
- No exception is logged, no error is recorded, and no signal is sent to the user.
- The daemon will retry indefinitely (or until the timeout) without the user knowing what went wrong.
- A transient network error, an auth token expiration, or an API endpoint change will silently fail, and the user will wait 30 minutes only to get a timeout message.
- This violates the principle of "loud failures" (P1-03 in the strategy document).

---

**Summary of UC-MEET-001 defects:**

1. **Daemon thread:** daemon=True, fire-and-forget, unjoinable, unkillable, unrecoverable → if agent exits, transcript is lost.
2. **Stdout pollution:** `print("You: ")` corrupts TUI and API output; assumes REPL context.
3. **Silent timeout:** 30 minutes with no user signal; tool returns "SUCCESS" even if timeout fired.
4. **Missing transcript:** If user forgets to click "Start Transcription", daemon silently fails with only a stdout message (not visible in many contexts).
5. **Error swallowing:** Three `except Exception: pass` blocks hide auth/network/API errors, causing silent retries for up to 30 minutes.

**Regression test:**
- Run the agent in TUI and observe that no "You: " prompt corruption occurs.
- Trigger `google_monitor_meeting` and let it timeout (or mock a 30-second timeout for testing); verify that a clear failure signal surfaces to the chat or the jobs queue (not just stdout).
- Trigger the tool with transcription manually disabled; verify a clear, persistent message that transcription must be enabled.
- Simulate an auth failure in the API calls; verify the error is logged and surfaced, not silently retried.
- Verify that the daemon thread is either (a) properly joined/cleaned up on agent exit, (b) refactored into a proper background job with recovery, or (c) moved to a more robust task scheduling system (e.g., the job queue with a retry policy).

---

## Conclusion

The Google Workspace integrations lack fundamental input validation (email, range syntax, file size), pagination support, and approval gates. UC-MEET-001 is a critical reliability and UX issue with daemon threads, stdout corruption, silent timeouts, and error swallowing. All five scenarios require fixes before GA per the UAT plan priority levels.
