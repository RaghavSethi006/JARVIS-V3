import asyncio
from core.event_bus import EventBus
import importlib
import os
import sys

class JarvisEngine:
    def __init__(self):
        self.bus = EventBus()
        self.running = False

    async def start(self):
        self.running = True
        print("Jarvis Engine Started")
        await self.bus.emit("startup")
        
        # Keep the loop alive
        while self.running:
            await asyncio.sleep(1)

    def stop(self):
        self.running = False

    def load_skills(self):
        # Dynamic skill loading logic would go here
        # For now, we will manually register
        pass
