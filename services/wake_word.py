"""Always-on wake-word detection and fallback keyword spotting."""

import asyncio
import threading

from core.logger import logger

try:
    from openwakeword.model import Model
    import sounddevice as sd

    HAS_OWW = True
except ImportError:
    HAS_OWW = False


class WakeWordService:
    def __init__(self, bus, engine_loop: asyncio.AbstractEventLoop):
        self.bus = bus
        self._loop = engine_loop
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("WakeWordService started.")

    def stop(self):
        self._running = False

    def _run(self):
        if HAS_OWW:
            self._run_oww()
        else:
            self._run_fallback()

    def _emit_detected(self):
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.bus.emit("wake_word_detected", {}), self._loop)

    def _run_oww(self):
        chunk = 1280
        try:
            model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception as exc:
            logger.warning("WakeWord openWakeWord init failed; using fallback: %s", exc)
            self._run_fallback()
            return
        logger.info("WakeWord: using openWakeWord model.")
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk) as stream:
            while self._running:
                audio_chunk, _ = stream.read(chunk)
                prediction = model.predict(audio_chunk.flatten())
                if prediction.get("hey_jarvis", 0) > 0.5:
                    logger.info("Wake word detected.")
                    self._emit_detected()

    def _run_fallback(self):
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        logger.info("WakeWord: using speech_recognition fallback.")
        while self._running:
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    audio = recognizer.listen(source, timeout=5, phrase_time_limit=3)
                text = recognizer.recognize_google(audio).lower()
                if "jarvis" in text:
                    logger.info("Wake keyword detected by fallback.")
                    self._emit_detected()
            except Exception:
                continue
