"""
Google Integration Hub

The main entry point for the Google Workspace module.
It dynamically discovers required scopes and registers all tools from all active services.
"""

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.calendar import CalendarService
from tokoboros.integrations.google.client import GoogleHTTPClient
from tokoboros.integrations.google.contacts import ContactsService
from tokoboros.integrations.google.docs import DocsService
from tokoboros.integrations.google.drive import DriveService

# Import specific services
from tokoboros.integrations.google.gmail import GmailService
from tokoboros.integrations.google.meet import MeetService
from tokoboros.integrations.google.service import GoogleService
from tokoboros.integrations.google.sheets import SheetsService


class GoogleIntegrationHub:
    """
    The orchestrator for all Google Workspace plugins.
    Instantiates services, aggregates required scopes, and mounts tools.
    """

    def __init__(self):
        # 1. Instantiate the services without a client first to aggregate scopes
        self._services: list[GoogleService] = []

        # Instantiate services (temporarily passing None for client to get scopes)
        gmail = GmailService(client=None)  # type: ignore
        calendar = CalendarService(client=None)  # type: ignore
        drive = DriveService(client=None)  # type: ignore
        docs = DocsService(client=None)  # type: ignore
        sheets = SheetsService(client=None)  # type: ignore
        contacts = ContactsService(client=None)  # type: ignore
        meet = MeetService(client=None)  # type: ignore

        # Aggregate scopes dynamically
        all_scopes = set()
        all_scopes.update(gmail.required_scopes)
        all_scopes.update(calendar.required_scopes)
        all_scopes.update(drive.required_scopes)
        all_scopes.update(docs.required_scopes)
        all_scopes.update(sheets.required_scopes)
        all_scopes.update(contacts.required_scopes)
        all_scopes.update(meet.required_scopes)

        # 2. Instantiate the actual client with the aggregated scopes
        self.client = GoogleHTTPClient(scopes=list(all_scopes))

        # 3. Fully initialize services with the working client
        self._services = [
            GmailService(client=self.client),
            CalendarService(client=self.client),
            DriveService(client=self.client),
            DocsService(client=self.client),
            SheetsService(client=self.client),
            ContactsService(client=self.client),
            MeetService(client=self.client),
        ]

    def register_tools(self, registry: ToolRegistry) -> None:
        """
        Loops through all active plugins and mounts their tools into the agent.
        """
        for service in self._services:
            service.register_tools(registry)
