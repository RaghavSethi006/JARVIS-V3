from core.event_bus import EventBus
from abc import ABC, abstractmethod

class BaseAdapter(ABC):
    def __init__(self, bus: EventBus):
        self.bus = bus

    @abstractmethod
    async def start(self):
        """Start the adapter loop"""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop the adapter"""
        pass
