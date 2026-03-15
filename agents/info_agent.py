import logging
from datetime import datetime
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("info_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("info_agent: BaseAgent import failed: %s", exc)


class InfoAgent(BaseAgent or object):
    """Handles information retrieval tasks such as weather, news, and general queries."""

    NAME = "InfoAgent"
    SYSTEM_PROMPT = "You handle information retrieval for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            self.llm = None
            logger.warning("InfoAgent: BaseAgent unavailable; LLM features disabled.")

    async def handle(self, task: dict) -> dict:
        """Execute a single info task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "get_weather":
            location = (params.get("location") or params.get("city") or "").strip()
            await self.bus.emit("check_weather", {"city": location})
            return self._ok(speech=f"Checking weather for {location or 'your default city'}.")

        if action == "get_time":
            now = datetime.now().strftime("%I:%M %p")
            return self._ok(speech=f"It's {now}.")

        if action == "get_news":
            source = (params.get("source") or "bbc-news").strip()
            await self.bus.emit("get_news", {"source": source})
            return self._ok(speech=f"Fetching news from {source}.")

        if action == "get_info":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("InfoAgent: Missing query.")
            if not getattr(self, "llm", None):
                return self._err("LLM is unavailable for general questions.")
            response = await self.llm.complete(
                messages=[{"role": "user", "content": query}],
                system=self.SYSTEM_PROMPT,
                max_tokens=300,
                temperature=0.7,
            )
            return self._ok(speech=response)

        return self._err(f"InfoAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        """Local ok helper for fallback mode."""
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        """Local error helper for fallback mode."""
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
