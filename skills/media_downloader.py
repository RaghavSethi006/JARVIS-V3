from interfaces.skill import BaseSkill
from core.event_bus import Event
import yt_dlp
import os
import asyncio

class MediaSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("download_video", self.handle_download)

    async def handle_download(self, event: Event):
        url = event.data.get("url")
        if not url:
            return

        save_path = event.data.get("path", os.path.join(os.path.expanduser("~"), "Downloads"))
        
        print(f"Downloading {url} to {save_path}...")
        await self.bus.emit("tts_speak", "Starting download.")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._download(url, save_path))
            
            print("Download complete.")
            await self.bus.emit("tts_speak", "Download complete.")
        except Exception as e:
            print(f"Download error: {e}")
            await self.bus.emit("tts_speak", "Download failed.")

    def _download(self, url, path):
        ydl_opts = {
            'outtmpl': os.path.join(path, '%(title)s.%(ext)s'),
            'format': 'best',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
