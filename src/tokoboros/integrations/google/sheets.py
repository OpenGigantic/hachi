"""
Google Sheets Service Plugin
"""

from typing import List
from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService

class SheetsService(GoogleService):
    
    @property
    def required_scopes(self) -> List[str]:
        return ["https://www.googleapis.com/auth/spreadsheets"]

    def append_sheet_row(self, spreadsheet_id: str, range_name: str, values: List[str]) -> str:
        """Appends a row of data to a spreadsheet."""
        body = {
            "values": [values]
        }
        response = self.client.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}:append",
            params={"valueInputOption": "USER_ENTERED"},
            json_data=body
        )
        updated_cells = response.get("updates", {}).get("updatedCells", 0)
        return f"Successfully appended row to {spreadsheet_id}. {updated_cells} cells updated."

    def create_sheet(self, title: str) -> str:
        """Creates a new Google Spreadsheet."""
        try:
            response = self.client.post(
                "https://sheets.googleapis.com/v4/spreadsheets",
                json_data={"properties": {"title": title}}
            )
            sheet_id = response.get("spreadsheetId")
            return f"Spreadsheet '{title}' created successfully. ID: {sheet_id} [Link: https://docs.google.com/spreadsheets/d/{sheet_id}]"
        except Exception as e:
            return f"Failed to create spreadsheet: {str(e)}"

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

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register({
            "name": "google_append_sheet_row",
            "description": "Append a row of data to a Google Spreadsheet. You must provide the exact spreadsheet_id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The ID of the spreadsheet"},
                    "range_name": {"type": "string", "description": "The range or sheet name (e.g., 'Sheet1' or 'A1:D1')"},
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of string values to append as a row"
                    }
                },
                "required": ["spreadsheet_id", "range_name", "values"]
            }
        }, self.append_sheet_row)

        registry.register({
            "name": "google_create_sheet",
            "description": "Create a new blank Google Spreadsheet.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the new spreadsheet"}
                },
                "required": ["title"]
            }
        }, self.create_sheet)

        registry.register({
            "name": "google_read_sheet",
            "description": "Read data from a Google Spreadsheet. Data is returned in CSV format.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The ID of the spreadsheet"},
                    "range_name": {"type": "string", "description": "The range or sheet name to read (e.g., 'Sheet1' or 'Sheet1!A1:D10')"}
                },
                "required": ["spreadsheet_id", "range_name"]
            }
        }, self.read_sheet)
