from interfaces.skill import BaseSkill
from core.event_bus import Event
import subprocess
import platform
import asyncio
import psutil
from core.logger import logger

class SystemSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("system_launch", self.handle_launch)
        self.bus.subscribe("system_close", self.handle_close)

    async def handle_launch(self, event: Event):
        app_name = (event.data or {}).get("app", "").strip()
        if not app_name:
            return
        loop = asyncio.get_event_loop()
        await self.bus.emit("tts_speak", f"Opening {app_name}.")
        try:
            await loop.run_in_executor(None, self._launch_app, app_name)
        except Exception as e:
            logger.error(f"Launch error: {e}")
            await self.bus.emit("tts_speak", f"Couldn't open {app_name}.")

    def _launch_app(self, app_name: str):
        sys_name = platform.system()
        if sys_name == "Windows":
            subprocess.Popen(
                ["cmd", "/c", "start", "", app_name],
                shell=False,
                creationflags=subprocess.DETACHED_PROCESS,
            )
        elif sys_name == "Darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:
            subprocess.Popen([app_name])

    async def handle_close(self, event: Event):
        app_name = (event.data or {}).get("app", "").strip().lower()
        if not app_name:
            return
        loop = asyncio.get_event_loop()
        killed = await loop.run_in_executor(None, self._kill_app, app_name)
        if killed:
            await self.bus.emit("tts_speak", f"Closed {app_name}.")
        else:
            await self.bus.emit("tts_speak", f"Couldn't find {app_name} running.")

    def _kill_app(self, app_name: str) -> bool:
        killed = False
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if app_name in name:
                proc.kill()
                killed = True
        return killed
