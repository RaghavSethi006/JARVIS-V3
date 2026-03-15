from interfaces.skill import BaseSkill
from core.event_bus import Event
import webbrowser
import asyncio

class WebAutomationSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("open_website", self.handle_open_site)
        self.bus.subscribe("search_google", self.handle_search)

    async def handle_open_site(self, event: Event):
        url = event.data.get("url")
        if not url:
            return
        
        # Ensure http protocol
        if not url.startswith("http"):
            url = "https://" + url
            
        print(f"Opening {url}...")
        await self.bus.emit("tts_speak", f"Opening {url}")
        
        # Run in executor to be safe, though webbrowser is usually fast
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: webbrowser.open(url))

    async def handle_search(self, event: Event):
        query = event.data.get("query")
        if query:
            url = f"https://www.google.com/search?q={query}"
            await self.bus.emit("open_website", {"url": url})
