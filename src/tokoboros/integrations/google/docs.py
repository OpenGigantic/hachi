"""
Google Docs Service Plugin
"""

from typing import List
from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService

class DocsService(GoogleService):
    
    @property
    def required_scopes(self) -> List[str]:
        return ["https://www.googleapis.com/auth/documents"]

    def create_doc(self, title: str, initial_content: str = "") -> str:
        """Creates a Google Document and optionally inserts content."""
        response = self.client.post(
            "https://docs.googleapis.com/v1/documents", 
            json_data={"title": title}
        )
        doc_id = response.get("documentId")
        
        if doc_id and initial_content:
            requests = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": initial_content
                    }
                }
            ]
            self.client.post(
                f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate", 
                json_data={"requests": requests}
            )
            
        return f"Document '{title}' created successfully. ID: {doc_id} [Link: https://docs.google.com/document/d/{doc_id}]"

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
                
            requests = [
                {
                    "insertText": {
                        "location": {"index": end_index},
                        "text": "\n" + text
                    }
                }
            ]
            self.client.post(
                f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate", 
                json_data={"requests": requests}
            )
            return f"Successfully appended text to document {doc_id}."
        except Exception as e:
            return f"Failed to append to document: {str(e)}"

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register({
            "name": "google_create_doc",
            "description": "Create a new Google Document and optionally insert text into it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the document"},
                    "initial_content": {"type": "string", "description": "Optional text to insert into the document"}
                },
                "required": ["title"]
            }
        }, self.create_doc)
        
        registry.register({
            "name": "google_read_doc",
            "description": "Read the raw text content of a Google Document.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "The Document ID (from the URL)"}
                },
                "required": ["doc_id"]
            }
        }, self.read_doc)

        registry.register({
            "name": "google_append_doc_text",
            "description": "Safely append new text to the bottom of an existing Google Document. Useful for maintaining a running log of meeting notes without overwriting previous content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "The Document ID"},
                    "text": {"type": "string", "description": "The text to append"}
                },
                "required": ["doc_id", "text"]
            }
        }, self.append_doc_text)
