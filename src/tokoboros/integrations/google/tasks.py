"""Google Tasks Service Plugin."""

from __future__ import annotations

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService


class TasksService(GoogleService):
    """Google Tasks integration — task lists, tasks CRUD."""

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/tasks"]

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def list_task_lists(self) -> str:
        """Return all the user's Google Task lists."""
        response = self.client.get("/tasks/v1/users/@me/lists")
        items = response.get("items", [])
        if not items:
            return "No task lists found."
        lines = [f"[{t.get('id', '')}] {t.get('title', '(unnamed)')}" for t in items]
        return "\n".join(lines)

    def list_tasks(
        self,
        tasklist_id: str = "@default",
        show_completed: bool = False,
        max_results: int = 20,
    ) -> str:
        """Return tasks in a task list, optionally including completed ones."""
        params: dict[str, str | int] = {
            "showCompleted": str(show_completed).lower(),
            "maxResults": max_results,
        }
        response = self.client.get(f"/tasks/v1/lists/{tasklist_id}/tasks", params=params)
        items = response.get("items", [])
        if not items:
            return "No tasks found."
        lines = []
        for t in items:
            done = "✓" if t.get("status") == "completed" else "○"
            due = f" (due {t['due'][:10]})" if t.get("due") else ""
            notes = f" — {t['notes'][:60]}" if t.get("notes") else ""
            lines.append(f"{done} [{t.get('id', '')}] {t.get('title', '(no title)')}{due}{notes}")
        return "\n".join(lines)

    def create_task(
        self,
        title: str,
        tasklist_id: str = "@default",
        notes: str = "",
        due_date_iso: str = "",
    ) -> str:
        """Create a new task in a task list."""
        body: dict[str, str] = {"title": title}
        if notes:
            body["notes"] = notes
        if due_date_iso:
            # Google Tasks expects RFC 3339; append Z if plain date provided.
            body["due"] = due_date_iso if "T" in due_date_iso else f"{due_date_iso}T00:00:00Z"
        response = self.client.post(f"/tasks/v1/lists/{tasklist_id}/tasks", json_data=body)
        task_id = response.get("id", "")
        return f"Task created: '{title}' (id={task_id})."

    def complete_task(self, task_id: str, tasklist_id: str = "@default") -> str:
        """Mark a task as completed."""
        self.client.patch(
            f"/tasks/v1/lists/{tasklist_id}/tasks/{task_id}",
            json_data={"status": "completed"},
        )
        return f"Task {task_id} marked as completed."

    def delete_task(self, task_id: str, tasklist_id: str = "@default") -> str:
        """Permanently delete a task."""
        self.client.delete(f"/tasks/v1/lists/{tasklist_id}/tasks/{task_id}")
        return f"Task {task_id} deleted."

    def update_task(
        self,
        task_id: str,
        tasklist_id: str = "@default",
        title: str = "",
        notes: str = "",
        due_date_iso: str = "",
    ) -> str:
        """Update the title, notes, or due date of an existing task."""
        body: dict[str, str] = {}
        if title:
            body["title"] = title
        if notes:
            body["notes"] = notes
        if due_date_iso:
            body["due"] = due_date_iso if "T" in due_date_iso else f"{due_date_iso}T00:00:00Z"
        if not body:
            return (
                "Nothing to update — please supply at least one of title, notes, or due_date_iso."
            )
        self.client.patch(f"/tasks/v1/lists/{tasklist_id}/tasks/{task_id}", json_data=body)
        return f"Task {task_id} updated."

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register(
            {
                "name": "google_list_task_lists",
                "description": "List all of the user's Google Task lists (IDs and titles).",
                "input_schema": {"type": "object", "properties": {}},
            },
            self.list_task_lists,
        )

        registry.register(
            {
                "name": "google_list_tasks",
                "description": "List tasks in a Google Task list. "
                "Defaults to the primary list (@default).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tasklist_id": {
                            "type": "string",
                            "description": "Task list ID or '@default' for the primary list.",
                        },
                        "show_completed": {
                            "type": "boolean",
                            "description": "Include already-completed tasks.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of tasks to return (default 20).",
                        },
                    },
                },
            },
            self.list_tasks,
        )

        registry.register(
            {
                "name": "google_create_task",
                "description": "Create a new task in Google Tasks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title."},
                        "tasklist_id": {
                            "type": "string",
                            "description": "Task list ID (defaults to '@default').",
                        },
                        "notes": {"type": "string", "description": "Optional task description."},
                        "due_date_iso": {
                            "type": "string",
                            "description": "Optional due date (ISO 8601, e.g. 2026-06-15).",
                        },
                    },
                    "required": ["title"],
                },
            },
            self.create_task,
        )

        registry.register(
            {
                "name": "google_complete_task",
                "description": "Mark a Google Task as completed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "ID of the task."},
                        "tasklist_id": {
                            "type": "string",
                            "description": "Task list ID (defaults to '@default').",
                        },
                    },
                    "required": ["task_id"],
                },
            },
            self.complete_task,
        )

        registry.register(
            {
                "name": "google_update_task",
                "description": "Update the title, notes, or due date of an existing Google Task.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "ID of the task to update."},
                        "tasklist_id": {
                            "type": "string",
                            "description": "Task list ID (defaults to '@default').",
                        },
                        "title": {"type": "string", "description": "New title (optional)."},
                        "notes": {"type": "string", "description": "New notes (optional)."},
                        "due_date_iso": {
                            "type": "string",
                            "description": "New due date in ISO 8601 format (optional).",
                        },
                    },
                    "required": ["task_id"],
                },
            },
            self.update_task,
        )

        registry.register(
            {
                "name": "google_delete_task",
                "description": "Permanently delete a Google Task.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "ID of the task to delete."},
                        "tasklist_id": {
                            "type": "string",
                            "description": "Task list ID (defaults to '@default').",
                        },
                    },
                    "required": ["task_id"],
                },
            },
            self.delete_task,
        )
