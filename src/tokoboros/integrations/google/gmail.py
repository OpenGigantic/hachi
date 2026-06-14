"""Gmail Service Plugin."""

from __future__ import annotations

import base64
import html
import logging
import re
from typing import Any

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService

logger = logging.getLogger(__name__)

# Drop <script>/<style> blocks wholesale, then strip remaining tags.
_HTML_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RUNS_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(raw: str) -> str:
    """Reduce an HTML email body to readable plain text (no extra deps).

    Tag soup in tool results overflows the TUI and is useless to the model, so
    we strip markup, unescape entities, and collapse runaway whitespace.
    """
    text = _HTML_BLOCK_RE.sub(" ", raw)
    # Keep line structure for common block-level breaks before nuking tags.
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|table|li|h[1-6])>", "\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RUNS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def _decode_b64(data: str) -> str:
    """URL-safe base64-decode a Gmail body part, tolerating missing padding."""
    padding = len(data) % 4
    if padding:
        data += "=" * (4 - padding)
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _extract_message_text(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload and return its best-effort plain text.

    Prefers ``text/plain``; falls back to ``text/html`` (stripped to text) so
    single-part HTML newsletters render cleanly instead of dumping raw markup.
    Recurses through nested multipart parts.
    """
    plain: str | None = None
    html_body: str | None = None

    def _walk(part: dict[str, Any]) -> None:
        nonlocal plain, html_body
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain" and plain is None:
                plain = _decode_b64(data)
            elif mime == "text/html" and html_body is None:
                html_body = _decode_b64(data)
        for sub in part.get("parts", []) or []:
            _walk(sub)

    _walk(payload)
    if plain and plain.strip():
        return plain.strip()
    if html_body and html_body.strip():
        return _html_to_text(html_body)
    return "[No readable body found]"


class GmailService(GoogleService):
    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/gmail.modify"]

    def send_email(
        self, to: str, subject: str, body: str, attachment_paths: list[str] | None = None
    ) -> str:
        """Sends an email using the Gmail API, optionally with attachments."""
        import base64
        import mimetypes
        from email.message import EmailMessage
        from pathlib import Path

        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = to
        msg["Subject"] = subject

        if attachment_paths:
            for path_str in attachment_paths:
                path = Path(path_str)
                if not path.exists():
                    return f"Failed to attach file: {path_str} not found."
                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(path, "rb") as f:
                    msg.add_attachment(
                        f.read(), maintype=maintype, subtype=subtype, filename=path.name
                    )

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        self.client.post("/gmail/v1/users/me/messages/send", json_data={"raw": raw_msg})
        return f"Email successfully sent to {to}."

    def list_recent_emails(self, max_results: int = 5) -> str:
        """Lists recent emails in the inbox."""
        response = self.client.get(
            "/gmail/v1/users/me/messages", params={"maxResults": max_results, "q": "in:inbox"}
        )
        messages = response.get("messages", [])

        if not messages:
            return "Your inbox is empty."

        result = []
        for msg in messages:
            msg_id = msg["id"]
            try:
                detail_resp = self.client.get(f"/gmail/v1/users/me/messages/{msg_id}")
            except Exception as exc:
                logger.warning("Failed to fetch detail for message %s: %s", msg_id, exc)
                continue
            headers = detail_resp.get("payload", {}).get("headers", [])

            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
            thread_id = detail_resp.get("threadId", msg_id)
            result.append(
                f"From: {sender}\nSubject: {subject}\n"
                f"Message-ID: {msg_id}\nThread-ID: {thread_id}\n---"
            )

        return "\n".join(result)

    def search_emails(self, query: str, max_results: int = 5) -> str:
        """Searches emails using standard Gmail query syntax."""
        response = self.client.get(
            "/gmail/v1/users/me/messages", params={"maxResults": max_results, "q": query}
        )
        messages = response.get("messages", [])

        if not messages:
            return f"No emails found matching query: {query}"

        result = []
        for msg in messages:
            msg_id = msg["id"]
            try:
                detail_resp = self.client.get(f"/gmail/v1/users/me/messages/{msg_id}")
            except Exception as exc:
                logger.warning("Failed to fetch detail for message %s: %s", msg_id, exc)
                continue
            headers = detail_resp.get("payload", {}).get("headers", [])

            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
            thread_id = detail_resp.get("threadId", msg_id)
            result.append(
                f"From: {sender}\nSubject: {subject}\n"
                f"Message-ID: {msg_id}\nThread-ID: {thread_id}\n---"
            )

        return "\n".join(result)

    def create_draft(self, to: str, subject: str, body: str) -> str:
        """Creates an email draft without sending it."""
        import base64
        from email.message import EmailMessage

        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = to
        msg["Subject"] = subject

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        response = self.client.post(
            "/gmail/v1/users/me/drafts", json_data={"message": {"raw": raw_msg}}
        )
        draft_id = response.get("id", "Unknown")
        return f"Draft created successfully. Draft ID: {draft_id}"

    def reply_thread(
        self, thread_id: str, body: str, attachment_paths: list[str] | None = None
    ) -> str:
        """Replies to an existing email thread, optionally with attachments."""
        import base64
        import mimetypes
        from email.message import EmailMessage
        from pathlib import Path

        # Fetch the thread to get the latest message headers (like Message-ID)
        thread_response = self.client.get(f"/gmail/v1/users/me/threads/{thread_id}")
        messages = thread_response.get("messages", [])
        if not messages:
            return f"Thread {thread_id} not found."

        latest_msg = messages[-1]
        headers = latest_msg.get("payload", {}).get("headers", [])

        message_id = next((h["value"] for h in headers if h["name"] == "Message-ID"), "")
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "")

        if not subject.startswith("Re:"):
            subject = "Re: " + subject

        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = sender
        msg["Subject"] = subject
        if message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = message_id

        if attachment_paths:
            for path_str in attachment_paths:
                path = Path(path_str)
                if not path.exists():
                    return f"Failed to attach file: {path_str} not found."
                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(path, "rb") as f:
                    msg.add_attachment(
                        f.read(), maintype=maintype, subtype=subtype, filename=path.name
                    )

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        self.client.post(
            "/gmail/v1/users/me/messages/send", json_data={"raw": raw_msg, "threadId": thread_id}
        )
        return f"Successfully replied to thread {thread_id}."

    def archive_email(self, message_id: str) -> str:
        """Safely archives an email by removing it from the INBOX without deleting it."""
        try:
            self.client.post(
                f"/gmail/v1/users/me/messages/{message_id}/modify",
                json_data={"removeLabelIds": ["INBOX"]},
            )
            return f"Successfully archived email {message_id}."
        except Exception as e:
            return f"Failed to archive email: {str(e)}"

    def mark_email_read(self, message_id: str) -> str:
        """Marks an email as read."""
        try:
            self.client.post(
                f"/gmail/v1/users/me/messages/{message_id}/modify",
                json_data={"removeLabelIds": ["UNREAD"]},
            )
            return f"Successfully marked email {message_id} as read."
        except Exception as e:
            return f"Failed to mark email as read: {str(e)}"

    def list_labels(self) -> str:
        """Lists all Gmail labels (folders)."""
        try:
            response = self.client.get("/gmail/v1/users/me/labels")
            labels = response.get("labels", [])
            if not labels:
                return "No labels found."

            result = ["Available Gmail Labels (Folders):"]
            for label in labels:
                result.append(f"- {label.get('name')} (ID: {label.get('id')})")
            return "\n".join(result)
        except Exception as e:
            return f"Failed to list labels: {str(e)}"

    def create_label(self, label_name: str) -> str:
        """Creates a new Gmail label (folder)."""
        try:
            response = self.client.post(
                "/gmail/v1/users/me/labels",
                json_data={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            label_id = response.get("id")
            return f"Successfully created label '{label_name}' with ID: {label_id}"
        except Exception as e:
            return f"Failed to create label: {str(e)}"

    def categorize_email(self, message_id: str, label_id: str) -> str:
        """Applies a label to an email (moves it to a folder)."""
        try:
            self.client.post(
                f"/gmail/v1/users/me/messages/{message_id}/modify",
                json_data={"addLabelIds": [label_id]},
            )
            return f"Successfully applied label {label_id} to email {message_id}."
        except Exception as e:
            return f"Failed to categorize email: {str(e)}"

    def batch_categorize_emails(self, message_ids: list[str], label_id: str) -> str:
        """Applies a specific label to multiple emails simultaneously."""
        try:
            self.client.post(
                "/gmail/v1/users/me/messages/batchModify",
                json_data={"ids": message_ids, "addLabelIds": [label_id]},
            )
            return f"Successfully applied label {label_id} to {len(message_ids)} emails."
        except Exception as e:
            return f"Failed to batch categorize emails: {str(e)}"

    def _render_message(self, msg: dict[str, Any]) -> str:
        """Format one Gmail message as ``--- header ---`` + readable body."""
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
        date = next((h["value"] for h in headers if h["name"] == "Date"), "Unknown")
        body = _extract_message_text(payload)
        return f"--- Message from {sender} on {date} ---\n{body}\n"

    def read_email_thread(self, thread_id: str) -> str:
        """Reads and extracts text content from an entire email thread.

        Falls back to reading ``thread_id`` as a single message id when the
        thread endpoint 404s — the model often passes a Message-ID where a
        Thread-ID is expected, and both are interchangeable hex ids.
        """
        try:
            thread_response = self.client.get(f"/gmail/v1/users/me/threads/{thread_id}")
            messages = thread_response.get("messages", [])
            if not messages:
                return f"Thread {thread_id} not found."
            return "\n".join(self._render_message(msg) for msg in messages)
        except Exception as e:
            if "404" in str(e):
                try:
                    msg = self.client.get(f"/gmail/v1/users/me/messages/{thread_id}")
                    return self._render_message(msg)
                except Exception as inner:
                    return f"Failed to read thread or message {thread_id}: {str(inner)}"
            return f"Failed to read thread: {str(e)}"

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register Gmail tools into the agent's tool registry."""
        registry.register(
            {
                "name": "google_send_email",
                "description": "Send an email via Gmail, optionally with file attachments.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "attachment_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of absolute paths to local files to attach"
                            ),
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            self.send_email,
        )

        registry.register(
            {
                "name": "google_list_emails",
                "description": "List the most recent emails in the user's Gmail inbox.",
                "input_schema": {
                    "type": "object",
                    "properties": {"max_results": {"type": "integer"}},
                },
            },
            self.list_recent_emails,
        )

        registry.register(
            {
                "name": "google_search_emails",
                "description": (
                    "Search the user's Gmail using advanced query syntax"
                    " (e.g., 'from:boss has:attachment')."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Gmail search query"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            self.search_emails,
        )

        registry.register(
            {
                "name": "google_create_draft",
                "description": (
                    "Create an email draft without sending it. Use this when you want"
                    " the user to review the email before it goes out."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            self.create_draft,
        )

        registry.register(
            {
                "name": "google_reply_thread",
                "description": (
                    "Reply directly to an existing email thread. You must provide the exact"
                    " thread_id retrieved from list_emails or search_emails."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {
                            "type": "string",
                            "description": "The ID of the thread to reply to",
                        },
                        "body": {"type": "string", "description": "The body of your reply"},
                        "attachment_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of absolute paths to local files to attach"
                            ),
                        },
                    },
                    "required": ["thread_id", "body"],
                },
            },
            self.reply_thread,
        )

        registry.register(
            {
                "name": "google_read_email_thread",
                "description": (
                    "Extract the raw plain-text content from an entire email thread."
                    " Use this to read the full context of an email before replying or summarizing."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {
                            "type": "string",
                            "description": "The ID of the thread to read",
                        }
                    },
                    "required": ["thread_id"],
                },
            },
            self.read_email_thread,
        )

        registry.register(
            {
                "name": "google_archive_email",
                "description": (
                    "Safely archive an email (removes it from the INBOX without deleting it"
                    " permanently). Use this for cleaning up the user's inbox after reading"
                    " newsletters or completed threads."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The ID of the message to archive",
                        }
                    },
                    "required": ["message_id"],
                },
            },
            self.archive_email,
        )

        registry.register(
            {
                "name": "google_mark_email_read",
                "description": "Mark an email as read to clear the notification.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The ID of the message to mark as read",
                        }
                    },
                    "required": ["message_id"],
                },
            },
            self.mark_email_read,
        )

        registry.register(
            {
                "name": "google_list_gmail_labels",
                "description": (
                    "List all Gmail labels (folders). Use this to find the exact Label ID"
                    " before categorizing an email."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            self.list_labels,
        )

        registry.register(
            {
                "name": "google_create_gmail_label",
                "description": (
                    "Create a new Gmail label (folder) to help organize the user's inbox"
                    " (e.g. 'Newsletters', 'Finance')."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label_name": {
                            "type": "string",
                            "description": "The name of the new label/folder",
                        }
                    },
                    "required": ["label_name"],
                },
            },
            self.create_label,
        )

        registry.register(
            {
                "name": "google_categorize_email",
                "description": (
                    "Apply a specific label to an email to categorize it. You must fetch the"
                    " label_id first using google_list_gmail_labels or google_create_gmail_label."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The ID of the email message",
                        },
                        "label_id": {
                            "type": "string",
                            "description": "The ID of the label to apply",
                        },
                    },
                    "required": ["message_id", "label_id"],
                },
            },
            self.categorize_email,
        )

        registry.register(
            {
                "name": "google_batch_categorize_emails",
                "description": (
                    "Apply a specific label to multiple emails at once. This is highly optimized"
                    " and should ALWAYS be used instead of google_categorize_email when organizing"
                    " multiple emails into the same folder."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "A list of email message IDs to categorize",
                        },
                        "label_id": {
                            "type": "string",
                            "description": "The ID of the label to apply",
                        },
                    },
                    "required": ["message_ids", "label_id"],
                },
            },
            self.batch_categorize_emails,
        )
