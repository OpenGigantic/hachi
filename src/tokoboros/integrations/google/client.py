"""
Google HTTP Client

A dedicated, scalable HTTP client for Google APIs. 
It abstracts away the OAuth token lifecycle and provides clean HTTP verbs
so that individual services (Gmail, Drive) can focus purely on business logic.
"""

import httpx
from typing import Any, Dict, List, Optional
from tokoboros.core.oauth import OAuthManager

class GoogleHTTPClient:
    """
    Manages authenticated HTTP connections to Google Workspace APIs.
    Features lazy authentication to prevent blocking agent boot.
    """
    
    BASE_URL = "https://www.googleapis.com"
    
    def __init__(self, scopes: List[str]):
        """
        Initialize the client with required Google OAuth scopes.
        
        Args:
            scopes: A list of URL scopes required by the loaded services.
        """
        self.oauth = OAuthManager()
        self.scopes = scopes
        
    def _get_headers(self) -> Dict[str, str]:
        """
        Retrieves the OAuth token and constructs the Authorization header.
        Triggers the browser flow only if the token is missing/expired.
        """
        token = self.oauth.get_google_credentials(self.scopes)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes an authenticated GET request."""
        with httpx.Client(base_url=self.BASE_URL, headers=self._get_headers()) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
            
    def post(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes an authenticated POST request."""
        with httpx.Client(base_url=self.BASE_URL, headers=self._get_headers()) as client:
            response = client.post(endpoint, json=json_data, params=params)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def patch(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes an authenticated PATCH request."""
        with httpx.Client(base_url=self.BASE_URL, headers=self._get_headers()) as client:
            response = client.patch(endpoint, json=json_data, params=params)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def upload_multipart(self, endpoint: str, metadata: Dict[str, Any], file_path: str, mime_type: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes an authenticated POST request with multipart/related data (metadata + file)."""
        import json
        import mimetypes
        from pathlib import Path
        
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        token = self.oauth.get_google_credentials(self.scopes)
        # We don't use _get_headers() because httpx will set the Content-Type with the correct boundary for multipart
        headers = {"Authorization": f"Bearer {token}"}
        
        metadata_json = json.dumps(metadata).encode('utf-8')
        
        with open(path, 'rb') as f:
            file_data = f.read()
            
        files = {
            'metadata': ('metadata.json', metadata_json, 'application/json'),
            'file': (path.name, file_data, mime_type)
        }
        
        with httpx.Client(base_url=self.BASE_URL, headers=headers) as client:
            response = client.post(endpoint, files=files, params=params)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def delete(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Executes an authenticated DELETE request."""
        with httpx.Client(base_url=self.BASE_URL, headers=self._get_headers()) as client:
            response = client.delete(endpoint, params=params)
            response.raise_for_status()
