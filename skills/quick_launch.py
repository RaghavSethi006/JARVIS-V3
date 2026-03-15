from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
import asyncio
import platform
import subprocess
import webbrowser

KNOWN_APPS = {
    "spotify": {"win": "spotify", "darwin": "Spotify", "linux": "spotify"},
    "chrome": {"win": "chrome", "darwin": "Google Chrome", "linux": "google-chrome"},
    "vscode": {"win": "code", "darwin": "Visual Studio Code", "linux": "code"},
    "discord": {"win": "discord", "darwin": "Discord", "linux": "discord"},
    "notepad": {"win": "notepad", "darwin": "TextEdit", "linux": "gedit"},
    "calculator": {"win": "calc", "darwin": "Calculator", "linux": "gnome-calculator"},
}

KNOWN_URLS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "chatgpt": "https://chat.openai.com",
    "google maps": "https://maps.google.com",
    "google drive": "https://drive.google.com",
    "google calendar": "https://calendar.google.com",
    "linkedin": "https://www.linkedin.com",
    "spotify web": "https://open.spotify.com",
}

class QuickLaunchSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("quick_launch", self.handle_launch)

    async def handle_launch(self, event: Event):
        app = (event.data or {}).get("app", "").strip().lower()
        if not app:
            return
        loop = asyncio.get_event_loop()

        for key, url in KNOWN_URLS.items():
            if key in app:
                await self.bus.emit("tts_speak", f"Opening {key}.")
                await loop.run_in_executor(None, lambda: webbrowser.open(url))
                return

        sys_name = platform.system().lower()
        sys_key = {"windows": "win", "darwin": "darwin", "linux": "linux"}.get(sys_name, "linux")
        for key, platforms in KNOWN_APPS.items():
            if key in app:
                exe = platforms.get(sys_key, key)
                await self.bus.emit("tts_speak", f"Launching {key}.")
                await loop.run_in_executor(None, self._open_app, exe, sys_name)
                return

        await self.bus.emit("tts_speak", f"Trying to open {app}.")
        await loop.run_in_executor(None, self._open_app, app, platform.system().lower())

    def _open_app(self, exe: str, sys_name: str):
        try:
            if sys_name == "windows":
                subprocess.Popen(
                    ["cmd", "/c", "start", "", exe],
                    shell=False,
                    creationflags=subprocess.DETACHED_PROCESS,
                )
            elif sys_name == "darwin":
                subprocess.Popen(["open", "-a", exe])
            else:
                subprocess.Popen([exe])
        except Exception as e:
            logger.error(f"Quick launch failed for {exe}: {e}")
