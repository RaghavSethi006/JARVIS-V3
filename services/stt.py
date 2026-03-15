import asyncio
import threading

import speech_recognition as sr

from core.event_bus import EventBus
from core.logger import logger


class STTService:
    def __init__(self, bus: EventBus, engine_loop: asyncio.AbstractEventLoop = None):
        self.bus = bus
        self._engine_loop = engine_loop or asyncio.get_event_loop()
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.8
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self._listening = False
        self._thread = None

        self.bus.subscribe("toggle_listening", self._on_toggle)

    def _on_toggle(self, event):
        """Sync callback - toggles listening state or applies explicit active flag."""
        payload = event.data if isinstance(event.data, dict) else {}
        desired = payload.get("active")
        if desired is True:
            self.start_listening()
            return
        if desired is False:
            self.stop_listening()
            return
        if self._listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if self._listening:
            return
        logger.info("STT: Starting voice listener...")
        self._listening = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop_listening(self):
        logger.info("STT: Stopping voice listener.")
        self._listening = False

    def _listen_loop(self):
        """Background thread: listens for speech and emits events."""
        def emit(event_name, data=None):
            future = asyncio.run_coroutine_threadsafe(
                self.bus.emit(event_name, data), self._engine_loop
            )
            try:
                future.result(timeout=10)
            except Exception as e:
                logger.error(f"STT emit error ({event_name}): {e}")

        with sr.Microphone() as source:
            logger.info("STT: Adjusting for ambient noise...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("STT: Ready. Listening...")

            emit("set_listening", True)
            emit("set_status", "Listening...")

            while self._listening:
                try:
                    audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                    emit("set_core_state", "thinking")
                    text = self.recognizer.recognize_google(audio)
                    logger.info(f"STT recognized: {text}")

                    emit("stt_recognition", text)
                    emit("process_user_input", {"text": text})
                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    logger.debug("STT: Could not understand audio.")
                    emit("set_core_state", "idle")
                except sr.RequestError as e:
                    logger.error(f"STT RequestError: {e}")
                    emit("tts_speak", "Speech recognition service is unavailable.")
                    break
                except Exception as e:
                    logger.error(f"STT loop error: {e}")
                    break

        emit("set_listening", False)
        emit("set_status", "Online")
        emit("set_core_state", "idle")
        self._listening = False
