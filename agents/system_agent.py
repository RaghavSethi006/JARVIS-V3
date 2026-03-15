import logging
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("system_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("system_agent: BaseAgent import failed: %s", exc)


class SystemAgent(BaseAgent or object):
    """Handles system and window management tasks."""

    NAME = "SystemAgent"
    SYSTEM_PROMPT = "You handle system controls for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            logger.warning("SystemAgent: BaseAgent unavailable; running in fallback mode.")

    async def handle(self, task: dict) -> dict:
        """Execute a system task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "open_app":
            app = (params.get("app") or "").strip()
            if not app:
                return self._err("SystemAgent: Missing app name.")
            await self.bus.emit("system_launch", {"app": app})
            return self._ok(speech=f"Opening {app}.")

        if action == "close_app":
            app = (params.get("app") or "").strip()
            if not app:
                return self._err("SystemAgent: Missing app name.")
            await self.bus.emit("system_close", {"app": app})
            return self._ok(speech=f"Closing {app}.")

        if action == "set_volume":
            value = int(params.get("value", 10))
            direction = (params.get("direction") or "up").strip().lower()
            if direction not in {"up", "down", "mute", "set"}:
                direction = "up"
            payload = {"action": direction, "value": value}
            await self.bus.emit("set_volume", payload)
            return self._ok(speech="Adjusting volume.")

        if action == "set_brightness":
            value = int(params.get("value", 20))
            direction = (params.get("direction") or "up").strip().lower()
            if direction not in {"up", "down", "set"}:
                direction = "up"
            payload = {"action": direction, "value": value}
            await self.bus.emit("set_brightness", payload)
            return self._ok(speech="Adjusting brightness.")

        if action == "minimize_window":
            await self.bus.emit("minimize_window", {})
            return self._ok(speech="Minimizing window.")

        if action == "maximize_window":
            await self.bus.emit("maximize_window", {})
            return self._ok(speech="Maximizing window.")

        if action == "take_screenshot":
            await self.bus.emit("take_screenshot", {})
            return self._ok(speech="Taking a screenshot.")

        if action == "search_file":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("SystemAgent: Missing file query.")
            await self.bus.emit("search_file", {"query": query})
            return self._ok(speech=f"Searching for {query}.")

        if action == "tell_joke":
            await self.bus.emit("tell_joke", {})
            return self._ok(speech="Here's a joke.")

        return self._err(f"SystemAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
