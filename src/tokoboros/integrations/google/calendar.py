"""Google Calendar Service Plugin."""

from __future__ import annotations

import datetime

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService


class CalendarService(GoogleService):
    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/calendar.events"]

    def create_calendar_event(
        self,
        summary: str,
        start_time_iso: str,
        end_time_iso: str,
        description: str = "",
        create_meet_link: bool = False,
        attendees: list[str] | None = None,
    ) -> str:
        """Creates a Google Calendar event, optionally attaching a Google Meet link."""
        from typing import Any as _Any

        event: dict[str, _Any] = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time_iso},
            "end": {"dateTime": end_time_iso},
        }

        if attendees:
            event["attendees"] = [{"email": email.strip()} for email in attendees]

        params: dict[str, _Any] = {}
        if create_meet_link:
            import uuid

            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            params["conferenceDataVersion"] = 1

        response = self.client.post(
            "/calendar/v3/calendars/primary/events", params=params, json_data=event
        )
        link = response.get("htmlLink", "")
        meet_link = response.get("hangoutLink", "")

        result = f"Event created successfully! Link: {link}"
        if meet_link:
            result += f"\nGoogle Meet Link: {meet_link}"

        return result

    def get_upcoming_events(self, max_results: int = 5) -> str:
        """Gets the next upcoming calendar events."""
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z"

        response = self.client.get(
            "/calendar/v3/calendars/primary/events",
            params={
                "timeMin": now,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        events = response.get("items", [])

        if not events:
            return "No upcoming events found."

        result = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            result.append(f"[{start}] {e.get('summary', 'Busy')}")

        return "\n".join(result)

    def find_free_time(self, duration_minutes: int = 60, days_ahead: int = 3) -> str:
        """Scans the calendar to find the next available time slot."""
        now_dt = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        time_min = now_dt.isoformat() + "Z"
        time_max = (now_dt + datetime.timedelta(days=days_ahead)).isoformat() + "Z"

        response = self.client.post(
            "/calendar/v3/freeBusy",
            json_data={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
                "timeZone": "UTC",
            },
        )

        calendars = response.get("calendars", {})
        primary = calendars.get("primary", {})
        busy_slots = primary.get("busy", [])

        # Simplified logic: If there are no busy slots, now is free.
        if not busy_slots:
            return (
                f"The calendar is completely free for the next {days_ahead} days "
                f"starting from {time_min}."
            )

        return f"Busy slots found: {busy_slots}. Please pick a time outside these windows."

    def update_event(
        self,
        event_id: str,
        summary: str | None = None,
        start_time_iso: str | None = None,
        end_time_iso: str | None = None,
    ) -> str:
        """Updates an existing calendar event."""
        # First, fetch the existing event
        event = self.client.get(f"/calendar/v3/calendars/primary/events/{event_id}")

        if summary:
            event["summary"] = summary
        if start_time_iso:
            event["start"] = {"dateTime": start_time_iso}
        if end_time_iso:
            event["end"] = {"dateTime": end_time_iso}

        response = self.client.patch(
            f"/calendar/v3/calendars/primary/events/{event_id}", json_data=event
        )
        link = response.get("htmlLink", "")
        return f"Event updated successfully! Link: {link}"

    def delete_event(self, event_id: str) -> str:
        """Deletes a calendar event."""
        self.client.delete(f"/calendar/v3/calendars/primary/events/{event_id}")
        return f"Event {event_id} has been deleted."

    def create_recurring_event(
        self,
        summary: str,
        start_time_iso: str,
        end_time_iso: str,
        recurrence_rule: str,
        description: str = "",
        attendees: list[str] | None = None,
        create_meet_link: bool = False,
    ) -> str:
        """Creates a recurring Google Calendar event using an RFC 5545 RRULE.

        Common recurrence_rule examples:
          - Daily:         RRULE:FREQ=DAILY
          - Every weekday: RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
          - Weekly Monday: RRULE:FREQ=WEEKLY;BYDAY=MO
          - Monthly 1st:   RRULE:FREQ=MONTHLY;BYDAY=1MO
          - Until a date:  RRULE:FREQ=DAILY;UNTIL=20261231T235959Z
          - N occurrences: RRULE:FREQ=WEEKLY;COUNT=10
        """
        from typing import Any as _Any

        event: dict[str, _Any] = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time_iso},
            "end": {"dateTime": end_time_iso},
            "recurrence": [recurrence_rule],
        }

        if attendees:
            event["attendees"] = [{"email": e.strip()} for e in attendees]

        params: dict[str, _Any] = {}
        if create_meet_link:
            import uuid

            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            params["conferenceDataVersion"] = 1

        response = self.client.post(
            "/calendar/v3/calendars/primary/events", params=params, json_data=event
        )
        link = response.get("htmlLink", "")
        meet_link = response.get("hangoutLink", "")
        result = f"Recurring event created! Rule: {recurrence_rule}  Link: {link}"
        if meet_link:
            result += f"\nMeet: {meet_link}"
        return result

    def get_events_for_date(self, date_iso: str, max_results: int = 20) -> str:
        """Return all calendar events that fall on a given date (YYYY-MM-DD).

        Used by the day-planning tool to fetch what's scheduled on a specific day.
        """
        time_min = f"{date_iso}T00:00:00Z"
        time_max = f"{date_iso}T23:59:59Z"

        response = self.client.get(
            "/calendar/v3/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        events = response.get("items", [])
        if not events:
            return f"No events scheduled for {date_iso}."

        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            end = e["end"].get("dateTime", e["end"].get("date", ""))
            # Show only the time portion for readability.
            start_t = start[11:16] if len(start) > 10 else start
            end_t = end[11:16] if len(end) > 10 else end
            attendee_count = len(e.get("attendees", []))
            attend_note = f" ({attendee_count} attendees)" if attendee_count else ""
            meet = " 📹" if e.get("hangoutLink") else ""
            lines.append(
                f"{start_t}–{end_t}  {e.get('summary', 'Busy')}{attend_note}{meet}"
                f"  [id={e.get('id', '')}]"
            )
        return "\n".join(lines)

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register Google Calendar tools into the agent's tool registry."""
        registry.register(
            {
                "name": "google_create_event",
                "description": "Schedule a Google Calendar event. Time must be in ISO 8601 format.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Title of the event"},
                        "start_time_iso": {
                            "type": "string",
                            "description": "Start time in ISO format INCLUDING the local timezone "
                            "offset (e.g., 2026-05-01T23:30:00+05:30)",
                        },
                        "end_time_iso": {
                            "type": "string",
                            "description": "End time in ISO format INCLUDING the local timezone "
                            "offset (e.g., 2026-05-02T00:30:00+05:30)",
                        },
                        "description": {"type": "string"},
                        "create_meet_link": {
                            "type": "boolean",
                            "description": "If true, automatically generates and attaches a "
                            "Google Meet video conference link.",
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of email addresses to invite",
                        },
                    },
                    "required": ["summary", "start_time_iso", "end_time_iso"],
                },
            },
            self.create_calendar_event,
        )

        registry.register(
            {
                "name": "google_list_events",
                "description": "Get the user's upcoming Google Calendar events.",
                "input_schema": {
                    "type": "object",
                    "properties": {"max_results": {"type": "integer"}},
                },
            },
            self.get_upcoming_events,
        )

        registry.register(
            {
                "name": "google_find_free_time",
                "description": "Analyze the calendar to find free time slots. Returns a list of "
                "busy periods to avoid.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Duration of the meeting in minutes",
                        },
                        "days_ahead": {
                            "type": "integer",
                            "description": "How many days ahead to check",
                        },
                    },
                },
            },
            self.find_free_time,
        )

        registry.register(
            {
                "name": "google_update_event",
                "description": "Reschedule or rename an existing calendar event. You must provide "
                "the exact event_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "The ID of the event to update",
                        },
                        "summary": {"type": "string", "description": "New title (optional)"},
                        "start_time_iso": {
                            "type": "string",
                            "description": "New start time in ISO format (optional)",
                        },
                        "end_time_iso": {
                            "type": "string",
                            "description": "New end time in ISO format (optional)",
                        },
                    },
                    "required": ["event_id"],
                },
            },
            self.update_event,
        )

        registry.register(
            {
                "name": "google_delete_event",
                "description": "Cancel/Delete a calendar event.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "The ID of the event to delete",
                        }
                    },
                    "required": ["event_id"],
                },
            },
            self.delete_event,
        )

        registry.register(
            {
                "name": "google_create_recurring_event",
                "description": (
                    "Create a recurring Google Calendar event using an RFC 5545 RRULE. "
                    "Use this for standing meetings, daily standups, weekly reviews, etc. "
                    "Common recurrence_rule examples — "
                    "daily: 'RRULE:FREQ=DAILY', "
                    "every weekday: 'RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', "
                    "weekly on Monday: 'RRULE:FREQ=WEEKLY;BYDAY=MO', "
                    "10 occurrences: 'RRULE:FREQ=WEEKLY;COUNT=10', "
                    "until a date: 'RRULE:FREQ=DAILY;UNTIL=20261231T235959Z'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Event title."},
                        "start_time_iso": {
                            "type": "string",
                            "description": "Start time with timezone offset "
                            "(e.g. 2026-06-09T10:00:00+05:30).",
                        },
                        "end_time_iso": {
                            "type": "string",
                            "description": "End time with timezone offset.",
                        },
                        "recurrence_rule": {
                            "type": "string",
                            "description": "RFC 5545 RRULE string "
                            "(e.g. 'RRULE:FREQ=WEEKLY;BYDAY=MO').",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional event description / agenda.",
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Email addresses to invite.",
                        },
                        "create_meet_link": {
                            "type": "boolean",
                            "description": "Attach a Google Meet link to every occurrence.",
                        },
                    },
                    "required": ["summary", "start_time_iso", "end_time_iso", "recurrence_rule"],
                },
            },
            self.create_recurring_event,
        )

        registry.register(
            {
                "name": "google_get_events_for_date",
                "description": (
                    "Fetch all calendar events on a specific date (YYYY-MM-DD). "
                    "Use this to see what's scheduled before planning a day."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "date_iso": {
                            "type": "string",
                            "description": "The date to query in YYYY-MM-DD format.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum events to return (default 20).",
                        },
                    },
                    "required": ["date_iso"],
                },
            },
            self.get_events_for_date,
        )
