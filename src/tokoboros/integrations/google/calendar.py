"""
Google Calendar Service Plugin
"""

from typing import List
from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService

class CalendarService(GoogleService):
    
    @property
    def required_scopes(self) -> List[str]:
        return ["https://www.googleapis.com/auth/calendar.events"]

    def create_calendar_event(self, summary: str, start_time_iso: str, end_time_iso: str, description: str = "", create_meet_link: bool = False, attendees: List[str] = None) -> str:
        """Creates a Google Calendar event, optionally attaching a Google Meet link."""
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time_iso},
            "end": {"dateTime": end_time_iso},
        }
        
        if attendees:
            event["attendees"] = [{"email": email.strip()} for email in attendees]
            
        params = {}
        if create_meet_link:
            import uuid
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }
            params["conferenceDataVersion"] = 1
        
        response = self.client.post(
            "/calendar/v3/calendars/primary/events",
            params=params,
            json_data=event
        )
        link = response.get('htmlLink', '')
        meet_link = response.get('hangoutLink', '')
        
        result = f"Event created successfully! Link: {link}"
        if meet_link:
            result += f"\nGoogle Meet Link: {meet_link}"
            
        return result

    def get_upcoming_events(self, max_results: int = 5) -> str:
        """Gets the next upcoming calendar events."""
        import datetime
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        
        response = self.client.get(
            "/calendar/v3/calendars/primary/events",
            params={
                "timeMin": now,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime"
            }
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
        import datetime
        now = datetime.datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + datetime.timedelta(days=days_ahead)).isoformat() + 'Z'
        
        response = self.client.post(
            "/calendar/v3/freeBusy",
            json_data={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
                "timeZone": "UTC"
            }
        )
        
        calendars = response.get("calendars", {})
        primary = calendars.get("primary", {})
        busy_slots = primary.get("busy", [])
        
        # Simplified logic: If there are no busy slots, now is free.
        if not busy_slots:
            return f"The calendar is completely free for the next {days_ahead} days starting from {time_min}."
            
        return f"Busy slots found: {busy_slots}. Please pick a time outside these windows."

    def update_event(self, event_id: str, summary: str = None, start_time_iso: str = None, end_time_iso: str = None) -> str:
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
            f"/calendar/v3/calendars/primary/events/{event_id}",
            json_data=event
        )
        link = response.get('htmlLink', '')
        return f"Event updated successfully! Link: {link}"

    def delete_event(self, event_id: str) -> str:
        """Deletes a calendar event."""
        self.client.delete(f"/calendar/v3/calendars/primary/events/{event_id}")
        return f"Event {event_id} has been deleted."

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register({
            "name": "google_create_event",
            "description": "Schedule a Google Calendar event. Time must be in ISO 8601 format.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Title of the event"},
                    "start_time_iso": {"type": "string", "description": "Start time in ISO format INCLUDING the local timezone offset (e.g., 2026-05-01T23:30:00+05:30)"},
                    "end_time_iso": {"type": "string", "description": "End time in ISO format INCLUDING the local timezone offset (e.g., 2026-05-02T00:30:00+05:30)"},
                    "description": {"type": "string"},
                    "create_meet_link": {"type": "boolean", "description": "If true, automatically generates and attaches a Google Meet video conference link."},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of email addresses to invite"}
                },
                "required": ["summary", "start_time_iso", "end_time_iso"]
            }
        }, self.create_calendar_event)

        registry.register({
            "name": "google_list_events",
            "description": "Get the user's upcoming Google Calendar events.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer"}
                }
            }
        }, self.get_upcoming_events)

        registry.register({
            "name": "google_find_free_time",
            "description": "Analyze the calendar to find free time slots. Returns a list of busy periods to avoid.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "duration_minutes": {"type": "integer", "description": "Duration of the meeting in minutes"},
                    "days_ahead": {"type": "integer", "description": "How many days ahead to check"}
                }
            }
        }, self.find_free_time)

        registry.register({
            "name": "google_update_event",
            "description": "Reschedule or rename an existing calendar event. You must provide the exact event_id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The ID of the event to update"},
                    "summary": {"type": "string", "description": "New title (optional)"},
                    "start_time_iso": {"type": "string", "description": "New start time in ISO format (optional)"},
                    "end_time_iso": {"type": "string", "description": "New end time in ISO format (optional)"}
                },
                "required": ["event_id"]
            }
        }, self.update_event)

        registry.register({
            "name": "google_delete_event",
            "description": "Cancel/Delete a calendar event.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The ID of the event to delete"}
                },
                "required": ["event_id"]
            }
        }, self.delete_event)
