import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

try:
    from core.agent_feedback import capture_tool_feedback
except ImportError:
    from contextlib import contextmanager

    @contextmanager
    def capture_tool_feedback():
        class _FallbackCapture:
            messages: list[str] = []

        yield _FallbackCapture()

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("interfaces.agent: core.logger import failed: %s", exc)

try:
    from core.event_bus import EventBus
except ImportError as exc:
    EventBus = Any
    logger.warning("interfaces.agent: core.event_bus import failed: %s", exc)

try:
    from core.llm_client import LLMClient
except ImportError as exc:
    LLMClient = None
    logger.warning("interfaces.agent: core.llm_client import failed: %s", exc)


class BaseAgent(ABC):
    """
    Base class for all specialist agents.

    Each agent has a system prompt, tool set, and its own context window.
    """

    NAME = "BaseAgent"
    SYSTEM_PROMPT = ""
    FAILURE_MARKERS = (
        "not configured",
        "not connected",
        "couldn't",
        "could not",
        "failed",
        "unavailable",
        "disabled",
        "not found",
        "please provide",
        "i need ",
        "need a ",
        "need an ",
        "missing ",
    )

    def __init__(self, bus: EventBus):
        """Initialize the agent with an EventBus and optional LLM client."""
        self.bus = bus
        self.llm = LLMClient.get() if LLMClient is not None else None
        if self.llm is None:
            logger.warning("%s: LLM client unavailable; LLM features disabled.", self.NAME)
        self._context: list[dict] = []

    @abstractmethod
    async def handle(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task and return a result dict.

        Expected return format:
        {"status": "ok|error", "result": any, "speech": "..."}
        """
        raise NotImplementedError("Agent must implement handle().")

    def _ok(self, result: Optional[Any] = None, speech: str = "") -> Dict[str, Any]:
        """Return a success payload."""
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> Dict[str, Any]:
        """Return an error payload."""
        return {"status": "error", "result": None, "speech": message}

    async def _run_tool_event(
        self,
        event_name: str,
        payload: Optional[Dict[str, Any]] = None,
        fallback_speech: str = "",
        failure_markers: Optional[tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        """Emit a tool event and capture any skill feedback produced during execution."""
        try:
            with capture_tool_feedback() as capture:
                await self.bus.emit(event_name, payload or {})
            messages = self._normalize_feedback(getattr(capture, "messages", []))
            speech = self._summarize_feedback(messages) or fallback_speech
            if self._looks_like_failure(messages, failure_markers):
                return self._err(speech or f"{self.NAME}: {event_name} failed.")
            return self._ok(
                result={"event": event_name, "messages": messages},
                speech=speech or fallback_speech,
            )
        except Exception as exc:
            logger.error("%s: tool event %s failed: %s", self.NAME, event_name, exc)
            return self._err(f"{self.NAME}: {event_name} failed.")

    def _normalize_feedback(self, messages: list[str]) -> list[str]:
        """Deduplicate feedback messages while preserving order."""
        normalized: list[str] = []
        seen: set[str] = set()
        for message in messages:
            text = str(message or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    def _summarize_feedback(self, messages: list[str]) -> str:
        """Collapse captured tool feedback into a concise summary for synthesis."""
        if not messages:
            return ""

        progress_prefixes = (
            "fetching ",
            "opening ",
            "starting ",
            "searching ",
            "sending ",
            "checking ",
            "trying to ",
            "launching ",
            "adjusting ",
            "setting ",
            "resuming ",
            "playing ",
        )
        filtered = [
            message
            for message in messages
            if not message.lower().startswith(progress_prefixes)
        ]
        relevant = filtered or messages
        return " ".join(relevant[:3])

    def _looks_like_failure(
        self,
        messages: list[str],
        failure_markers: Optional[tuple[str, ...]] = None,
    ) -> bool:
        """Infer whether captured tool feedback indicates a failed task."""
        if not messages:
            return False

        markers = tuple(m.lower() for m in (failure_markers or self.FAILURE_MARKERS))
        return any(marker in message.lower() for marker in markers for message in messages)
