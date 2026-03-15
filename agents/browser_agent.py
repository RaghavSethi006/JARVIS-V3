import logging
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("browser_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("browser_agent: BaseAgent import failed: %s", exc)


class BrowserAgent(BaseAgent or object):
    """Handles browser automation tasks and tab management."""

    NAME = "BrowserAgent"
    SYSTEM_PROMPT = "You handle browser tasks for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            logger.warning("BrowserAgent: BaseAgent unavailable; running in fallback mode.")

    async def handle(self, task: dict) -> dict:
        """Execute a browser task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "open_url":
            url = (params.get("url") or params.get("site") or "").strip()
            if not url:
                return self._err("BrowserAgent: Missing URL.")
            await self.bus.emit("browser_open", {"url": url})
            return self._ok(speech=f"Opening {url}.")

        if action == "search_web":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("BrowserAgent: Missing search query.")
            await self.bus.emit("browser_search", {"query": query})
            return self._ok(speech=f"Searching for {query}.")

        if action == "browser_new_tab":
            await self.bus.emit("browser_new_tab", {})
            return self._ok(speech="Opening a new tab.")

        if action == "browser_close_tab":
            await self.bus.emit("browser_close_tab", {})
            return self._ok(speech="Closing the tab.")

        if action == "browser_next_tab":
            await self.bus.emit("browser_next_tab", {})
            return self._ok(speech="Switching to the next tab.")

        if action == "browser_prev_tab":
            await self.bus.emit("browser_prev_tab", {})
            return self._ok(speech="Switching to the previous tab.")

        if action == "browser_close":
            await self.bus.emit("browser_close", {})
            return self._ok(speech="Closing the browser.")

        return self._err(f"BrowserAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
