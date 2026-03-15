"""Volume, brightness, window management, screenshots, and utility controls."""

import asyncio
import os
import platform
import subprocess

from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger


class SystemControlSkill(BaseSkill):
    def register(self):
        self.bus.subscribe("set_volume", self.handle_volume)
        self.bus.subscribe("set_brightness", self.handle_brightness)
        self.bus.subscribe("minimize_window", self.handle_minimize)
        self.bus.subscribe("maximize_window", self.handle_maximize)
        self.bus.subscribe("take_screenshot", self.handle_screenshot)
        self.bus.subscribe("search_file", self.handle_file_search)
        self.bus.subscribe("tell_joke", self.handle_joke)

    async def handle_volume(self, event: Event):
        action = (event.data or {}).get("action", "up")
        value = int((event.data or {}).get("value", 10))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._set_volume, action, value)
        await self.bus.emit("tts_speak", result)

    def _set_volume(self, action: str, value: int) -> str:
        if platform.system() != "Windows":
            return "Volume control is only supported on Windows."
        try:
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            current = volume.GetMasterVolumeLevelScalar()

            if action == "up":
                volume.SetMasterVolumeLevelScalar(min(1.0, current + value / 100.0), None)
                return "Volume increased."
            if action == "down":
                volume.SetMasterVolumeLevelScalar(max(0.0, current - value / 100.0), None)
                return "Volume decreased."
            if action == "mute":
                volume.SetMute(1, None)
                return "Muted."
            if action == "set":
                clamped = max(0, min(100, value))
                volume.SetMasterVolumeLevelScalar(clamped / 100.0, None)
                return f"Volume set to {clamped} percent."
        except Exception as exc:
            logger.error("Volume error: %s", exc)
        return "Could not adjust volume."

    async def handle_brightness(self, event: Event):
        action = (event.data or {}).get("action", "up")
        value = int((event.data or {}).get("value", 20))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._set_brightness, action, value)
        await self.bus.emit("tts_speak", result)

    def _set_brightness(self, action: str, value: int) -> str:
        if platform.system() != "Windows":
            return "Brightness control is only supported on Windows."
        try:
            import wmi

            wmi_obj = wmi.WMI(namespace="wmi")
            methods = wmi_obj.WmiMonitorBrightnessMethods()[0]
            current = wmi_obj.WmiMonitorBrightness()[0].CurrentBrightness
            if action == "up":
                new_value = min(100, current + value)
            elif action == "down":
                new_value = max(0, current - value)
            else:
                new_value = max(0, min(100, value))
            methods.WmiSetBrightness(new_value, 0)
            return f"Brightness set to {new_value} percent."
        except Exception as exc:
            logger.error("Brightness error: %s", exc)
            return "Could not adjust brightness."

    async def handle_minimize(self, event: Event):
        import pyautogui

        pyautogui.hotkey("win", "down")
        await self.bus.emit("tts_speak", "Window minimized.")

    async def handle_maximize(self, event: Event):
        import pyautogui

        pyautogui.hotkey("win", "up")
        await self.bus.emit("tts_speak", "Window maximized.")

    async def handle_screenshot(self, event: Event):
        import datetime
        import pyautogui

        loop = asyncio.get_event_loop()

        def _snap():
            fname = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            pictures = os.path.join(os.path.expanduser("~"), "Pictures")
            os.makedirs(pictures, exist_ok=True)
            path = os.path.join(pictures, fname)
            pyautogui.screenshot(path)
            return path

        _ = await loop.run_in_executor(None, _snap)
        await self.bus.emit("tts_speak", "Screenshot saved.")

    async def handle_file_search(self, event: Event):
        query = (event.data or {}).get("query", "")
        if not query:
            return
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, self._search_files, query)
        if results:
            await self.bus.emit(
                "tts_speak",
                f"Found {len(results)} files. Top result: {results[0]}",
            )
        else:
            await self.bus.emit("tts_speak", f"No files found matching {query}.")

    def _search_files(self, query: str) -> list[str]:
        home = os.path.expanduser("~")
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["where", "/r", home, f"*{query}*"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                result = subprocess.run(
                    ["find", home, "-name", f"*{query}*", "-type", "f"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
        except Exception as exc:
            logger.error("File search error: %s", exc)
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()][:5]

    async def handle_joke(self, event: Event):
        try:
            import pyjokes

            await self.bus.emit("tts_speak", pyjokes.get_joke())
        except ImportError:
            await self.bus.emit("tts_speak", "Install pyjokes to hear jokes. pip install pyjokes")
