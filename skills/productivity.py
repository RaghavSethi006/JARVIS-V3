from interfaces.skill import BaseSkill
from core.event_bus import Event
import asyncio
import datetime
import re
import threading
import time
from core.database import delete_alarm, load_pending_alarms, save_alarm

class ProductivitySkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.alarms = []
        self.running = True
        self._loop = None
        self._load_alarms_from_db()
        self.check_thread = threading.Thread(target=self.check_loop, daemon=True)
        self.check_thread.start()

    def register(self):
        self._loop = asyncio.get_event_loop()
        self.bus.subscribe("set_alarm", self.handle_set_alarm)
        self.bus.subscribe("set_reminder", self.handle_set_reminder)

    async def handle_set_alarm(self, event: Event):
        text = ((event.data or {}).get("time_text") or "").strip()
        if not text:
            return

        target_time = self._parse_alarm_time(text)
        if target_time is None:
            await self.bus.emit("tts_speak", "Please specify a time like 5:30 PM.")
            return

        msg = "Wake up!"
        alarm_id = save_alarm(target_time, msg)
        self.alarms.append(
            {"id": alarm_id, "time": target_time, "type": "alarm", "msg": msg}
        )
        formatted = target_time.strftime("%I:%M %p")
        await self.bus.emit("tts_speak", f"Alarm set for {formatted}")
        print(f"Alarm set for {formatted}")

    async def handle_set_reminder(self, event: Event):
        # Similar logic maybe with a message
        pass

    def check_loop(self):
        """Background thread - checks alarms every second."""
        while self.running:
            now = datetime.datetime.now()
            triggered = [a for a in self.alarms if now >= a["time"]]
            for alarm in triggered:
                self.alarms.remove(alarm)
                alarm_id = alarm.get("id")
                if alarm_id is not None:
                    delete_alarm(int(alarm_id))
                msg = alarm.get("msg", "Wake up!")
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.bus.emit("tts_speak", msg),
                        self._loop,
                    )
            time.sleep(1)

    def _parse_alarm_time(self, text: str):
        text = text.lower()
        patterns = [
            r"\b\d{1,2}:\d{2}\s*[ap]m\b",
            r"\b\d{1,2}\s*[ap]m\b",
        ]
        parsed = None
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            time_str = match.group().replace(" ", "")
            fmt = "%I:%M%p" if ":" in time_str else "%I%p"
            try:
                parsed = datetime.datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None

        now = datetime.datetime.now()
        target = parsed.replace(year=now.year, month=now.month, day=now.day)
        if target < now:
            target += datetime.timedelta(days=1)
        return target

    def _load_alarms_from_db(self):
        pending = load_pending_alarms()
        loaded = []
        for alarm in pending:
            trigger_raw = alarm.get("trigger_time")
            if not trigger_raw:
                continue
            try:
                trigger_time = datetime.datetime.fromisoformat(trigger_raw)
            except ValueError:
                continue
            loaded.append(
                {
                    "id": alarm.get("id"),
                    "time": trigger_time,
                    "type": "alarm",
                    "msg": alarm.get("message", "Wake up!"),
                }
            )
        self.alarms.extend(loaded)
