from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import asyncio
import threading

class MediaControlSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.driver = None
        self.lock = threading.Lock()

    def register(self):
        self.bus.subscribe("play_youtube", self.handle_play_youtube)
        self.bus.subscribe("search_youtube", self.handle_search_youtube)

    def _ensure_driver(self):
        with self.lock:
            if self.driver is None:
                try:
                    self.driver = webdriver.Chrome()
                    self.driver.get("https://www.youtube.com")
                except Exception as e:
                    print(f"Driver init error: {e}")
                    return False
        return True

    async def handle_play_youtube(self, event: Event):
        await self.handle_play(event)

    async def handle_play(self, event: Event):
        query = event.data.get("query")
        if not query:
            return
        
        await self.bus.emit("tts_speak", f"Playing {query} on YouTube.")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._play_sync, query)
        except Exception as e:
            logger.error(f"Media control error: {e}")
            await self.bus.emit("tts_speak", "Couldn't play that. Check if Chrome is available.")

    def _play_sync(self, query: str):
        """Synchronous Selenium work - runs in executor."""
        self._ensure_driver()
        if not self.driver:
            return
            
        try:
            self.driver.get("https://www.youtube.com")
            search_box = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "search_query"))
            )
            search_box.clear()
            search_box.send_keys(query)
            search_box.send_keys(Keys.RETURN)
            first_video = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "video-title"))
            )
            first_video.click()
        except Exception as e:
            raise RuntimeError(f"YouTube play error: {e}") from e

    async def handle_search_youtube(self, event: Event):
        query = event.data.get("query")
        if not query:
            return
        
        await self.bus.emit("tts_speak", f"Searching YouTube for {query}.")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._search, query)

    def _search(self, query):
        self._ensure_driver()
        if not self.driver:
            return
            
        try:
            self.driver.get("https://www.youtube.com")
            search_box = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "search_query"))
            )
            search_box.clear()
            search_box.send_keys(query)
            search_box.send_keys(Keys.RETURN)
        except Exception as e:
            print(f"YouTube search error: {e}")
