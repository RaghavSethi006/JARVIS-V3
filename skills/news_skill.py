from interfaces.skill import BaseSkill
from core.event_bus import Event
import requests
import os
import asyncio

class NewsSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("get_news", self.handle_news)

    async def handle_news(self, event: Event):
        if not event.data or not isinstance(event.data, dict):
            return
        api_key = os.environ.get("NEWS_API_KEY")
        if not api_key:
            await self.bus.emit("tts_speak", "I need a News API key.")
            return

        source = event.data.get("source", "bbc-news")
        
        await self.bus.emit("tts_speak", f"Fetching news from {source}.")
        
        loop = asyncio.get_event_loop()
        headlines = await loop.run_in_executor(None, self._fetch_news, api_key, source)
        
        if headlines:
            await self.bus.emit("tts_speak", "Here are the latest headlines.")
            for i, headline in enumerate(headlines[:5], 1):
                await self.bus.emit("tts_speak", f"Headline {i}: {headline}")
        else:
            await self.bus.emit("tts_speak", "Could not fetch news.")

    def _fetch_news(self, api_key, source):
        url = f"https://newsapi.org/v2/top-headlines?sources={source}&apiKey={api_key}"
        try:
            response = requests.get(url)
            data = response.json()
            
            if data.get('status') == 'ok':
                return [article['title'] for article in data.get('articles', [])]
        except Exception as e:
            print(f"News fetch error: {e}")
        return None
