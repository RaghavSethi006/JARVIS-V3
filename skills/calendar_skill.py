"""Google Calendar integration for reading and creating events."""

import asyncio
import datetime
import os
import uuid

from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "token.json")
CREDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "credentials.json")


class CalendarSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self._service = None
        self._init_calendar()

    def _init_calendar(self):
        if not os.path.exists(CREDS_PATH):
            logger.warning("Google Calendar credentials.json not found. CalendarSkill disabled.")
            return
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None
            if os.path.exists(TOKEN_PATH):
                creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(TOKEN_PATH, "w", encoding="utf-8") as fh:
                    fh.write(creds.to_json())
            self._service = build("calendar", "v3", credentials=creds)
        except Exception as exc:
            logger.error("Calendar init failed: %s", exc)

    def register(self):
        self.bus.subscribe("get_calendar", self.handle_get_events)
        self.bus.subscribe("create_event", self.handle_create_event)
        self.bus.subscribe("schedule_meeting", self.handle_schedule_meeting)

    async def handle_get_events(self, event: Event):
        count = int((event.data or {}).get("count", 5))
        if not self._service:
            await self.bus.emit("tts_speak", "Google Calendar is not connected.")
            return
        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(None, self._fetch_events, count)
        if not events:
            await self.bus.emit("tts_speak", "No upcoming events.")
            return
        await self.bus.emit("tts_speak", f"You have {len(events)} upcoming events.")
        for item in events[:3]:
            start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            summary = item.get("summary", "Untitled")
            await self.bus.emit("tts_speak", f"{summary} at {start}.")

    def _fetch_events(self, count: int) -> list:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        result = (
            self._service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=count,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return result.get("items", [])

    async def handle_create_event(self, event: Event):
        data = event.data or {}
        title = data.get("title", "Meeting")
        start_dt = data.get("start")
        end_dt = data.get("end")
        if not self._service or not start_dt:
            await self.bus.emit("tts_speak", "I need a time to create the event.")
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._create_event, title, start_dt, end_dt)
        await self.bus.emit("tts_speak", f"Event '{title}' created.")

    def _create_event(self, title: str, start: str, end: str | None):
        end = end or start
        (
            self._service.events()
            .insert(
                calendarId="primary",
                body={
                    "summary": title,
                    "start": {"dateTime": start, "timeZone": "UTC"},
                    "end": {"dateTime": end, "timeZone": "UTC"},
                },
            )
            .execute()
        )

    async def handle_schedule_meeting(self, event: Event):
        data = event.data or {}
        if not self._service:
            await self.bus.emit("tts_speak", "Google Calendar is not connected.")
            return
        loop = asyncio.get_event_loop()
        link = await loop.run_in_executor(None, self._create_meet, data)
        if link:
            await self.bus.emit("tts_speak", "Google Meet scheduled.")
            await self.bus.emit("show_meet_link", {"link": link})
        else:
            await self.bus.emit("tts_speak", "Couldn't schedule the meeting.")

    def _create_meet(self, data: dict) -> str:
        try:
            event = (
                self._service.events()
                .insert(
                    calendarId="primary",
                    conferenceDataVersion=1,
                    body={
                        "summary": data.get("title", "Jarvis Meeting"),
                        "start": {"dateTime": data.get("start"), "timeZone": "UTC"},
                        "end": {"dateTime": data.get("end") or data.get("start"), "timeZone": "UTC"},
                        "conferenceData": {
                            "createRequest": {
                                "requestId": f"jarvis-meet-{uuid.uuid4()}",
                                "conferenceSolutionKey": {"type": "hangoutsMeet"},
                            }
                        },
                    },
                )
                .execute()
            )
            return event.get("hangoutLink", "")
        except Exception as exc:
            logger.error("Meet creation failed: %s", exc)
            return ""
