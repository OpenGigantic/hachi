"""
Notion Databases Service
Handles querying and searching across Notion databases.
"""
from typing import List, Dict, Any
from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.notion.service import NotionService

class NotionDatabases(NotionService):
    
    def search_workspace(self, query: str) -> str:
        """
        Performs a global search across the entire connected Notion workspace.
        """
        payload = {"query": query}
        
        try:
            res = self._post("/search", json_data=payload)
            results = res.get("results", [])
            
            if not results:
                return f"No results found in Notion for query: '{query}'"
                
            formatted = []
            for item in results[:5]:  # Limit to top 5 to prevent context bloat
                item_id = item.get("id")
                item_type = item.get("object")
                url = item.get("url")
                
                # Extract title based on whether it's a page or database
                title = "Untitled"
                if "properties" in item:
                    props = item["properties"]
                    # Finding the title property (it's the only one with type='title')
                    for key, val in props.items():
                        if val.get("type") == "title":
                            title_arr = val.get("title", [])
                            if title_arr:
                                title = "".join([t.get("plain_text", "") for t in title_arr])
                                break
                                
                formatted.append(f"[{item_type.upper()}] {title}\nID: {item_id}\nURL: {url}")
                
            return "\n\n".join(formatted)
        except Exception as e:
            return f"Failed to search Notion workspace: {str(e)}"

    def query_database(self, database_id: str) -> str:
        """
        Queries a database to list its pages.
        """
        try:
            res = self._post(f"/databases/{database_id}/query")
            results = res.get("results", [])
            
            if not results:
                return f"Database is empty."
                
            formatted = []
            for item in results[:20]:
                item_id = item.get("id")
                url = item.get("url")
                
                title = "Untitled"
                if "properties" in item:
                    props = item["properties"]
                    for key, val in props.items():
                        if val.get("type") == "title":
                            title_arr = val.get("title", [])
                            if title_arr:
                                title = "".join([t.get("plain_text", "") for t in title_arr])
                                break
                                
                formatted.append(f"- {title} (ID: {item_id})")
                
            return "\n".join(formatted)
        except Exception as e:
            return f"Failed to query database: {str(e)}"

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register({
            "name": "notion_search_workspace",
            "description": "Perform a global text search across the entire Notion workspace to find pages or databases.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search term to look for."}
                },
                "required": ["query"]
            }
        }, self.search_workspace)

        registry.register({
            "name": "notion_query_database",
            "description": "Query a Notion database to list all the pages (documents) inside it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {"type": "string", "description": "The UUID of the Notion database to query."}
                },
                "required": ["database_id"]
            }
        }, self.query_database)
