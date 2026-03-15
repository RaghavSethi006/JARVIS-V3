import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

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
