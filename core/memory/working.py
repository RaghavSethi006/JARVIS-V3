"""
Working Memory — active in-session context.
Holds the current conversation with rich annotations,
active task/goal state, and detected emotional tone.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json


@dataclass
class AnnotatedExchange:
    """A single exchange in a conversation with metadata annotations."""
    role: str                          # "user" | "jarvis"
    content: str
    timestamp: str
    intent: Optional[str] = None       # "get_weather", "get_info", etc.
    entities_mentioned: list = field(default_factory=list)
    emotional_tone: Optional[str] = None  # "neutral", "frustrated", "rushed"


@dataclass
class WorkingMemory:
    """
    Active session context. Tracks exchanges, current task/goal,
    active entities, and user mood within a single session.
    """
    session_id: str
    session_start: str
    exchanges: list[AnnotatedExchange] = field(default_factory=list)
    current_task: Optional[str] = None
    current_goal: Optional[str] = None
    active_entities: list[str] = field(default_factory=list)
    user_mood: str = "neutral"
    token_count: int = 0

    MAX_EXCHANGES: int = 40
    MAX_TOKENS: int = 4000

    def add_exchange(self, role: str, content: str, **kwargs) -> None:
        """Add a new exchange to working memory, trimming if over limit."""
        exchange = AnnotatedExchange(
            role=role,
            content=content,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )
        self.exchanges.append(exchange)
        if len(self.exchanges) > self.MAX_EXCHANGES:
            self.exchanges = self.exchanges[-self.MAX_EXCHANGES:]

    def to_messages(self) -> list[dict]:
        """Convert to LLM API messages format."""
        return [
            {"role": e.role if e.role != "jarvis" else "assistant", "content": e.content}
            for e in self.exchanges
        ]

    def to_context_string(self) -> str:
        """Human-readable context block for system prompt injection."""
        if not self.exchanges:
            return ""
        lines = ["[CURRENT SESSION CONTEXT]"]
        if self.current_task:
            lines.append(f"Current task: {self.current_task}")
        if self.active_entities:
            lines.append(f"Active topics: {', '.join(self.active_entities)}")
        lines.append(f"User mood: {self.user_mood}")
        return "\n".join(lines)

    def serialize(self) -> str:
        """Serialize working memory to JSON string for snapshot storage."""
        return json.dumps({
            "session_id": self.session_id,
            "session_start": self.session_start,
            "exchanges": [
                {
                    "role": e.role,
                    "content": e.content,
                    "timestamp": e.timestamp,
                    "intent": e.intent,
                    "entities_mentioned": e.entities_mentioned,
                    "emotional_tone": e.emotional_tone,
                }
                for e in self.exchanges
            ],
            "current_task": self.current_task,
            "current_goal": self.current_goal,
            "active_entities": self.active_entities,
            "user_mood": self.user_mood,
        })

    @classmethod
    def deserialize(cls, data: str) -> "WorkingMemory":
        """Reconstruct WorkingMemory from a serialized JSON string."""
        d = json.loads(data)
        wm = cls(session_id=d["session_id"], session_start=d["session_start"])
        wm.current_task = d.get("current_task")
        wm.current_goal = d.get("current_goal")
        wm.active_entities = d.get("active_entities", [])
        wm.user_mood = d.get("user_mood", "neutral")
        for e in d.get("exchanges", []):
            wm.exchanges.append(AnnotatedExchange(
                role=e["role"],
                content=e["content"],
                timestamp=e["timestamp"],
                intent=e.get("intent"),
                entities_mentioned=e.get("entities_mentioned", []),
                emotional_tone=e.get("emotional_tone"),
            ))
        return wm
