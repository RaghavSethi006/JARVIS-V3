import asyncio
from typing import Callable, Dict, List, Any
from dataclasses import dataclass
from core.logger import logger

try:
    from core.agent_feedback import is_tool_feedback_suppressed, record_tool_feedback
except ImportError:
    def is_tool_feedback_suppressed() -> bool:
        return False

    def record_tool_feedback(message: object) -> None:
        return None

@dataclass
class Event:
    name: str
    data: Any = None

class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable[[Event], Any]]] = {}

    def subscribe(self, event_name: str, callback: Callable[[Event], Any]):
        if event_name not in self.subscribers:
            self.subscribers[event_name] = []
        self.subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Callable[[Event], Any]):
        if event_name in self.subscribers:
            if callback in self.subscribers[event_name]:
                self.subscribers[event_name].remove(callback)

    async def emit(self, event_name: str, data: Any = None):
        event = Event(name=event_name, data=data)
        if event_name in {"tts_speak", "add_jarvis_response"} and is_tool_feedback_suppressed():
            record_tool_feedback(data)
            return
        if event_name in self.subscribers:
            for callback in self.subscribers[event_name]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(event)
                    else:
                        callback(event)
                except Exception as e:
                    logger.error(f"EventBus: subscriber {callback.__name__} raised {e}")
