from core.event_bus import EventBus
from abc import ABC, abstractmethod

class BaseSkill(ABC):
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.name = self.__class__.__name__

    @abstractmethod
    def register(self):
        """Register event listeners here."""
        pass
