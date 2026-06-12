import threading
import time

import httpx

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.service import GoogleService


class MeetService(GoogleService):
    """
    Google Meet Agent (Native API Tier)
    Uses Google Workspace's native transcription API to process meeting notes.
    """

    @property
    def required_scopes(self) -> list[str]:
        # Required to read meeting artifacts
        return ["https://www.googleapis.com/auth/meetings.space.readonly"]

    def __init__(self, client):
        super().__init__(client)

    def register_tools(self, registry: ToolRegistry) -> None:
        def google_monitor_meeting(meet_link: str) -> str:
            # Extract space_id (e.g. abc-defg-hij)
            space_id = meet_link.split("/")[-1].split("?")[0]

            try:
                # 1. Probe the Workspace Native API
                headers = self.client._get_headers()
                probe_url = f"https://meet.googleapis.com/v2/spaces/{space_id}"

                response = httpx.get(probe_url, headers=headers)
                response.raise_for_status()

                # 2. Deploy Autonomous Background Daemon (Real Production Polling)
                def _poll_for_transcript():
                    print(
                        f"\n  [MeetBot Daemon] Hooked into {space_id}."
                        " Waiting for meeting to begin..."
                    )
                    print(
                        "  [MeetBot Daemon] ⚠️ CRITICAL: You must manually click"
                        " 'Activities > Transcripts > Start Transcription' in the Google Meet UI."
                    )

                    conference_record_name = None
                    max_start_retries = 60  # 30 minutes at 30s intervals
                    attempts = 0

                    # 1. Wait for meeting to start and generate a conference record
                    while not conference_record_name and attempts < max_start_retries:
                        try:
                            res = httpx.get(
                                "https://meet.googleapis.com/v2/spaces/"
                                f"{space_id}/conferenceRecords",
                                headers=headers,
                            )
                            res.raise_for_status()
                            data = res.json()
                            if data.get("conferenceRecords"):
                                # Grab the most recent meeting instance
                                conference_record_name = data["conferenceRecords"][0]["name"]
                                print(
                                    f"\n  [MeetBot Daemon] 🟢 Meeting started!"
                                    f" Tracking {conference_record_name} in the background..."
                                )
                                break
                        except Exception:
                            pass
                        attempts += 1
                        time.sleep(30)  # Poll every 30 seconds

                    if not conference_record_name:
                        print(
                            f"\n  [MeetBot Daemon] 🔴 TIMEOUT: Meeting {space_id} never started"
                            " after 30 minutes. Shutting down monitor."
                        )
                        return

                    # 2. Poll the specific Conference Record to detect when
                    # the host ends the meeting
                    meeting_active = True
                    print("  [MeetBot Daemon] Monitoring active meeting...")

                    while meeting_active:
                        try:
                            res = httpx.get(
                                f"https://meet.googleapis.com/v2/{conference_record_name}",
                                headers=headers,
                            )
                            res.raise_for_status()
                            record_data = res.json()

                            # If endTime is populated, the meeting has officially concluded
                            if "endTime" in record_data:
                                meeting_active = False
                                print(
                                    "\n  [MeetBot Daemon] 🔴 Host closed the meeting."
                                    " Meeting has officially ended."
                                )
                                break
                        except Exception:
                            pass
                        time.sleep(30)  # Poll every 30 seconds

                    # 3. Meeting is over. Fetch the transcript.
                    print("  [MeetBot Daemon] Searching for Google Meet transcripts...")

                    transcript_text = None
                    # Give Google up to 5 minutes to process the text after the meeting ends
                    for _ in range(10):
                        try:
                            res = httpx.get(
                                f"https://meet.googleapis.com/v2/{conference_record_name}"
                                "/transcripts",
                                headers=headers,
                            )
                            res.raise_for_status()
                            data = res.json()
                            if data.get("transcripts"):
                                transcript_name = data["transcripts"][0]["name"]

                                # Fetch the actual transcript entries
                                entries_res = httpx.get(
                                    f"https://meet.googleapis.com/v2/{transcript_name}/entries",
                                    headers=headers,
                                )
                                entries_data = entries_res.json()

                                lines = [
                                    entry.get("text", "")
                                    for entry in entries_data.get("entries", [])
                                ]
                                transcript_text = "\n".join(lines)
                                break
                        except Exception:
                            pass
                        time.sleep(30)  # Poll every 30 seconds

                    if not transcript_text:
                        print(
                            "\n  [MeetBot Daemon] 🔴 ERROR: Meeting ended, but Google generated"
                            " no transcript. You forgot to click 'Start Transcription'"
                            " in the Meet UI!"
                        )
                        return

                    print("\n  [MeetBot Daemon] 🟢 Native transcript pulled from Google.")

                    # Autonomously inject the transcript into the agent's long-term brain
                    from tokoboros.core.memory_vault import MemoryVault

                    vault = MemoryVault()
                    vault.upsert_fact(
                        f"Transcript for meeting {space_id}:\n{transcript_text}", "workspace_state"
                    )

                    print("  [MeetBot Daemon] 🧠 Transcript securely saved to Memory Vault.")
                    print(
                        "  [MeetBot Daemon] (You can now ask Tokoboros to summarize"
                        " or email the notes.)\nYou: ",
                        end="",
                        flush=True,
                    )

                import uuid as _uuid

                from tokoboros.jobs.queue import InMemoryJobQueue, Job, JobWorker

                _q = InMemoryJobQueue()
                _q.enqueue(
                    Job(
                        job_id=_uuid.uuid4().hex,
                        name=f"meet_transcript:{space_id}",
                        func=_poll_for_transcript,
                        job_type="background",
                    )
                )
                _worker = JobWorker(_q)
                threading.Thread(
                    target=_worker.execute_one, daemon=True, name=f"meet:{space_id}"
                ).start()
                return (
                    f"SUCCESS: Native Workspace API detected. Monitoring {space_id} silently"
                    " in the background. CRITICAL REQUIREMENT: The user MUST physically click"
                    " 'Activities > Transcripts > Start Transcription' in the Google Meet UI"
                    " during the call, otherwise no notes will be generated."
                )

            except httpx.HTTPStatusError as e:
                # API Failed (Usually because of free tier or invalid link)
                if e.response.status_code in [403, 401]:
                    return (
                        "ERROR: Google Meet API access denied. Native meeting transcription"
                        " requires a paid Google Workspace tier (Business/Enterprise)."
                        " Local fallback has been disabled by the user."
                    )
                return (
                    f"ERROR: Google Meet API failed with status {e.response.status_code}."
                    " Make sure the meet link is valid."
                )
            except Exception as e:
                return f"Failed to monitor meeting: {str(e)}"

        registry.register(
            {
                "name": "google_monitor_meeting",
                "description": (
                    "Take notes, transcribe, or monitor an ongoing/upcoming Google Meet."
                    " Always use this if asked to join or record a meeting."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "meet_link": {
                            "type": "string",
                            "description": (
                                "The full Google Meet URL"
                                " (e.g., https://meet.google.com/abc-defg-hij)"
                            ),
                        }
                    },
                    "required": ["meet_link"],
                },
            },
            google_monitor_meeting,
        )
