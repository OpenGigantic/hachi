"""
Google Drive Service Plugin
"""

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService


class DriveService(GoogleService):
    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/drive.file"]

    def search_drive(self, query: str, max_results: int = 5) -> str:
        """Searches Google Drive for files."""
        response = self.client.get(
            "/drive/v3/files",
            params={
                "q": query,
                "pageSize": max_results,
                "fields": "files(id, name, mimeType, webViewLink)",
            },
        )
        files = response.get("files", [])
        if not files:
            return f"No files found matching query: {query}"

        result = [f"Found {len(files)} files:"]
        for f in files:
            result.append(f"- {f.get('name')} (ID: {f.get('id')}) [Link: {f.get('webViewLink')}]")
        return "\n".join(result)

    def create_folder(self, folder_name: str, parent_id: str = None) -> str:
        """Creates a folder in Google Drive."""
        metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            metadata["parents"] = [parent_id]

        response = self.client.post("/drive/v3/files", json_data=metadata)
        folder_id = response.get("id")
        return f"Folder '{folder_name}' created successfully. ID: {folder_id}"

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

    def move_file(self, file_id: str, new_parent_id: str) -> str:
        """Moves a file to a new folder."""
        # Retrieve the existing parents to remove
        file_meta = self.client.get(f"/drive/v3/files/{file_id}", params={"fields": "parents"})
        previous_parents = ",".join(file_meta.get("parents", []))

        # Move the file to the new folder
        self.client.patch(
            f"/drive/v3/files/{file_id}",
            params={"addParents": new_parent_id, "removeParents": previous_parents},
            json_data={},  # Empty body
        )
        return f"File {file_id} moved to folder {new_parent_id}."

    def share_file(self, file_id: str, email: str, role: str = "writer") -> str:
        """Shares a file with a specific user."""
        try:
            permission = {"type": "user", "role": role, "emailAddress": email}
            self.client.post(f"/drive/v3/files/{file_id}/permissions", json_data=permission)
            return f"Successfully shared file {file_id} with {email} as {role}."
        except Exception as e:
            return f"Failed to share file: {str(e)}"

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

    def register_tools(self, registry: ToolRegistry) -> None:
        registry.register(
            {
                "name": "google_search_drive",
                "description": "Search for files in Google Drive using standard Drive query "
                "syntax.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g., \"name contains 'budget'\")",
                        },
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            self.search_drive,
        )

        registry.register(
            {
                "name": "google_create_folder",
                "description": "Create a new folder in Google Drive.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "folder_name": {"type": "string"},
                        "parent_id": {
                            "type": "string",
                            "description": "Optional ID of the parent folder",
                        },
                    },
                    "required": ["folder_name"],
                },
            },
            self.create_folder,
        )

        registry.register(
            {
                "name": "google_upload_file",
                "description": "Upload a local binary file (e.g. PDF, Image, Text) to Google "
                "Drive.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the local file to upload",
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "Optional ID of the destination folder in Drive",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            self.upload_file,
        )

        registry.register(
            {
                "name": "google_move_file",
                "description": "Move a file into a specific folder in Google Drive.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "ID of the file to move"},
                        "new_parent_id": {
                            "type": "string",
                            "description": "ID of the destination folder",
                        },
                    },
                    "required": ["file_id", "new_parent_id"],
                },
            },
            self.move_file,
        )

        registry.register(
            {
                "name": "google_share_file",
                "description": "Share a Google Drive file or Google Doc with a specific email "
                "address.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "ID of the file to share"},
                        "email": {
                            "type": "string",
                            "description": "Email address of the user to share with",
                        },
                        "role": {
                            "type": "string",
                            "enum": ["reader", "commenter", "writer"],
                            "description": "Permission role (default: writer)",
                        },
                    },
                    "required": ["file_id", "email"],
                },
            },
            self.share_file,
        )

        registry.register(
            {
                "name": "google_trash_file",
                "description": "Safely move a file or folder to the Google Drive Trash. The item "
                "is not permanently deleted and can be recovered within 30 days. Use this instead "
                "of a hard delete to maintain user safety.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID of the file or folder to trash",
                        }
                    },
                    "required": ["file_id"],
                },
            },
            self.trash_file,
        )
