import json

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService


class ContactsService(GoogleService):
    """
    Google Contacts Integration (The Identity Layer)
    Provides autonomous resolution of names to emails/phone numbers.
    """

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/contacts"]

    def register_tools(self, registry: ToolRegistry) -> None:
        def google_search_contact(name: str) -> str:
            try:
                response = self.client.get(
                    "https://people.googleapis.com/v1/people/me/connections?personFields=names,emailAddresses,phoneNumbers&pageSize=1000"
                )

                search_lower = name.lower()
                matches = []
                for person in response.get("connections", []):
                    person_names = person.get("names", [])
                    if any(search_lower in n.get("displayName", "").lower() for n in person_names):
                        emails = [e.get("value") for e in person.get("emailAddresses", [])]
                        phones = [p.get("value") for p in person.get("phoneNumbers", [])]

                        matches.append(
                            {
                                "resourceName": person.get("resourceName", ""),
                                "name": person_names[0].get("displayName")
                                if person_names
                                else "Unknown",
                                "emails": emails,
                                "phones": phones,
                            }
                        )

                if not matches:
                    return f"No contacts found matching '{name}'."

                return (
                    f"Found {len(matches)} match(es) for '{name}':\n{json.dumps(matches, indent=2)}"
                )
            except Exception as e:
                return f"Failed to search Google Contacts: {str(e)}"

        def google_create_contact(name: str, email: str) -> str:
            try:
                body = {"names": [{"givenName": name}], "emailAddresses": [{"value": email}]}
                self.client.post(
                    "https://people.googleapis.com/v1/people:createContact", json_data=body
                )
                return f"Successfully saved new contact: {name} ({email})."
            except Exception as e:
                return f"Failed to create Google Contact: {str(e)}"

        def google_update_contact(resourceName: str, phone: str = None, email: str = None) -> str:
            try:
                # 1. Fetch existing contact to get the required etag
                person = self.client.get(
                    f"https://people.googleapis.com/v1/{resourceName}?personFields=names,emailAddresses,phoneNumbers"
                )

                update_fields = []
                # 2. Append new data
                if phone:
                    phones = person.get("phoneNumbers", [])
                    phones.append({"value": phone})
                    person["phoneNumbers"] = phones
                    update_fields.append("phoneNumbers")

                if email:
                    emails = person.get("emailAddresses", [])
                    emails.append({"value": email})
                    person["emailAddresses"] = emails
                    update_fields.append("emailAddresses")

                if not update_fields:
                    return "No updates provided."

                # 3. Send the PATCH request
                fields_str = ",".join(update_fields)
                self.client.patch(
                    f"https://people.googleapis.com/v1/{resourceName}:updateContact?updatePersonFields={fields_str}",
                    json_data=person,
                )
                return "Successfully updated contact."
            except Exception as e:
                return f"Failed to update Google Contact: {str(e)}"

        def google_list_contacts(max_results: int = 50) -> str:
            try:
                response = self.client.get(
                    f"https://people.googleapis.com/v1/people/me/connections?personFields=names,emailAddresses,phoneNumbers&pageSize={max_results}"
                )

                matches = []
                for person in response.get("connections", []):
                    person_names = person.get("names", [])
                    emails = [e.get("value") for e in person.get("emailAddresses", [])]
                    phones = [p.get("value") for p in person.get("phoneNumbers", [])]

                    matches.append(
                        {
                            "resourceName": person.get("resourceName", ""),
                            "name": person_names[0].get("displayName")
                            if person_names
                            else "Unknown",
                            "emails": emails,
                            "phones": phones,
                        }
                    )

                if not matches:
                    return "No contacts found."

                return f"Found {len(matches)} contacts:\n{json.dumps(matches, indent=2)}"
            except Exception as e:
                return f"Failed to list Google Contacts: {str(e)}"

        def google_delete_contact(resourceName: str) -> str:
            try:
                self.client.delete(f"https://people.googleapis.com/v1/{resourceName}:deleteContact")
                return f"Successfully deleted contact: {resourceName}"
            except Exception as e:
                return f"Failed to delete Google Contact: {str(e)}"

        registry.register(
            {
                "name": "google_search_contact",
                "description": "Fuzzy search the user's Google Contacts to find someone's exact "
                "email address or phone number. ALWAYS use this if the user asks you to email "
                "someone but doesn't provide the email.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name or partial name of the person to search for.",
                        }
                    },
                    "required": ["name"],
                },
            },
            google_search_contact,
        )

        registry.register(
            {
                "name": "google_create_contact",
                "description": "Autonomously save a new contact to the user's Google Contacts.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The full name of the contact."},
                        "email": {
                            "type": "string",
                            "description": "The email address to associate with them.",
                        },
                    },
                    "required": ["name", "email"],
                },
            },
            google_create_contact,
        )

        registry.register(
            {
                "name": "google_update_contact",
                "description": "Update an existing Google Contact with a new phone number or "
                "email.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "resourceName": {
                            "type": "string",
                            "description": "The resourceName of the contact (e.g. people/c123456). "
                            "Fetch this first using google_search_contact.",
                        },
                        "phone": {"type": "string", "description": "New phone number to add."},
                        "email": {"type": "string", "description": "New email address to add."},
                    },
                    "required": ["resourceName"],
                },
            },
            google_update_contact,
        )

        registry.register(
            {
                "name": "google_list_contacts",
                "description": "Fetch a list of all saved Google Contacts. Use this when the user "
                "asks 'fetch my contacts' or 'who is in my contacts'.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of contacts to return (default: 50)",
                        }
                    },
                },
            },
            google_list_contacts,
        )

        registry.register(
            {
                "name": "google_delete_contact",
                "description": "DANGER: Permanently delete a Google Contact. ALWAYS verbally ask "
                "the user for confirmation before executing this tool, unless they explicitly "
                "commanded you to delete the contact in their prompt.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "resourceName": {
                            "type": "string",
                            "description": "The resourceName of the contact to delete "
                            "(e.g. people/c123456).",
                        }
                    },
                    "required": ["resourceName"],
                },
            },
            google_delete_contact,
        )
