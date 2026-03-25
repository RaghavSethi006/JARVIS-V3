"""
services/tts.py

TTS using Kokoro ONNX (bm_george -- British male).
Falls back to pyttsx3 automatically if Kokoro models are missing
or if any error occurs mid-speech, so the system never goes silent.

Public interface is identical to the previous pyttsx3 implementation --
nothing else in the codebase needs to change.
"""

import asyncio
import os
import threading

import numpy as np

from core.event_bus import EventBus
from core.logger import logger

# -- Model paths ---------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KOKORO_DIR = os.path.join(_ROOT, "models", "kokoro")
_MODEL_PATH = os.path.join(_KOKORO_DIR, "kokoro-v0_19.onnx")
_VOICES_PATH = os.path.join(_KOKORO_DIR, "voices.bin")

# -- Kokoro config -------------------------------------------------------------
KOKORO_VOICE = "bm_george"  # British male - deep, measured
KOKORO_SPEED = 0.95  # Slightly slower than default -> more deliberate
KOKORO_LANG = "en-gb"
SAMPLE_RATE = 24000  # Kokoro native sample rate

if os.environ.get("TTS_CLI_MODE") == "1":
    logger.info("TTS: CLI mode enabled. Speech playback is disabled.")


def _kokoro_available() -> bool:
    return os.path.exists(_MODEL_PATH) and os.path.exists(_VOICES_PATH)


class TTSService:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self._lock = asyncio.Lock()
        self._kokoro = None  # lazy-loaded on first speak
        self._kokoro_load_lock = threading.Lock()
        self._use_kokoro = _kokoro_available()

        if self._use_kokoro:
            logger.info("TTS: Kokoro model files found. Using bm_george (British).")
        else:
            logger.warning(
                "TTS: Kokoro models not found in models/kokoro/. "
                "Falling back to pyttsx3. "
                "Download kokoro-v0_19.onnx and voices.bin to enable."
            )

        self.bus.subscribe("tts_speak", self.speak)

    # -- Kokoro ----------------------------------------------------------------

    def _load_kokoro(self):
        """Load Kokoro once, thread-safe. Returns the model or None on failure."""
        if self._kokoro is not None:
            return self._kokoro
        with self._kokoro_load_lock:
            if self._kokoro is not None:  # double-checked locking
                return self._kokoro
            try:
                from kokoro_onnx import Kokoro

                self._kokoro = Kokoro(_MODEL_PATH, _VOICES_PATH)
                logger.info("TTS: Kokoro loaded successfully.")
            except Exception as e:
                logger.error(f"TTS: Kokoro load failed: {e}")
                self._use_kokoro = False
        return self._kokoro

    def _speak_kokoro(self, text: str):
        """
        Generate audio with Kokoro and play it through sounddevice.
        Runs in a thread executor so it never blocks the event loop.
        """
        model = self._load_kokoro()
        if model is None:
            raise RuntimeError("Kokoro model unavailable.")

        import sounddevice as sd

        samples, rate = model.create(
            text,
            voice=KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang=KOKORO_LANG,
        )

        # Kokoro returns float32 in range [-1, 1] - sounddevice expects that.
        samples = np.array(samples, dtype=np.float32)
        sd.play(samples, samplerate=rate)
        sd.wait()  # blocks until playback complete - correct inside executor

    # -- pyttsx3 fallback ------------------------------------------------------

    def _speak_sync(self, text: str):
        """pyttsx3 fallback - used when Kokoro is unavailable or fails."""
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 162)  # slower = less robotic
            engine.setProperty("volume", 0.92)
            # Try to find any non-American voice
            voices = engine.getProperty("voices")
            for v in voices:
                if any(x in v.id.lower() for x in ("gb", "british", "george", "hazel")):
                    engine.setProperty("voice", v.id)
                    break
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            logger.error(f"TTS fallback error: {e}")

    # -- Public speak handler --------------------------------------------------

    async def speak(self, event):
        text = str(event.data or "").strip()
        if not text:
            return

        logger.info(f"TTS: {text}")

        async with self._lock:
            await self.bus.emit("set_core_state", "speaking")
            loop = asyncio.get_event_loop()

            if self._use_kokoro:
                try:
                    await loop.run_in_executor(None, self._speak_kokoro, text)
                except Exception as e:
                    logger.warning(
                        f"TTS: Kokoro failed ({e}), falling back to pyttsx3."
                    )
                    await loop.run_in_executor(None, self._speak_sync, text)
            else:
                await loop.run_in_executor(None, self._speak_sync, text)

            await self.bus.emit("set_core_state", "idle")
