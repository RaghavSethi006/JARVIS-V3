import logging
from typing import Any

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("media_agent: core.logger import failed: %s", exc)

try:
    from interfaces.agent import BaseAgent
except ImportError as exc:
    BaseAgent = None
    logger.warning("media_agent: BaseAgent import failed: %s", exc)


class MediaAgent(BaseAgent or object):
    """Handles media playback, Spotify control, and downloads."""

    NAME = "MediaAgent"
    SYSTEM_PROMPT = "You handle media playback for JARVIS."

    def __init__(self, bus: Any):
        if BaseAgent:
            super().__init__(bus)
        else:
            self.bus = bus
            logger.warning("MediaAgent: BaseAgent unavailable; running in fallback mode.")

    async def handle(self, task: dict) -> dict:
        """Execute a media task."""
        action = (task or {}).get("action", "")
        params = (task or {}).get("params", {}) or {}

        if action == "play_media":
            platform = (params.get("platform") or "spotify").strip().lower()
            query = (params.get("query") or "").strip()
            if platform == "youtube":
                action = "play_youtube"
            else:
                action = "spotify_play"
            params = {"query": query}

        if action == "spotify_play":
            return await self._run_tool_event(
                "spotify_play",
                {"query": params.get("query", "")},
                fallback_speech="Playing on Spotify.",
            )

        if action == "spotify_pause":
            return await self._run_tool_event(
                "spotify_pause",
                {},
                fallback_speech="Pausing Spotify.",
            )

        if action == "spotify_next":
            return await self._run_tool_event(
                "spotify_next",
                {},
                fallback_speech="Skipping to the next track.",
            )

        if action == "spotify_prev":
            return await self._run_tool_event(
                "spotify_prev",
                {},
                fallback_speech="Going back to the previous track.",
            )

        if action == "spotify_volume":
            level = int(params.get("value", 50))
            return await self._run_tool_event(
                "spotify_volume",
                {"value": level},
                fallback_speech="Adjusting Spotify volume.",
            )

        if action == "play_youtube":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("MediaAgent: Missing YouTube query.")
            return await self._run_tool_event(
                "play_youtube",
                {"query": query},
                fallback_speech=f"Playing {query} on YouTube.",
            )

        if action == "search_youtube":
            query = (params.get("query") or "").strip()
            if not query:
                return self._err("MediaAgent: Missing YouTube search query.")
            return await self._run_tool_event(
                "search_youtube",
                {"query": query},
                fallback_speech=f"Searching YouTube for {query}.",
            )

        if action == "download_video":
            url = (params.get("url") or "").strip()
            if not url:
                return self._err("MediaAgent: Missing download URL.")
            return await self._run_tool_event(
                "download_video",
                {"url": url},
                fallback_speech="Starting the download.",
            )

        return self._err(f"MediaAgent: Unknown action {action}")

    def _ok(self, result: Any = None, speech: str = "") -> dict:
        if BaseAgent:
            return super()._ok(result=result, speech=speech)
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        if BaseAgent:
            return super()._err(message)
        return {"status": "error", "result": None, "speech": message}
