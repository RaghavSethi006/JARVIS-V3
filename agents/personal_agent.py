import logging
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("personal_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("personal_agent: BaseAgent import failed: %s", exc)


class PersonalAgent(BaseAgent or object):
    """Handles personal tasks like alarms, reminders, biometrics, and gestures."""

    NAME = "PersonalAgent"
    SYSTEM_PROMPT = "You handle personal tasks for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            logger.warning("PersonalAgent: BaseAgent unavailable; running in fallback mode.")

    async def handle(self, task: dict) -> dict:
        """Execute a personal task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "set_alarm":
            time_text = (params.get("time_text") or params.get("time") or "").strip()
            if not time_text:
                return self._err("PersonalAgent: Missing alarm time.")
            await self.bus.emit("set_alarm", {"time_text": time_text})
            return self._ok(speech=f"Setting an alarm for {time_text}.")

        if action == "set_reminder":
            task_text = (params.get("task") or "").strip()
            time_text = (params.get("time") or params.get("time_text") or "").strip()
            await self.bus.emit("set_reminder", {"task": task_text, "time_text": time_text})
            return self._ok(speech="Setting a reminder.")

        if action == "auth_login":
            await self.bus.emit("auth_login", {})
            return self._ok(speech="Starting face login.")

        if action == "auth_register":
            await self.bus.emit("auth_register", {})
            return self._ok(speech="Starting face registration.")

        if action == "toggle_gesture_control":
            await self.bus.emit("toggle_gesture_control", {})
            return self._ok(speech="Toggling gesture control.")

        return self._err(f"PersonalAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
