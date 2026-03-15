from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
import asyncio
import os

class WeatherSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.default_city = os.environ.get("DEFAULT_CITY", "")

    def register(self):
        self.bus.subscribe("check_weather", self.handle_weather)

    async def handle_weather(self, event: Event):
        city = (event.data or {}).get("city") or self.default_city
        await self.bus.emit("tts_speak", f"Fetching weather for {city}...")
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, self._fetch_weather, city)
            if data:
                await self.bus.emit("tts_speak", data)
            else:
                await self.bus.emit("tts_speak", f"Sorry, I couldn't get weather data for {city}.")
        except Exception as e:
            logger.error(f"Weather error: {e}")
            await self.bus.emit("tts_speak", "Weather service is unavailable.")

    def _fetch_weather(self, city: str) -> str:
        """Synchronous - runs in executor."""
        import requests

        api_key = os.environ.get("OPENWEATHER_API_KEY", "")
        if not api_key:
            return "Weather API key not configured. Set OPENWEATHER_API_KEY in .env"
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={city}&appid={api_key}&units=metric"
        )
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            d = resp.json()
            temp = d["main"]["temp"]
            feels = d["main"]["feels_like"]
            desc = d["weather"][0]["description"]
            humid = d["main"]["humidity"]
            return (
                f"Weather in {city}: {desc}. "
                f"Temperature {temp:.1f} degrees C, feels like {feels:.1f} degrees C. "
                f"Humidity {humid}%."
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return f"City '{city}' not found."
            return f"Weather API error: {e}"
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return None
