"""Real Spotify control via Spotify Web API (spotipy)."""

import asyncio
import os

from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger


class SpotifySkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self._sp = None
        self._init_spotify()

    def _init_spotify(self):
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
        if not client_id or not client_secret:
            logger.warning("Spotify credentials not set. SpotifySkill disabled.")
            return
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            self._sp = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=(
                        "user-modify-playback-state "
                        "user-read-playback-state "
                        "user-read-currently-playing"
                    ),
                )
            )
        except Exception as exc:
            logger.error("Spotify init failed: %s", exc)

    def register(self):
        self.bus.subscribe("spotify_play", self.handle_play)
        self.bus.subscribe("spotify_pause", self.handle_pause)
        self.bus.subscribe("spotify_next", self.handle_next)
        self.bus.subscribe("spotify_prev", self.handle_prev)
        self.bus.subscribe("spotify_volume", self.handle_volume)

    async def _sp_call(self, fn, *args, **kwargs):
        if not self._sp:
            await self.bus.emit("tts_speak", "Spotify is not configured.")
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def handle_play(self, event: Event):
        query = (event.data or {}).get("query", "")
        if query:
            results = await self._sp_call(self._sp.search, q=query, type="track", limit=1)
            if results and results["tracks"]["items"]:
                uri = results["tracks"]["items"][0]["uri"]
                name = results["tracks"]["items"][0]["name"]
                await self._sp_call(self._sp.start_playback, uris=[uri])
                await self.bus.emit("tts_speak", f"Playing {name} on Spotify.")
            else:
                await self.bus.emit("tts_speak", "Couldn't find that track.")
        else:
            await self._sp_call(self._sp.start_playback)
            await self.bus.emit("tts_speak", "Resuming Spotify.")

    async def handle_pause(self, event: Event):
        await self._sp_call(self._sp.pause_playback)
        await self.bus.emit("tts_speak", "Spotify paused.")

    async def handle_next(self, event: Event):
        await self._sp_call(self._sp.next_track)
        await self.bus.emit("tts_speak", "Next track.")

    async def handle_prev(self, event: Event):
        await self._sp_call(self._sp.previous_track)
        await self.bus.emit("tts_speak", "Previous track.")

    async def handle_volume(self, event: Event):
        level = int((event.data or {}).get("value", 50))
        level = max(0, min(100, level))
        await self._sp_call(self._sp.volume, level)
        await self.bus.emit("tts_speak", f"Spotify volume set to {level}.")
