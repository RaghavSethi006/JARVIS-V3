import logging
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("comms_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("comms_agent: BaseAgent import failed: %s", exc)


class CommsAgent(BaseAgent or object):
    """Handles communication tasks like email, WhatsApp, and calendar events."""

    NAME = "CommsAgent"
    SYSTEM_PROMPT = "You handle communications for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            logger.warning("CommsAgent: BaseAgent unavailable; running in fallback mode.")

    async def handle(self, task: dict) -> dict:
        """Execute a communications task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "send_email":
            receiver = (params.get("receiver") or params.get("to") or "").strip()
            subject = (params.get("subject") or "").strip()
            body = (params.get("body") or params.get("message") or "").strip()
            await self.bus.emit("send_email", {"receiver": receiver, "subject": subject, "body": body})
            return self._ok(speech=f"Sending email to {receiver}.")

        if action == "read_emails":
            count = int(params.get("count", 5))
            await self.bus.emit("read_emails", {"count": count})
            return self._ok(speech="Reading your emails.")

        if action == "send_whatsapp":
            contact = (params.get("contact") or "").strip()
            message = (params.get("message") or "").strip()
            await self.bus.emit("send_whatsapp", {"contact": contact, "message": message})
            return self._ok(speech=f"Sending a WhatsApp message to {contact}.")

        if action == "get_calendar":
            count = int(params.get("count", 5))
            await self.bus.emit("get_calendar", {"count": count})
            return self._ok(speech="Checking your calendar.")

        if action == "create_event":
            await self.bus.emit(
                "create_event",
                {
                    "title": params.get("title", ""),
                    "start": params.get("start", ""),
                    "end": params.get("end", ""),
                },
            )
            return self._ok(speech="Creating the calendar event.")

        if action == "schedule_meeting":
            await self.bus.emit(
                "schedule_meeting",
                {
                    "title": params.get("title", ""),
                    "start": params.get("start", ""),
                    "end": params.get("end", ""),
                },
            )
            return self._ok(speech="Scheduling the meeting.")

        return self._err(f"CommsAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
