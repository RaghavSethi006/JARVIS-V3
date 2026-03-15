from interfaces.skill import BaseSkill
from core.event_bus import Event
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import asyncio
import threading

class BrowserControlSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.driver = None
        self.lock = threading.Lock()

    def register(self):
        self.bus.subscribe("browser_open", self.handle_open)
        self.bus.subscribe("browser_search", self.handle_search)
        self.bus.subscribe("browser_new_tab", self.handle_new_tab)
        self.bus.subscribe("browser_close_tab", self.handle_close_tab)
        self.bus.subscribe("browser_next_tab", self.handle_next_tab)
        self.bus.subscribe("browser_prev_tab", self.handle_prev_tab)
        self.bus.subscribe("browser_close", self.handle_close)

    def _ensure_driver(self):
        with self.lock:
            if self.driver is None:
                try:
                    self.driver = webdriver.Chrome()
                    self.driver.get("https://www.google.com")
                except Exception as e:
                    print(f"Browser init error: {e}")
                    return False
        return True

    async def handle_open(self, event: Event):
        loop = asyncio.get_event_loop()
        url = event.data.get("url", "https://www.google.com")
        if not url.startswith("http"):
            url = "https://" + url
        
        await self.bus.emit("tts_speak", "Opening browser.")
        await loop.run_in_executor(None, self._open_url, url)

    def _open_url(self, url):
        self._ensure_driver()
        if self.driver:
            self.driver.get(url)

    async def handle_search(self, event: Event):
        query = event.data.get("query")
        if not query:
            return
        
        loop = asyncio.get_event_loop()
        await self.bus.emit("tts_speak", f"Searching for {query}.")
        await loop.run_in_executor(None, self._search, query)

    def _search(self, query):
        self._ensure_driver()
        if self.driver:
            try:
                self.driver.get("https://www.google.com")
                search_box = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "q"))
                )
                search_box.clear()
                search_box.send_keys(query)
                search_box.submit()
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "search"))
                )
            except Exception as e:
                print(f"Search error: {e}")

    async def handle_new_tab(self, event: Event):
        if not self.driver:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self.driver.execute_script("window.open('about:blank', '_blank');")
        )
        await loop.run_in_executor(
            None, lambda: self.driver.switch_to.window(self.driver.window_handles[-1])
        )
        await self.bus.emit("tts_speak", "New tab opened.")

    async def handle_close_tab(self, event: Event):
        if not self.driver or len(self.driver.window_handles) <= 1:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.driver.close)
        await loop.run_in_executor(
            None, lambda: self.driver.switch_to.window(self.driver.window_handles[-1])
        )
        await self.bus.emit("tts_speak", "Tab closed.")

    async def handle_next_tab(self, event: Event):
        if not self.driver:
            return
        handles = self.driver.window_handles
        current = handles.index(self.driver.current_window_handle)
        next_idx = (current + 1) % len(handles)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self.driver.switch_to.window(handles[next_idx]))

    async def handle_prev_tab(self, event: Event):
        if not self.driver:
            return
        handles = self.driver.window_handles
        current = handles.index(self.driver.current_window_handle)
        prev_idx = (current - 1) % len(handles)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self.driver.switch_to.window(handles[prev_idx]))

    async def handle_close(self, event: Event):
        if self.driver:
            self.driver.quit()
            self.driver = None
        await self.bus.emit("tts_speak", "Browser closed.")
