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
            return await self._run_tool_event(
                "system_launch",
                {"app": app},
                fallback_speech=f"Opening {app}.",
            )

        if action == "close_app":
            app = (params.get("app") or "").strip()
            if not app:
                return self._err("SystemAgent: Missing app name.")
            return await self._run_tool_event(
                "system_close",
                {"app": app},
                fallback_speech=f"Closing {app}.",
            )

        if action in {"set_volume", "volume_up", "volume_down", "mute"}:
            value = int(params.get("value", 10))
            direction = (params.get("direction") or "").strip().lower()
            if action == "volume_up":
                direction = "up"
            elif action == "volume_down":
                direction = "down"
            elif action == "mute":
                direction = "mute"
            if not direction:
                direction = "up"
            if direction not in {"up", "down", "mute", "set"}:
                direction = "up"
            payload = {"action": direction, "value": value}
            return await self._run_tool_event(
                "set_volume",
                payload,
                fallback_speech="Adjusting volume.",
            )

        if action in {"set_brightness", "brightness_up", "brightness_down"}:
            value = int(params.get("value", 20))
            direction = (params.get("direction") or "").strip().lower()
            if action == "brightness_up":
                direction = "up"
            elif action == "brightness_down":
                direction = "down"
            if not direction:
                direction = "up"
            if direction not in {"up", "down", "set"}:
                direction = "up"
            payload = {"action": direction, "value": value}
            return await self._run_tool_event(
                "set_brightness",
                payload,
                fallback_speech="Adjusting brightness.",
            )

        if action == "minimize_window":
            return await self._run_tool_event(
                "minimize_window",
                {},
                fallback_speech="Minimizing window.",
            )

        if action == "maximize_window":
            return await self._run_tool_event(
                "maximize_window",
                {},
                fallback_speech="Maximizing window.",
            )

        if action == "take_screenshot":
            return await self._run_tool_event(
                "take_screenshot",
                {},
                fallback_speech="Taking a screenshot.",
            )

        if action == "search_file":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("SystemAgent: Missing file query.")
            return await self._run_tool_event(
                "search_file",
                {"query": query},
                fallback_speech=f"Searching for {query}.",
            )

        if action == "tell_joke":
            return await self._run_tool_event(
                "tell_joke",
                {},
                fallback_speech="Here's a joke.",
            )

        return self._err(f"SystemAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
