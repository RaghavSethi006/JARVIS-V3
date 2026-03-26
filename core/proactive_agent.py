"""
core/proactive_agent.py

Background agent that monitors for things worth surfacing to the user
without being asked. Runs on a timer while the app is open.
"""

import asyncio
from datetime import datetime
from core.logger import logger

try:
    from core.event_bus import EventBus
except ImportError:
    EventBus = None
    logger.warning("ProactiveAgent: EventBus not available. Proactive agent disabled.")

try:
    from core.memory import MemoryManager
except ImportError:
    MemoryManager = None
    logger.warning("ProactiveAgent: MemoryManager not available. Proactive agent disabled.")


CHECK_INTERVAL_SECONDS = 300  # 5 minutes


class ProactiveAgent:
    """Monitors for proactive prompts such as open threads or upcoming events."""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.memory = MemoryManager.get() if MemoryManager is not None else None
        self.running = False
        self._last_checks: dict[str, datetime] = {}

    async def start(self) -> None:
        """Start the proactive loop until stopped."""
        if self.bus is None or self.memory is None:
            logger.warning("ProactiveAgent: Dependencies missing. Not starting.")
            return
        self.running = True
        logger.info("ProactiveAgent: Started.")
        while self.running:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            if self.running:
                await self._run_checks()

    def stop(self) -> None:
        """Stop the proactive loop."""
        self.running = False

    async def _run_checks(self) -> None:
        await self._check_upcoming_calendar()
        await self._check_open_threads()

    async def _check_upcoming_calendar(self) -> None:
        """Surface calendar events in the next 60 minutes."""
        try:
            await self.bus.emit("get_calendar_silent", {"count": 5, "proactive": True})
        except Exception as e:
            logger.debug(f"ProactiveAgent: Calendar check failed: {e}")

    async def _check_open_threads(self) -> None:
        """
        Periodically surface open entity threads that have not been
        addressed in a while.
        """
        if self.memory is None or not hasattr(self.memory, "entity_store"):
            return

        try:
            all_entities = self.memory.entity_store.get_all_entities()
            for entity in all_entities[:10]:
                threads = self.memory.entity_store.get_open_threads(entity["id"])
                for thread in threads[:1]:
                    thread_key = f"thread_{thread['id']}"
                    last_check = self._last_checks.get(thread_key)
                    if last_check and (datetime.now() - last_check).days < 3:
                        continue

                    self._last_checks[thread_key] = datetime.now()
                    msg = f"By the way - {thread['question']}"
                    await self.bus.emit("tts_speak", msg)
                    await self.bus.emit("add_jarvis_response", msg)
                    break
        except Exception as e:
            logger.debug(f"ProactiveAgent: Thread check failed: {e}")
