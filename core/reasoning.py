"""
core/reasoning.py

Pre-response reasoning layer for complex inputs.
Produces an internal scratchpad (never spoken) that improves
response quality on multi-step, ambiguous, or sensitive requests.
"""

from core.logger import logger

try:
    from core.llm_client import LLMClient
    _LLM_AVAILABLE = True
except ImportError:
    LLMClient = None
    _LLM_AVAILABLE = False
    logger.warning("ReasoningLayer: LLMClient not available. Reasoning disabled.")


REASONING_PROMPT = """
You are the internal reasoning module of JARVIS.
Before JARVIS responds to the user, think through:

1. What exactly is the user asking for?
2. Is any information missing that would change the response?
3. Does this require multiple steps? If so, list them in order.
4. Does anything in memory context contradict or inform this request?
5. What is the most helpful response strategy?

Think briefly (3-6 sentences). This is an internal scratchpad - it will NOT be
shown to the user. It informs the final response only.

User input: {user_input}
Memory context: {memory_context}
"""

# Only trigger reasoning for complex inputs
COMPLEXITY_SIGNALS = [
    "and", "then", "after that", "also", "plus",
    "schedule", "plan", "remind", "every", "set up",
    "how do i", "what should", "help me", "can you",
    "why", "explain", "compare", "difference between",
]


class ReasoningLayer:
    """Pre-response reasoning layer for complex inputs."""

    def __init__(self) -> None:
        self.llm = LLMClient.get() if _LLM_AVAILABLE else None

    def needs_reasoning(self, user_input: str) -> bool:
        """Heuristic: only run reasoning pass on complex inputs."""
        lower = user_input.lower()
        signal_count = sum(1 for s in COMPLEXITY_SIGNALS if s in lower)
        word_count = len(user_input.split())
        return signal_count >= 2 or word_count > 12

    async def reason(self, user_input: str, memory_context: str = "") -> str:
        """
        Produces internal reasoning. Returns the scratchpad text.
        Caller decides whether to inject it into the system prompt.
        """
        if self.llm is None:
            logger.debug("ReasoningLayer: LLM unavailable. Skipping reasoning pass.")
            return ""
        if not self.needs_reasoning(user_input):
            return ""
        try:
            prompt = REASONING_PROMPT.format(
                user_input=user_input,
                memory_context=memory_context or "No relevant memory context.",
            )
            scratchpad = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system="You are an internal reasoning module. Be precise and brief.",
                max_tokens=300,
                temperature=0.3,
            )
            logger.debug(f"ReasoningLayer scratchpad: {scratchpad[:100]}...")
            return scratchpad
        except Exception as e:
            logger.error(f"ReasoningLayer: {e}")
            return ""
