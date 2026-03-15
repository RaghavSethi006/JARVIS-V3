from interfaces.skill import BaseSkill
from core.event_bus import Event
import pywhatkit
import pandas as pd
import difflib
import os
import asyncio

class WhatsAppSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.contacts = self._load_contacts()

    def register(self):
        self.bus.subscribe("send_whatsapp", self.handle_send)

    def _load_contacts(self):
        _BASE = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(_BASE, "..", "contacts.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if 'Name' in df.columns and 'Phone' in df.columns:
                    return df
            except Exception as e:
                print(f"Error loading contacts: {e}")
        return pd.DataFrame(columns=['Name', 'Phone'])

    async def handle_send(self, event: Event):
        target_name = event.data.get("contact")
        message = event.data.get("message")

        if not target_name or not message:
            await self.bus.emit("tts_speak", "I need a contact name and message.")
            return

        phone = self._find_contact(target_name)
        if not phone:
            await self.bus.emit("tts_speak", f"Contact {target_name} not found.")
            return

        await self.bus.emit("tts_speak", f"Sending message to {target_name}.")
        
        # Run blocking pywhatkit in executor
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: pywhatkit.sendwhatmsg_instantly(
                    phone, message, wait_time=10, tab_close=True
                ),
            )
            await self.bus.emit("tts_speak", "Message sent successfully.")
        except Exception as e:
            print(f"WhatsApp Error: {e}")
            await self.bus.emit("tts_speak", "Failed to send the message.")

    def _find_contact(self, name):
        if self.contacts.empty:
            return None
        matches = difflib.get_close_matches(name, self.contacts['Name'].tolist(), n=1, cutoff=0.5)
        if matches:
            phone = self.contacts[self.contacts['Name'] == matches[0]]['Phone'].iloc[0]
            phone = ''.join(str(phone).split())
            return phone
        return None
