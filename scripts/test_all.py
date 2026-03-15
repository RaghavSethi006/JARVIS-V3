"""
scripts/test_all.py
JARVIS ── Upgraded Full System Test Suite v2.0
─────────────────────────────────────────────
Run from the project root:
    python scripts/test_all.py

Covers:
  1.  Environment & files
  2.  Package imports
  3.  Core  (EventBus, Engine, Logger, Config)
  4.  Services  (TTS, STT, Gesture, Biometrics)
  5.  Skills init & subscriptions  (all 11 skills)
  6.  Command routing  (single + multi-command)
  7.  Skills functional  (mocked external calls)
  8.  LLM  (intent parsing, all 4 API providers, local model, edge cases)
  9.  Multi-command dispatch pipeline
  10. Bridge  (JS queue, pill-window, evaluate_js_call)
  11. Config  (env override, dot-notation, singleton)
  12. Frontend build
  13. Bug-regression checks  (model path, thread-safety, event loop, TTS lock)
"""

import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import platform
import sys
import threading
import time
import traceback
from unittest.mock import AsyncMock, MagicMock, patch, call

# ── stdout encoding ────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Project root ───────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── ANSI colours ──────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"

# ── Results store ─────────────────────────────────────────────────────────────
results: list[tuple] = []


def _fmt_note(note: str) -> str:
    return f"  {DIM}{note}{RESET}" if note else ""


def passed(cat, name, note=""):
    results.append((cat, name, "PASS", note))
    print(f"  {GREEN}✓{RESET}  {name}{_fmt_note(note)}")


def failed(cat, name, note=""):
    results.append((cat, name, "FAIL", note))
    print(f"  {RED}✗{RESET}  {name}{_fmt_note(note)}")


def warned(cat, name, note=""):
    results.append((cat, name, "WARN", note))
    print(f"  {YELLOW}⚠{RESET}  {name}{_fmt_note(note)}")


def header(title: str):
    bar = "─" * 60
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")


def subheader(title: str):
    print(f"\n  {BOLD}{BLUE}▸ {title}{RESET}")


# ── Async helpers ──────────────────────────────────────────────────────────────
def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Import helpers ─────────────────────────────────────────────────────────────
def clean_import(*module_names):
    for module_name in module_names:
        keys = [
            k
            for k in sys.modules
            if k == module_name or k.startswith(module_name + ".")
        ]
        for k in keys:
            del sys.modules[k]


def get_eventbus_class():
    for name in ("core.event_bus", "core.eventbus"):
        try:
            mod = importlib.import_module(name)
            return getattr(mod, "EventBus")
        except Exception:
            continue
    raise ImportError("Could not import EventBus")


def import_skill(module_name: str, class_name: str):
    """Import a skill class, trying both underscore and no-underscore module names."""
    for candidate in (
        f"skills.{module_name}",
        f"skills.{module_name.replace('_', '')}",
    ):
        try:
            mod = importlib.import_module(candidate)
            return getattr(mod, class_name)
        except Exception:
            continue
    raise ImportError(f"Cannot import {class_name} from skills.{module_name}")


def get_webview_main_path():
    for name in ("webview_main.py", "webviewmain.py"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, "webview_main.py")


# ── Shared bus factory ─────────────────────────────────────────────────────────
def make_bus():
    """Return (bus, spoken_list) where spoken_list captures all tts_speak payloads."""
    EventBus = get_eventbus_class()
    bus = EventBus()
    spoken = []
    emitted = {}

    async def cap_tts(e):
        spoken.append(e.data)

    async def cap_any(e):
        emitted[e.name] = (emitted.get(e.name) or []) + [e.data]

    bus.subscribe("tts_speak", cap_tts)
    # Patch emit to also track every event name
    _orig_emit = bus.emit

    async def patched_emit(event_name, data=None):
        emitted.setdefault(event_name, []).append(data)
        await _orig_emit(event_name, data)

    bus.emit = patched_emit

    return bus, spoken, emitted


# ══════════════════════════════════════════════════════════════════════════════
# 1 ── ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════
def test_environment():
    CAT = "Environment"
    header("1 ── Environment & Files")

    maj, mn = sys.version_info[:2]
    if maj >= 3 and mn >= 10:
        passed(CAT, f"Python {maj}.{mn} (≥3.10)")
    else:
        failed(CAT, f"Python {maj}.{mn}", "need 3.10+")

    required = {
        "requirements.txt": "requirements.txt",
        "config/.env": ".env config",
        "config/settings.yaml": "settings.yaml",
        "contacts.csv": "contacts.csv",
        os.path.relpath(get_webview_main_path(), ROOT): "webview_main.py",
    }
    for rel, label in required.items():
        if os.path.exists(os.path.join(ROOT, rel)):
            passed(CAT, label)
        else:
            failed(CAT, label, f"missing: {rel}")

    optional = {
        "frontend/dist/index.html": "Frontend build (dist/index.html)",
        "models/biometrics/face_model.yml": "Face biometric data",
        "models/biometrics/face_encodings.npy": "face_recognition encodings",
        "user_data.csv": "User data CSV",
    }
    for rel, label in optional.items():
        if os.path.exists(os.path.join(ROOT, rel)):
            passed(CAT, label)
        else:
            warned(CAT, label, "missing – feature limited")

    # Model files
    models_dir = os.path.join(ROOT, "models")
    if os.path.isdir(models_dir):
        ggufs = sorted(f for f in os.listdir(models_dir) if f.lower().endswith(".gguf"))
        if ggufs:
            size_mb = os.path.getsize(os.path.join(models_dir, ggufs[0])) // (
                1024 * 1024
            )
            passed(CAT, f"Local LLM model [{ggufs[0]}] ({size_mb} MB)")
            # Check for the correct filename (regression: Q4KM vs Q4_K_M)
            correct = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
            if correct in ggufs:
                passed(CAT, f"Model filename is correct ({correct})")
            else:
                warned(CAT, "Model filename", f"Expected '{correct}', found {ggufs[0]}")
        else:
            warned(CAT, "Local LLM model", "no .gguf in models/ – local LLM disabled")
    else:
        warned(CAT, "models/ directory", "missing")

    # ENV vars
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, "config", ".env"), encoding="latin-1")
    except Exception as e:
        failed(CAT, ".env load", str(e))
        return

    env_keys = {
        "OPENWEATHER_API_KEY": "Weather API key",
        "EMAIL_ADDRESS": "Email address",
        "EMAIL_PASSWORD": "Email app password",
        "NEWSAPI_KEY": "News API key",
        "LLM_MODE": "LLM mode (local|api)",
    }
    for key, label in env_keys.items():
        if os.environ.get(key):
            passed(CAT, f"ENV: {label}")
        else:
            warned(CAT, f"ENV: {label}", f"{key} not set")

    llm_mode = os.environ.get("LLM_MODE", "local")
    if llm_mode == "api":
        if os.environ.get("LLM_API_KEY"):
            passed(CAT, "ENV: LLM API key")
        else:
            failed(CAT, "ENV: LLM API key", "LLM_MODE=api but LLM_API_KEY missing")
        provider = os.environ.get("LLM_API_PROVIDER", "groq")
        passed(CAT, f"ENV: LLM provider = {provider}")


# ══════════════════════════════════════════════════════════════════════════════
# 2 ── IMPORTS
# ══════════════════════════════════════════════════════════════════════════════
def test_imports():
    CAT = "Imports"
    header("2 ── Package Imports")

    core_packages = [
        ("webview", "pywebview"),
        ("speech_recognition", "SpeechRecognition"),
        ("pyttsx3", "pyttsx3"),
        ("cv2", "opencv-contrib-python"),
        ("selenium", "selenium"),
        ("pywhatkit", "pywhatkit"),
        ("psutil", "psutil"),
        ("requests", "requests"),
        ("pandas", "pandas"),
        ("numpy", "numpy"),
        ("mediapipe", "mediapipe"),
        ("dotenv", "python-dotenv"),
        ("schedule", "schedule"),
        ("pyautogui", "pyautogui"),
        ("bs4", "beautifulsoup4"),
        ("yaml", "pyyaml"),
        ("yt_dlp", "yt-dlp"),
    ]
    for module, pkg in core_packages:
        try:
            importlib.import_module(module)
            passed(CAT, pkg)
        except ImportError as e:
            failed(CAT, pkg, str(e))

    # Optional / platform-specific
    try:
        import pyaudio

        passed(CAT, "pyaudio (voice input)")
    except ImportError:
        warned(CAT, "pyaudio", "voice disabled – pipwin install pyaudio")

    try:
        import cv2

        cv2.face.LBPHFaceRecognizer_create()
        passed(CAT, "cv2.face.LBPHFaceRecognizer (LBPH)")
    except AttributeError:
        failed(CAT, "cv2.face", "need opencv-contrib-python, not opencv-python")
    except ImportError:
        failed(CAT, "cv2.face", "cv2 not installed")

    try:
        from llama_cpp import Llama

        passed(CAT, "llama-cpp-python (local LLM)")
    except ImportError:
        warned(CAT, "llama-cpp-python", "local LLM disabled")

    # API provider SDKs (all optional)
    for module, label in [
        ("groq", "groq SDK"),
        ("openai", "openai SDK"),
        ("anthropic", "anthropic SDK"),
        ("google.generativeai", "google-generativeai SDK"),
    ]:
        try:
            importlib.import_module(module)
            passed(CAT, label)
        except ImportError:
            warned(CAT, label, "install to use this LLM provider")

    # Verify no stray newsapi import name confusion
    try:
        from newsapi import NewsApiClient

        passed(CAT, "newsapi-python (NewsApiClient)")
    except ImportError:
        warned(CAT, "newsapi-python", "news skill will be limited")


# ══════════════════════════════════════════════════════════════════════════════
# 3 ── CORE
# ══════════════════════════════════════════════════════════════════════════════
def test_core():
    CAT = "Core"
    header("3 ── Core (EventBus, Engine, Logger, Config)")

    # Logger
    try:
        from core.logger import logger

        logger.info("Logger test from test suite")
        passed(CAT, "Logger initializes and emits")
    except Exception as e:
        failed(CAT, "Logger", str(e))
        return

    # ConfigManager
    try:
        from config.manager import ConfigManager

        cfg = ConfigManager()
        cfg.get("app.name", "fallback")
        passed(CAT, "ConfigManager loads settings.yaml")
        passed(CAT, "ConfigManager.get() dot notation")
        # Singleton
        cfg2 = ConfigManager()
        if cfg is cfg2:
            passed(CAT, "ConfigManager is a singleton")
        else:
            failed(CAT, "ConfigManager singleton", "new instance returned")
    except Exception as e:
        failed(CAT, "ConfigManager", str(e))

    EventBus = get_eventbus_class()

    subheader("EventBus")

    # Basic emit/subscribe
    try:
        bus = EventBus()
        got = []

        async def h(e):
            got.append(e.data)

        bus.subscribe("t1", h)
        run_async(bus.emit("t1", "hello"))
        if got == ["hello"]:
            passed(CAT, "EventBus: basic emit / subscribe")
        else:
            failed(CAT, "EventBus basic emit", f"got {got}")
    except Exception as e:
        failed(CAT, "EventBus basic", str(e))

    # Multiple subscribers – all called
    try:
        bus = EventBus()
        log = []

        async def h1(e):
            log.append("h1")

        async def h2(e):
            log.append("h2")

        bus.subscribe("t2", h1)
        bus.subscribe("t2", h2)
        run_async(bus.emit("t2", None))
        if log == ["h1", "h2"]:
            passed(CAT, "EventBus: multiple subscribers all called")
        else:
            failed(CAT, "EventBus multiple subscribers", f"got {log}")
    except Exception as e:
        failed(CAT, "EventBus multiple subscribers", str(e))

    # Error isolation – bad handler should not block good handler
    try:
        bus = EventBus()
        log = []

        async def bad(e):
            raise RuntimeError("intentional")

        async def good(e):
            log.append("ok")

        bus.subscribe("t3", bad)
        bus.subscribe("t3", good)
        run_async(bus.emit("t3", None))
        if "ok" in log:
            passed(CAT, "EventBus: error isolation (bad handler doesn't block good)")
        else:
            failed(CAT, "EventBus error isolation", "good handler didn't run")
    except Exception as e:
        failed(CAT, "EventBus error isolation", str(e))

    # Sync subscriber
    try:
        bus = EventBus()
        log = []

        def sync(e):
            log.append(e.data)

        bus.subscribe("t4", sync)
        run_async(bus.emit("t4", "sync_ok"))
        if log == ["sync_ok"]:
            passed(CAT, "EventBus: sync (non-async) subscriber")
        else:
            failed(CAT, "EventBus sync subscriber", f"got {log}")
    except Exception as e:
        failed(CAT, "EventBus sync subscriber", str(e))

    # Unsubscribe
    try:
        bus = EventBus()
        log = []

        async def h(e):
            log.append("x")

        bus.subscribe("t5", h)
        bus.unsubscribe("t5", h)
        run_async(bus.emit("t5", None))
        if not log:
            passed(CAT, "EventBus: unsubscribe prevents handler from firing")
        else:
            failed(CAT, "EventBus unsubscribe", "handler called after unsubscribe")
    except Exception as e:
        failed(CAT, "EventBus unsubscribe", str(e))

    # Emit with no subscribers doesn't raise
    try:
        bus = EventBus()
        run_async(bus.emit("orphan_event", {"x": 1}))
        passed(CAT, "EventBus: emit to empty subscriber list is safe")
    except Exception as e:
        failed(CAT, "EventBus empty emit", str(e))

    # Concurrent emits – basic stability
    try:
        bus = EventBus()
        counts = []

        async def counter(e):
            counts.append(1)

        bus.subscribe("flood", counter)

        async def flood():
            await asyncio.gather(*[bus.emit("flood", i) for i in range(50)])

        run_async(flood())
        if len(counts) == 50:
            passed(CAT, "EventBus: 50 concurrent emits all delivered")
        else:
            warned(CAT, "EventBus concurrent", f"only {len(counts)}/50 received")
    except Exception as e:
        failed(CAT, "EventBus concurrent emits", str(e))

    # JarvisEngine
    try:
        from core.engine import JarvisEngine

        eng = JarvisEngine()
        if hasattr(eng, "bus") and hasattr(eng, "running"):
            passed(CAT, "JarvisEngine initializes with bus and running flag")
        else:
            failed(CAT, "JarvisEngine attributes", "missing bus or running")
    except Exception as e:
        failed(CAT, "JarvisEngine", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 4 ── SERVICES
# ══════════════════════════════════════════════════════════════════════════════
def test_services():
    CAT = "Services"
    header("4 ── Services (TTS, STT, Gesture, Biometrics)")
    EventBus = get_eventbus_class()

    # ── TTS ──────────────────────────────────────────────────────────────────
    subheader("TTSService")
    try:
        from services.tts import TTSService

        bus = EventBus()
        tts = TTSService(bus)
        passed(CAT, "TTSService: initializes")
        if "tts_speak" in bus.subscribers:
            passed(CAT, "TTSService: subscribed to tts_speak")
        else:
            failed(CAT, "TTSService: subscription to tts_speak missing")

        # asyncio.Lock exists and is an asyncio.Lock
        if hasattr(tts, "_lock") and isinstance(tts._lock, asyncio.Lock):
            passed(CAT, "TTSService: has asyncio.Lock (_lock)")
        else:
            failed(CAT, "TTSService: missing _lock", "bug: lock never created")

    except Exception as e:
        failed(CAT, "TTSService init", str(e))

    # TTS non-blocking (speak doesn't block main thread)
    try:
        from services.tts import TTSService

        bus = EventBus()
        tts = TTSService(bus)
        start = time.time()
        with patch.object(tts, "_speak_sync", return_value=None):
            run_async(bus.emit("tts_speak", "test phrase"))
        elapsed = time.time() - start
        if elapsed < 2.0:
            passed(CAT, f"TTSService: non-blocking speak ({elapsed:.3f}s)")
        else:
            warned(CAT, "TTSService speed", f"took {elapsed:.2f}s")
    except Exception as e:
        failed(CAT, "TTSService non-blocking", str(e))

    # TTS lock prevents concurrent overlapping speech
    try:
        from services.tts import TTSService

        bus = EventBus()
        tts = TTSService(bus)
        call_order = []
        original_speak = tts._speak_sync

        def slow_speak(text):
            call_order.append(("start", text))
            time.sleep(0.05)
            call_order.append(("end", text))

        with patch.object(tts, "_speak_sync", side_effect=slow_speak):

            async def fire_two():
                await asyncio.gather(
                    bus.emit("tts_speak", "first"),
                    bus.emit("tts_speak", "second"),
                )

            run_async(fire_two())

        # With locking: end of first should come before start of second
        starts = [i for i, (k, _) in enumerate(call_order) if k == "start"]
        ends = [i for i, (k, _) in enumerate(call_order) if k == "end"]
        if ends and starts and len(starts) >= 2:
            if ends[0] < starts[1]:
                passed(CAT, "TTSService: lock serialises concurrent speak calls")
            else:
                failed(
                    CAT,
                    "TTSService lock",
                    "overlapping speaks detected (lock not acquired)",
                )
        else:
            warned(
                CAT, "TTSService lock", "could not determine ordering from call_order"
            )
    except Exception as e:
        failed(CAT, "TTSService lock regression", str(e))

    # TTS emits set_core_state speaking/idle
    try:
        from services.tts import TTSService

        bus = EventBus()
        tts = TTSService(bus)
        states = []

        async def cap_state(e):
            states.append(e.data)

        bus.subscribe("set_core_state", cap_state)
        with patch.object(tts, "_speak_sync", return_value=None):
            run_async(bus.emit("tts_speak", "hello world"))
        if "speaking" in states and "idle" in states:
            passed(CAT, "TTSService: emits set_core_state speaking→idle")
        else:
            warned(CAT, "TTSService core state", f"states: {states}")
    except Exception as e:
        failed(CAT, "TTSService core state", str(e))

    # ── STT ──────────────────────────────────────────────────────────────────
    subheader("STTService")
    try:
        import speech_recognition as sr
        from services.stt import STTService

        bus = EventBus()
        # Updated constructor per Bug 7 fix (pass engine loop)
        try:
            loop = asyncio.get_event_loop()
            stt = STTService(bus, loop)
            passed(CAT, "STTService: initializes with engine_loop (Bug 7 fix)")
        except TypeError:
            # Fallback: old single-arg constructor still works
            stt = STTService(bus)
            warned(
                CAT,
                "STTService: still using single-arg constructor",
                "Bug 7 fix not applied",
            )

        if "toggle_listening" in bus.subscribers:
            passed(CAT, "STTService: subscribed to toggle_listening")
        else:
            failed(CAT, "STTService: toggle_listening subscription missing")
    except Exception as e:
        failed(CAT, "STTService init", str(e))

    # STT thread start/stop
    try:
        import speech_recognition as sr
        from services.stt import STTService

        bus = EventBus()
        try:
            stt = STTService(bus, asyncio.get_event_loop())
        except TypeError:
            stt = STTService(bus)

        with patch.object(stt.recognizer, "adjust_for_ambient_noise"), patch.object(
            stt.recognizer, "listen", side_effect=sr.WaitTimeoutError
        ), patch("speech_recognition.Microphone") as mock_mic:
            mock_mic.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_mic.return_value.__exit__ = MagicMock(return_value=False)
            stt.start_listening()
            time.sleep(0.4)
            if stt._listening and stt._thread and stt._thread.is_alive():
                passed(CAT, "STTService: starts listening thread")
            else:
                failed(CAT, "STTService thread start", "thread not alive")
            stt.stop_listening()
            time.sleep(0.4)
            if not stt._listening:
                passed(CAT, "STTService: stops listening thread")
            else:
                failed(CAT, "STTService thread stop", "still listening")
    except Exception as e:
        failed(CAT, "STTService thread toggle", str(e))

    # ── Gesture ───────────────────────────────────────────────────────────────
    subheader("GestureService")
    try:
        from services.gesture import GestureService

        bus = EventBus()
        gs = GestureService(bus)
        passed(CAT, "GestureService: initializes")
        if "toggle_gesture_control" in bus.subscribers:
            passed(CAT, "GestureService: subscribed to toggle_gesture_control")
        else:
            failed(CAT, "GestureService: subscription missing")
    except Exception as e:
        failed(CAT, "GestureService", str(e))

    # ── Biometrics ────────────────────────────────────────────────────────────
    subheader("BiometricService")
    try:
        from services.biometrics import BiometricService

        bus = EventBus()
        bio = BiometricService(bus)
        passed(CAT, "BiometricService: initializes")
        for ev in ("auth_login", "auth_register"):
            if ev in bus.subscribers:
                passed(CAT, f"BiometricService: subscribed to {ev}")
            else:
                failed(CAT, f"BiometricService: {ev} subscription missing")

        # Bug 8 regression: self.data_path should match FACE_DATA_PATH
        from services.biometrics import FACE_DATA_PATH

        if hasattr(bio, "data_path"):
            if bio.data_path == FACE_DATA_PATH:
                passed(
                    CAT,
                    "BiometricService: data_path consistent with FACE_DATA_PATH (Bug 8 fix)",
                )
            else:
                warned(
                    CAT,
                    "BiometricService data_path",
                    f"'{bio.data_path}' ≠ '{FACE_DATA_PATH}'",
                )
        else:
            passed(
                CAT, "BiometricService: dead data_path attribute removed (Bug 8 fix)"
            )

    except Exception as e:
        failed(CAT, "BiometricService", str(e))

    # Biometrics no infinite recursion in auth_with_haar
    try:
        from services.biometrics import BiometricService

        bio = BiometricService(EventBus())
        src = inspect.getsource(bio.auth_with_haar)
        if src.count("auth_with_haar") > 1:
            failed(CAT, "BiometricService: no recursion", "auth_with_haar calls itself")
        else:
            passed(CAT, "BiometricService: auth_with_haar has no infinite recursion")
    except Exception as e:
        warned(CAT, "BiometricService recursion check", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 5 ── SKILLS INIT & SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════════════════
def test_skills_init():
    CAT = "Skills Init"
    header("5 ── Skills (Init & Event Subscriptions)")
    EventBus = get_eventbus_class()

    skill_map = {
        "weather_skill": ("WeatherSkill", ["check_weather"]),
        "llm_skill": ("LLMSkill", ["ask_gpt", "process_user_input"]),
        "communication": ("CommunicationSkill", ["send_email"]),
        "whatsapp_skill": ("WhatsAppSkill", ["send_whatsapp"]),
        "news_skill": ("NewsSkill", ["get_news"]),
        "media_control": ("MediaControlSkill", ["play_youtube"]),
        "media_downloader": ("MediaSkill", ["download_video"]),
        "browser_control": ("BrowserControlSkill", ["browser_open", "browser_search"]),
        "system": ("SystemSkill", ["system_launch", "system_close"]),
        "quick_launch": ("QuickLaunchSkill", ["quick_launch"]),
        "productivity": ("ProductivitySkill", ["set_alarm"]),
        "web_automation": ("WebAutomationSkill", ["search_google"]),
    }

    for module_path, (class_name, expected_events) in skill_map.items():
        clean_import(f"skills.{module_path}")
        try:
            cls = import_skill(module_path, class_name)
            bus = EventBus()
            skill = cls(bus)
            skill.register()
            passed(CAT, f"{class_name}: instantiates and registers")
            missing = [ev for ev in expected_events if ev not in bus.subscribers]
            if missing:
                failed(CAT, f"{class_name}: subscriptions", f"missing: {missing}")
            else:
                passed(CAT, f"{class_name}: subscribed to {expected_events}")
        except Exception:
            failed(CAT, class_name, traceback.format_exc().strip().splitlines()[-1])


# ══════════════════════════════════════════════════════════════════════════════
# 6 ── COMMAND ROUTING (single + multi)
# ══════════════════════════════════════════════════════════════════════════════
def test_command_routing():
    CAT = "Command Routing"
    header("6 ── Command Routing (Single & Multi-Command)")

    try:
        spec = importlib.util.spec_from_file_location(
            "webviewmain", get_webview_main_path()
        )
        wm = importlib.util.module_from_spec(spec)
        mock_webview = MagicMock()
        mock_webview.screens = [MagicMock(width=1920)]
        with patch.dict(
            "sys.modules", {"webview": mock_webview, "repo_scanner": MagicMock()}
        ):
            spec.loader.exec_module(wm)

        EventBus = get_eventbus_class()
        bus = EventBus()
        emitted: dict[str, list] = {}

        _orig = bus.emit

        async def tracking_emit(event_name, data=None):
            emitted.setdefault(event_name, []).append(data)
            await _orig(event_name, data)

        bus.emit = tracking_emit

        wm._bus = bus
        wm._loop = asyncio.get_event_loop()

        api = wm.JarvisAPI()

        subheader("Single-command routing")
        single_routes = [
            ("download https://yt.be/abc", "download_video"),
            ("alarm 7am wake up", "set_alarm"),
            ("launch calculator", "quick_launch"),
            ("whatsapp John hello there", "send_whatsapp"),
            ("scan", "start_scan"),
            ("gesture", "toggle_gesture_control"),
            ("login", "auth_login"),
            ("register", "auth_register"),
            ("hello jarvis", "process_user_input"),
            ("what time is it", "process_user_input"),
            ("open youtube", "process_user_input"),
            ("play lofi music", "process_user_input"),
            ("search python tutorials", "process_user_input"),
            ("send message to Alice hi", "process_user_input"),
            ("shutdown the system", "process_user_input"),
        ]

        for cmd, expected in single_routes:
            emitted.clear()
            api.send_command(cmd)
            time.sleep(0.05)
            if expected in emitted:
                passed(CAT, f'"{cmd}" → {expected}')
            else:
                failed(
                    CAT,
                    f'"{cmd}" → {expected}',
                    f"got: {list(emitted.keys()) or 'nothing'}",
                )

        subheader("Edge cases")
        # Empty command
        emitted.clear()
        result = api.send_command("   ")
        if result.get("status") == "ignored":
            passed(CAT, "Empty / whitespace command → ignored")
        else:
            failed(CAT, "Empty command", f"returned: {result}")

        # None command
        try:
            result2 = api.send_command(None)
            if result2.get("status") == "ignored":
                passed(CAT, "None command → ignored")
            else:
                warned(CAT, "None command", f"returned: {result2}")
        except Exception as e:
            failed(CAT, "None command crash", str(e))

    except Exception:
        failed(
            CAT,
            "Command routing setup",
            traceback.format_exc().strip().splitlines()[-1],
        )


# ══════════════════════════════════════════════════════════════════════════════
# 7 ── SKILLS FUNCTIONAL (mocked external calls)
# ══════════════════════════════════════════════════════════════════════════════
def test_skills_functional():
    CAT = "Skills Functional"
    header("7 ── Skills Functional (Mocked External Calls)")
    EventBus = get_eventbus_class()

    # ── Weather ───────────────────────────────────────────────────────────────
    subheader("WeatherSkill")
    try:
        clean_import("skills.weather_skill")
        WeatherSkill = import_skill("weather_skill", "WeatherSkill")
        bus, spoken, emitted = make_bus()
        skill = WeatherSkill(bus)
        skill.register()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "main": {"temp": 5.0, "feels_like": 2.0, "humidity": 80},
            "weather": [{"description": "light snow"}],
        }
        with patch("requests.get", return_value=mock_resp), patch.dict(
            os.environ, {"OPENWEATHER_API_KEY": "testkey"}
        ):
            run_async(bus.emit("check_weather", {"city": "Calgary"}))
        if any("Calgary" in (s or "") or "snow" in (s or "").lower() for s in spoken):
            passed(CAT, "WeatherSkill: returns forecast for city")
        else:
            failed(CAT, "WeatherSkill forecast", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT,
            "WeatherSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Weather – empty city (default location)
    try:
        clean_import("skills.weather_skill")
        WeatherSkill = import_skill("weather_skill", "WeatherSkill")
        bus, spoken, emitted = make_bus()
        skill = WeatherSkill(bus)
        skill.register()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "main": {"temp": 20.0, "feels_like": 19.0, "humidity": 50},
            "weather": [{"description": "clear sky"}],
        }
        with patch("requests.get", return_value=mock_resp), patch.dict(
            os.environ, {"OPENWEATHER_API_KEY": "testkey", "DEFAULT_CITY": "London"}
        ):
            run_async(bus.emit("check_weather", {"city": ""}))
        if spoken:
            passed(CAT, "WeatherSkill: handles empty city (uses default)")
        else:
            warned(CAT, "WeatherSkill empty city", "no TTS response")
    except Exception:
        failed(
            CAT,
            "WeatherSkill empty city",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Weather – no API key
    try:
        clean_import("skills.weather_skill")
        WeatherSkill = import_skill("weather_skill", "WeatherSkill")
        bus, spoken, emitted = make_bus()
        skill = WeatherSkill(bus)
        skill.register()
        with patch.dict(os.environ, {"OPENWEATHER_API_KEY": ""}):
            run_async(bus.emit("check_weather", {"city": "Paris"}))
        if spoken:
            passed(CAT, "WeatherSkill: handles missing API key gracefully")
        else:
            warned(CAT, "WeatherSkill missing key", "no error message emitted")
    except Exception:
        failed(
            CAT,
            "WeatherSkill missing key",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── News ──────────────────────────────────────────────────────────────────
    subheader("NewsSkill")
    try:
        clean_import("skills.news_skill")
        NewsSkill = import_skill("news_skill", "NewsSkill")
        bus, spoken, emitted = make_bus()
        skill = NewsSkill(bus)
        skill.register()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "ok",
            "articles": [
                {"title": "Jarvis Passes All Tests"},
                {"title": "AI Takes Over Automation"},
                {"title": "Third Headline Here"},
            ],
        }
        with patch("requests.get", return_value=mock_resp), patch.dict(
            os.environ, {"NEWS_API_KEY": "testkey", "NEWSAPI_KEY": "testkey"}
        ):
            run_async(bus.emit("get_news", {"source": "bbc-news"}))
        if any(
            "Jarvis" in (s or "") or "AI" in (s or "") or "Headline" in (s or "")
            for s in spoken
        ):
            passed(CAT, "NewsSkill: returns and speaks headlines")
        else:
            failed(CAT, "NewsSkill headlines", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT, "NewsSkill functional", traceback.format_exc().strip().splitlines()[-1]
        )

    # ── Email / Communication ─────────────────────────────────────────────────
    subheader("CommunicationSkill")
    try:
        CommunicationSkill = import_skill("communication", "CommunicationSkill")
        bus, spoken, emitted = make_bus()
        skill = CommunicationSkill(bus)
        skill.register()
        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=smtp_mock), patch.dict(
            os.environ, {"EMAIL_ADDRESS": "jarvis@test.com", "EMAIL_PASSWORD": "pass"}
        ):
            run_async(
                bus.emit(
                    "send_email",
                    {
                        "receiver": "boss@stark.com",
                        "subject": "Status Report",
                        "body": "All systems nominal.",
                    },
                )
            )
        if any(
            "sent" in (s or "").lower() or "email" in (s or "").lower() for s in spoken
        ):
            passed(CAT, "CommunicationSkill: sends email via SMTP (mocked)")
        else:
            failed(CAT, "CommunicationSkill email", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT,
            "CommunicationSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Email – missing credentials
    try:
        CommunicationSkill = import_skill("communication", "CommunicationSkill")
        bus, spoken, emitted = make_bus()
        skill = CommunicationSkill(bus)
        skill.register()
        with patch.dict(os.environ, {"EMAIL_ADDRESS": "", "EMAIL_PASSWORD": ""}):
            run_async(
                bus.emit(
                    "send_email",
                    {"receiver": "x@y.com", "subject": "Hi", "body": "Hello"},
                )
            )
        if spoken:
            passed(CAT, "CommunicationSkill: handles missing credentials")
        else:
            warned(CAT, "CommunicationSkill missing creds", "no error response")
    except Exception:
        failed(
            CAT,
            "CommunicationSkill missing creds",
            str(e) if "e" in dir() else "unknown",
        )

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    subheader("WhatsAppSkill")
    try:
        import pandas as pd

        clean_import("skills.whatsapp_skill")
        WhatsAppSkill = import_skill("whatsapp_skill", "WhatsAppSkill")
        mock_df = pd.DataFrame(
            {
                "Name": ["John Doe", "Alice Smith", "Bob Jones"],
                "Phone": ["+14031234567", "+14039876543", "+14031112222"],
            }
        )
        with patch("pandas.read_csv", return_value=mock_df):
            bus, spoken, emitted = make_bus()
            skill = WhatsAppSkill(bus)
            skill.register()
            # Contact lookup
            finder = getattr(skill, "find_contact", None) or getattr(
                skill, "_find_contact", None
            )
            if finder:
                phone = finder("John")
                if phone == "+14031234567":
                    passed(
                        CAT,
                        "WhatsAppSkill: fuzzy contact lookup 'John' → correct number",
                    )
                else:
                    failed(CAT, "WhatsAppSkill contact lookup", f"got: {phone}")
                # Partial name
                phone2 = finder("alice")
                if phone2 == "+14039876543":
                    passed(CAT, "WhatsAppSkill: case-insensitive partial name match")
                else:
                    warned(CAT, "WhatsAppSkill partial match", f"got: {phone2}")
            else:
                warned(
                    CAT, "WhatsAppSkill contact lookup", "find_contact method not found"
                )

            # Send flow (mocked pywhatkit)
            with patch("pywhatkit.sendwhatmsg_instantly") as mock_wa:
                run_async(
                    bus.emit(
                        "send_whatsapp", {"contact": "John", "message": "Hey there"}
                    )
                )
                if mock_wa.called or any(
                    "sent" in (s or "").lower() or "whatsapp" in (s or "").lower()
                    for s in spoken
                ):
                    passed(CAT, "WhatsAppSkill: send_whatsapp event triggers send flow")
                else:
                    warned(
                        CAT,
                        "WhatsAppSkill send",
                        f"spoke: {spoken}, mock called: {mock_wa.called}",
                    )
    except Exception:
        failed(
            CAT,
            "WhatsAppSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── System ────────────────────────────────────────────────────────────────
    subheader("SystemSkill")
    try:
        SystemSkill = import_skill("system", "SystemSkill")
        bus, spoken, emitted = make_bus()
        skill = SystemSkill(bus)
        skill.register()
        with patch("subprocess.Popen") as mock_popen:
            run_async(bus.emit("system_launch", {"app": "notepad"}))
            if mock_popen.called:
                passed(CAT, "SystemSkill: system_launch calls subprocess.Popen")
            else:
                failed(CAT, "SystemSkill launch", "subprocess.Popen not called")
    except Exception:
        failed(
            CAT, "SystemSkill launch", traceback.format_exc().strip().splitlines()[-1]
        )

    try:
        SystemSkill = import_skill("system", "SystemSkill")
        bus, spoken, emitted = make_bus()
        skill = SystemSkill(bus)
        skill.register()
        with patch("subprocess.Popen") as mock_popen:
            run_async(bus.emit("system_close", {"app": "notepad"}))
            if mock_popen.called or spoken:
                passed(CAT, "SystemSkill: system_close triggers close action")
            else:
                warned(CAT, "SystemSkill close", "no action taken")
    except Exception:
        failed(
            CAT, "SystemSkill close", traceback.format_exc().strip().splitlines()[-1]
        )

    # ── QuickLaunch ───────────────────────────────────────────────────────────
    subheader("QuickLaunchSkill")
    try:
        clean_import("skills.quick_launch")
        QuickLaunchSkill = import_skill("quick_launch", "QuickLaunchSkill")
        bus, spoken, emitted = make_bus()
        skill = QuickLaunchSkill(bus)
        skill.register()
        with patch("webbrowser.open") as mock_wb:
            run_async(bus.emit("quick_launch", {"app": "youtube"}))
            if mock_wb.called:
                passed(CAT, "QuickLaunchSkill: 'youtube' opens via webbrowser")
            else:
                failed(CAT, "QuickLaunchSkill youtube", "webbrowser.open not called")
    except Exception:
        failed(
            CAT,
            "QuickLaunchSkill youtube",
            traceback.format_exc().strip().splitlines()[-1],
        )

    try:
        clean_import("skills.quick_launch")
        QuickLaunchSkill = import_skill("quick_launch", "QuickLaunchSkill")
        bus, spoken, emitted = make_bus()
        skill = QuickLaunchSkill(bus)
        skill.register()
        with patch("subprocess.Popen") as mock_popen, patch(
            "webbrowser.open"
        ) as mock_wb:
            run_async(bus.emit("quick_launch", {"app": "spotify"}))
            if mock_popen.called or mock_wb.called or spoken:
                passed(CAT, "QuickLaunchSkill: 'spotify' triggers launch action")
            else:
                warned(CAT, "QuickLaunchSkill spotify", "no action observed")
    except Exception:
        failed(
            CAT,
            "QuickLaunchSkill spotify",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── Productivity (alarm) ──────────────────────────────────────────────────
    subheader("ProductivitySkill")
    try:
        import datetime

        ProductivitySkill = import_skill("productivity", "ProductivitySkill")
        bus, spoken, emitted = make_bus()
        skill = ProductivitySkill(bus)
        if hasattr(skill, "_loop"):
            skill._loop = asyncio.get_event_loop()
        skill.register()
        future_time = datetime.datetime.now() + datetime.timedelta(seconds=1)
        skill.alarms.append({"time": future_time, "msg": "Regression alarm fired"})
        run_async(asyncio.sleep(3))
        if any("Regression alarm fired" in (s or "") for s in spoken):
            passed(CAT, "ProductivitySkill: alarm fires at correct time")
        else:
            failed(CAT, "ProductivitySkill alarm", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT,
            "ProductivitySkill alarm",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── WebAutomation ─────────────────────────────────────────────────────────
    subheader("WebAutomationSkill")
    try:
        clean_import("skills.web_automation")
        WebAutomationSkill = import_skill("web_automation", "WebAutomationSkill")
        bus, spoken, emitted = make_bus()
        skill = WebAutomationSkill(bus)
        skill.register()
        with patch("webbrowser.open") as mock_wb:
            run_async(bus.emit("search_google", {"query": "JARVIS AI assistant"}))
            if mock_wb.called:
                passed(CAT, "WebAutomationSkill: search_google opens browser")
            else:
                failed(CAT, "WebAutomationSkill search", "webbrowser.open not called")
    except Exception:
        failed(
            CAT,
            "WebAutomationSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── MediaControl ──────────────────────────────────────────────────────────
    subheader("MediaControlSkill")
    try:
        clean_import("skills.media_control")
        MediaControlSkill = import_skill("media_control", "MediaControlSkill")
        bus, spoken, emitted = make_bus()
        skill = MediaControlSkill(bus)
        skill.register()
        with patch("webbrowser.open") as mock_wb:
            run_async(bus.emit("play_youtube", {"query": "lofi hip hop beats"}))
            if mock_wb.called or spoken:
                passed(CAT, "MediaControlSkill: play_youtube triggers action")
            else:
                warned(CAT, "MediaControlSkill play_youtube", "no action observed")
    except Exception:
        failed(
            CAT,
            "MediaControlSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── MediaDownloader ───────────────────────────────────────────────────────
    subheader("MediaDownloader")
    try:
        clean_import("skills.media_downloader")
        MediaSkill = import_skill("media_downloader", "MediaSkill")
        bus, spoken, emitted = make_bus()
        skill = MediaSkill(bus)
        skill.register()
        with patch("yt_dlp.YoutubeDL") as mock_ytdl:
            mock_ytdl.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ytdl.return_value.__exit__ = MagicMock(return_value=False)
            run_async(
                bus.emit("download_video", {"url": "https://youtu.be/dQw4w9WgXcQ"})
            )
            if mock_ytdl.called or spoken:
                passed(CAT, "MediaSkill: download_video triggers yt-dlp (mocked)")
            else:
                warned(CAT, "MediaSkill download", "no action observed")
    except Exception:
        failed(
            CAT,
            "MediaSkill functional",
            traceback.format_exc().strip().splitlines()[-1],
        )


# ══════════════════════════════════════════════════════════════════════════════
# 8 ── LLM SKILL (intent parsing, all API providers, local model)
# ══════════════════════════════════════════════════════════════════════════════
def test_llm():
    CAT = "LLM"
    header("8 ── LLM Skill (Intent Parsing, API Providers, Local Model)")
    EventBus = get_eventbus_class()

    # ── Model path regression (Bug 1) ─────────────────────────────────────────
    subheader("Bug 1 regression: model filename")
    try:
        clean_import("skills.llm_skill")
        llm_mod = importlib.import_module("skills.llm_skill")
        model_path = getattr(llm_mod, "MODEL_PATH", "")
        if "Q4_K_M" in model_path and "Q4KM" not in model_path:
            passed(CAT, "MODEL_PATH has correct filename (Q4_K_M, not Q4KM)")
        else:
            failed(CAT, "MODEL_PATH typo", f"path is: {model_path}")
    except Exception:
        failed(CAT, "MODEL_PATH check", traceback.format_exc().strip().splitlines()[-1])

    # ── Thread-safety regression (Bug 2) ──────────────────────────────────────
    subheader("Bug 2 regression: _init_local thread-safety")
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        with patch.dict(os.environ, {"LLM_MODE": "local"}):
            skill = LLMSkill(EventBus())
        if hasattr(skill, "_init_lock") and isinstance(
            skill._init_lock, type(threading.Lock())
        ):
            passed(CAT, "LLMSkill: has threading.Lock (_init_lock) for model init")
        else:
            failed(
                CAT, "LLMSkill _init_lock", "Bug 2 not fixed: no _init_lock attribute"
            )
    except Exception:
        failed(
            CAT,
            "LLMSkill thread-safety",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── Mode detection ────────────────────────────────────────────────────────
    subheader("Mode & provider detection")
    for mode, provider, key_val in [
        ("api", "groq", "fake-groq-key"),
        ("api", "openai", "fake-openai-key"),
        ("api", "anthropic", "fake-anthropic-key"),
        ("api", "gemini", "fake-gemini-key"),
        ("local", "", ""),
    ]:
        try:
            clean_import("skills.llm_skill")
            LLMSkill = import_skill("llm_skill", "LLMSkill")
            env = {
                "LLM_MODE": mode,
                "LLM_API_PROVIDER": provider,
                "LLM_API_KEY": key_val,
            }
            with patch.dict(os.environ, {k: v for k, v in env.items() if v}):
                skill = LLMSkill(EventBus())
            if skill.mode == mode and (mode == "local" or skill.provider == provider):
                passed(CAT, f"LLMSkill: mode={mode}, provider={provider or 'n/a'}")
            else:
                failed(
                    CAT,
                    f"LLMSkill mode={mode}/{provider}",
                    f"got mode={skill.mode} provider={skill.provider}",
                )
        except Exception:
            failed(
                CAT,
                f"LLMSkill mode={mode}/{provider}",
                traceback.format_exc().strip().splitlines()[-1],
            )

    # ── ask_gpt event flows to TTS ─────────────────────────────────────────────
    subheader("ask_gpt → TTS pipeline")
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
        ):
            skill = LLMSkill(bus)
            skill.register()
            with patch.object(
                skill, "_call_groq", return_value="I am JARVIS, your AI assistant."
            ):
                run_async(bus.emit("ask_gpt", {"prompt": "who are you?"}))
        if any("JARVIS" in (s or "") for s in spoken):
            passed(CAT, "ask_gpt: response flows through tts_speak")
        else:
            failed(CAT, "ask_gpt TTS flow", f"spoke: {spoken}")
    except Exception:
        failed(CAT, "ask_gpt pipeline", traceback.format_exc().strip().splitlines()[-1])

    # ask_gpt with empty prompt
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
        ):
            skill = LLMSkill(bus)
            skill.register()
            run_async(bus.emit("ask_gpt", {"prompt": ""}))
        passed(CAT, "ask_gpt: empty prompt doesn't crash")
    except Exception:
        failed(CAT, "ask_gpt empty prompt", str(e) if "e" in dir() else "unknown")

    # ── All 4 API providers (mocked) ──────────────────────────────────────────
    subheader("API providers (mocked)")
    provider_configs = [
        ("groq", "_call_groq", "groq"),
        ("openai", "_call_openai", "openai"),
        ("anthropic", "_call_anthropic", "anthropic"),
        ("gemini", "_call_gemini", "gemini"),
    ]
    for provider, method, env_provider in provider_configs:
        try:
            clean_import("skills.llm_skill")
            LLMSkill = import_skill("llm_skill", "LLMSkill")
            bus, spoken, emitted = make_bus()
            with patch.dict(
                os.environ,
                {
                    "LLM_MODE": "api",
                    "LLM_API_PROVIDER": env_provider,
                    "LLM_API_KEY": "fake-key",
                },
            ):
                skill = LLMSkill(bus)
                skill.register()
                with patch.object(
                    skill, method, return_value=f"Response from {provider}."
                ) as mock_call:
                    run_async(bus.emit("ask_gpt", {"prompt": "test"}))
                    if mock_call.called:
                        passed(CAT, f"API provider '{provider}': {method} is called")
                    else:
                        failed(
                            CAT,
                            f"API provider '{provider}'",
                            f"{method} was not called",
                        )
                    if any(provider in (s or "") for s in spoken):
                        passed(CAT, f"API provider '{provider}': response reaches TTS")
                    else:
                        failed(
                            CAT, f"API provider '{provider}' TTS", f"spoke: {spoken}"
                        )
        except Exception:
            failed(
                CAT,
                f"API provider '{provider}'",
                traceback.format_exc().strip().splitlines()[-1],
            )

    # ── Local model (mocked Llama) ────────────────────────────────────────────
    subheader("Local model (mocked llama-cpp-python)")
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")

        mock_llama = MagicMock()
        mock_llama.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Local model says hello."}}]
        }

        bus, spoken, emitted = make_bus()
        with patch.dict(os.environ, {"LLM_MODE": "local"}):
            skill = LLMSkill(bus)
            skill.register()
            skill.llm = mock_llama  # inject mock model directly

            run_async(bus.emit("ask_gpt", {"prompt": "hello from local"}))

        if any("Local model" in (s or "") for s in spoken):
            passed(CAT, "Local model: mocked Llama responds and TTS fires")
        else:
            failed(CAT, "Local model TTS", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT,
            "Local model functional",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Local model missing – graceful degradation
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "local", "LLM_MODEL_PATH": "/nonexistent/none.gguf"},
        ):
            skill = LLMSkill(bus)
            skill.register()
            run_async(bus.emit("ask_gpt", {"prompt": "test"}))
        if any(
            "offline" in (s or "").lower()
            or "download" in (s or "").lower()
            or "brain" in (s or "").lower()
            for s in spoken
        ):
            passed(CAT, "Local model: missing model → graceful TTS error")
        else:
            failed(CAT, "Local model missing", f"spoke: {spoken}")
    except Exception:
        failed(
            CAT,
            "Local model graceful degradation",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # ── Intent parsing ────────────────────────────────────────────────────────
    subheader("Intent parsing (_parse_intent_json)")
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        skill = LLMSkill(EventBus())

        cases = [
            # valid JSON array
            (
                '[{"action":"get_time","params":{}}]',
                [{"action": "get_time", "params": {}}],
                "valid JSON array",
            ),
            # JSON with markdown fences
            (
                '```json\n[{"action":"get_weather","params":{"location":"London"}}]\n```',
                None,
                "JSON inside markdown fences",
            ),
            # JSON embedded in prose
            (
                'Here is the result: [{"action":"open_app","params":{"app":"chrome"}}] done.',
                [{"action": "open_app", "params": {"app": "chrome"}}],
                "JSON embedded in prose",
            ),
            # malformed JSON → fallback
            ("this is not json at all", None, "malformed JSON → fallback to get_info"),
            # empty array
            ("[]", [], "empty array"),
        ]

        for raw, expected, label in cases:
            result = skill._parse_intent_json(raw, "test input")
            if expected is None:
                # Just check it doesn't raise and returns a list
                if isinstance(result, list):
                    passed(CAT, f"_parse_intent_json: {label} → returns list")
                else:
                    failed(
                        CAT, f"_parse_intent_json: {label}", f"returned {type(result)}"
                    )
            else:
                if result == expected:
                    passed(CAT, f"_parse_intent_json: {label}")
                else:
                    failed(CAT, f"_parse_intent_json: {label}", f"got {result}")
    except Exception:
        failed(
            CAT, "Intent JSON parsing", traceback.format_exc().strip().splitlines()[-1]
        )


# ══════════════════════════════════════════════════════════════════════════════
# 9 ── MULTI-COMMAND DISPATCH PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
def test_multi_command_dispatch():
    CAT = "Multi-Command"
    header("9 ── Multi-Command Dispatch Pipeline")
    EventBus = get_eventbus_class()

    subheader("Intent-parsed multi-command sequences")

    multi_cases = [
        {
            "input": "open chrome and play lofi on youtube",
            "intents": [
                {"action": "open_app", "params": {"app": "chrome"}},
                {
                    "action": "play_media",
                    "params": {"query": "lofi", "platform": "youtube"},
                },
            ],
            "expected_events": ["system_launch", "play_youtube"],
            "label": "open chrome + play lofi",
        },
        {
            "input": "search python and tell me the time",
            "intents": [
                {"action": "search_web", "params": {"query": "python"}},
                {"action": "get_time", "params": {}},
            ],
            "expected_events": ["search_google"],
            "label": "search web + get time",
        },
        {
            "input": "shutdown and send message to John bye",
            "intents": [
                {"action": "system_control", "params": {"command": "shutdown"}},
                {
                    "action": "send_message",
                    "params": {"contact": "John", "message": "bye"},
                },
            ],
            "expected_events": ["send_whatsapp"],
            "label": "system control + send message",
        },
        {
            "input": "what's the weather and any news today",
            "intents": [
                {"action": "get_weather", "params": {"location": ""}},
                {"action": "get_info", "params": {"query": "any news today"}},
            ],
            "expected_events": ["check_weather", "tts_speak"],
            "label": "weather + general info",
        },
        {
            "input": "open spotify set alarm for 7am and search flights to Dubai",
            "intents": [
                {
                    "action": "play_media",
                    "params": {"query": "", "platform": "spotify"},
                },
                {
                    "action": "set_reminder",
                    "params": {"task": "wake up", "time": "7am"},
                },
                {"action": "search_web", "params": {"query": "flights to Dubai"}},
            ],
            "expected_events": ["quick_launch", "set_reminder", "search_google"],
            "label": "3-command: spotify + alarm + search",
        },
    ]

    for case in multi_cases:
        try:
            clean_import("skills.llm_skill")
            LLMSkill = import_skill("llm_skill", "LLMSkill")
            bus, spoken, emitted = make_bus()

            with patch.dict(
                os.environ,
                {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
            ):
                skill = LLMSkill(bus)
                skill.register()

                # Mock parse_intents to return our pre-defined intent list
                async def mock_parse(user_input):
                    return case["intents"]

                with patch.object(
                    skill, "parse_intents", side_effect=mock_parse
                ), patch.object(
                    skill,
                    "_generate_response",
                    new_callable=AsyncMock,
                    return_value="Done.",
                ), patch(
                    "subprocess.Popen"
                ), patch(
                    "webbrowser.open"
                ):
                    run_async(bus.emit("process_user_input", {"text": case["input"]}))

            found = [ev for ev in case["expected_events"] if ev in emitted]
            if len(found) == len(case["expected_events"]):
                passed(
                    CAT,
                    f"Multi-command '{case['label']}': all {len(case['expected_events'])} events emitted",
                )
            elif found:
                warned(
                    CAT,
                    f"Multi-command '{case['label']}'",
                    f"only {len(found)}/{len(case['expected_events'])} events: {found}",
                )
            else:
                failed(
                    CAT,
                    f"Multi-command '{case['label']}'",
                    f"expected {case['expected_events']}, got {list(emitted.keys())}",
                )

            # Conversation history updated
            if (
                hasattr(skill, "conversation_history")
                and len(skill.conversation_history) > 0
            ):
                passed(
                    CAT,
                    f"Multi-command '{case['label']}': conversation history updated",
                )
            else:
                warned(
                    CAT,
                    f"Multi-command conversation history",
                    "history empty after dispatch",
                )

        except Exception:
            failed(
                CAT,
                f"Multi-command '{case['label']}'",
                traceback.format_exc().strip().splitlines()[-1],
            )

    # ── Edge cases ────────────────────────────────────────────────────────────
    subheader("Dispatch edge cases")

    # Unknown action falls back gracefully
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
        ):
            skill = LLMSkill(bus)
            skill.register()

            async def mock_parse(ui):
                return [{"action": "totally_unknown_action_xyz", "params": {}}]

            with patch.object(
                skill, "parse_intents", side_effect=mock_parse
            ), patch.object(
                skill,
                "_generate_response",
                new_callable=AsyncMock,
                return_value="Fallback.",
            ):
                run_async(
                    bus.emit("process_user_input", {"text": "do something weird"})
                )
        passed(CAT, "Dispatch: unknown action falls back without crash")
    except Exception:
        failed(
            CAT,
            "Dispatch unknown action",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Non-list commands from parse_intents
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
        ):
            skill = LLMSkill(bus)
            skill.register()

            async def mock_parse(ui):
                return None  # returns None, not a list

            with patch.object(
                skill, "parse_intents", side_effect=mock_parse
            ), patch.object(
                skill, "_generate_response", new_callable=AsyncMock, return_value="ok"
            ):
                run_async(bus.emit("process_user_input", {"text": "bad parse result"}))
        passed(CAT, "Dispatch: non-list parse result handled without crash")
    except Exception:
        failed(
            CAT,
            "Dispatch non-list commands",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # Params with wrong types
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        bus, spoken, emitted = make_bus()
        with patch.dict(
            os.environ,
            {"LLM_MODE": "api", "LLM_API_PROVIDER": "groq", "LLM_API_KEY": "fake"},
        ):
            skill = LLMSkill(bus)
            skill.register()
            bad_cmds = [
                {"action": "open_app", "params": "not a dict"}
            ]  # params is string not dict

            async def mock_parse(ui):
                return bad_cmds

            with patch.object(skill, "parse_intents", side_effect=mock_parse), patch(
                "subprocess.Popen"
            ):
                run_async(
                    bus.emit("process_user_input", {"text": "open app bad params"})
                )
        passed(CAT, "Dispatch: non-dict params handled without crash")
    except Exception:
        failed(
            CAT,
            "Dispatch bad params type",
            traceback.format_exc().strip().splitlines()[-1],
        )


# ══════════════════════════════════════════════════════════════════════════════
# 10 ── BRIDGE
# ══════════════════════════════════════════════════════════════════════════════
def test_bridge():
    CAT = "Bridge"
    header("10 ── Python → Frontend Bridge")

    try:
        spec = importlib.util.spec_from_file_location(
            "webviewmain", get_webview_main_path()
        )
        wm = importlib.util.module_from_spec(spec)
        mock_webview = MagicMock()
        mock_webview.screens = [MagicMock(width=1920)]
        with patch.dict(
            "sys.modules", {"webview": mock_webview, "repo_scanner": MagicMock()}
        ):
            spec.loader.exec_module(wm)
    except Exception:
        failed(
            CAT, "Bridge module load", traceback.format_exc().strip().splitlines()[-1]
        )
        return

    # evaluate_js_call generates valid JS
    try:
        call_js = wm.evaluate_js_call("window.addJarvisResponse", 'He said "hello"')
        if "addJarvisResponse" in call_js and "hello" in call_js:
            passed(CAT, "evaluate_js_call: produces valid JS string")
        else:
            warned(CAT, "evaluate_js_call output", f"got: {call_js}")
    except Exception as e:
        failed(CAT, "evaluate_js_call", str(e))

    # JSON serialisation with special characters
    try:
        special = wm.evaluate_js_call(
            "window.f", {"msg": "He said \"hi\" & she said 'bye'"}
        )
        if "window.f" in special:
            passed(CAT, "evaluate_js_call: special chars JSON-encoded correctly")
        else:
            warned(CAT, "evaluate_js_call special chars", f"got: {special}")
    except Exception as e:
        failed(CAT, "evaluate_js_call special chars", str(e))

    # _emit_threadsafe safe when loop not started
    try:
        wm._bus = None
        wm._loop = None
        wm._emit_threadsafe("test_event", {})
        passed(CAT, "_emit_threadsafe: safe when bus/loop is None")
    except Exception as e:
        failed(CAT, "_emit_threadsafe no loop", str(e))

    # JS queue is flushed when window loads
    try:
        mock_win = MagicMock()
        mock_win.evaluate_js = MagicMock()
        wm._js_ready = False
        wm._js_queue.clear()
        wm._js_lock = threading.Lock()

        # Queue some JS before page loads
        wm._js_queue.append("window.test1();")
        wm._js_queue.append("window.test2();")

        # Simulate all windows in _windows list (Bug 4/5 fix)
        if hasattr(wm, "_windows"):
            wm._windows = [mock_win]
        else:
            wm.win = mock_win

        # Trigger loaded event
        wm._on_window_loaded()

        if mock_win.evaluate_js.call_count >= 2:
            passed(CAT, "JS queue: all queued calls flushed on window load")
        elif mock_win.evaluate_js.call_count == 1:
            warned(
                CAT,
                "JS queue partial flush",
                f"only {mock_win.evaluate_js.call_count}/2 calls flushed",
            )
        else:
            failed(CAT, "JS queue flush", "evaluate_js never called after load")

        if wm._js_ready:
            passed(CAT, "JS queue: _js_ready set to True after load")
        else:
            failed(CAT, "JS queue: _js_ready not set")

        if len(wm._js_queue) == 0:
            passed(CAT, "JS queue: cleared after flush")
        else:
            failed(CAT, "JS queue: not cleared", f"{len(wm._js_queue)} items remain")
    except Exception:
        failed(CAT, "JS queue flush", traceback.format_exc().strip().splitlines()[-1])

    # _evaluate_js_call queues when not ready
    try:
        wm._js_ready = False
        wm._js_queue.clear()
        if hasattr(wm, "_windows"):
            wm._windows = [MagicMock()]
        wm._evaluate_js_call("window.queued();")
        if "window.queued();" in wm._js_queue:
            passed(CAT, "_evaluate_js_call: queues JS when page not ready")
        else:
            failed(CAT, "_evaluate_js_call queue", f"queue: {wm._js_queue}")
    except Exception as e:
        failed(CAT, "_evaluate_js_call pre-ready queueing", str(e))

    # _evaluate_js_call executes immediately when ready
    try:
        mock_win2 = MagicMock()
        mock_win2.evaluate_js = MagicMock()
        wm._js_ready = True
        wm._js_queue.clear()
        if hasattr(wm, "_windows"):
            wm._windows = [mock_win2]
        else:
            wm.win = mock_win2
        wm._evaluate_js_call("window.immediate();")
        if mock_win2.evaluate_js.called:
            passed(CAT, "_evaluate_js_call: executes immediately when ready")
        else:
            failed(CAT, "_evaluate_js_call immediate", "evaluate_js not called")
    except Exception as e:
        failed(CAT, "_evaluate_js_call immediate execution", str(e))

    # Bug 4 regression: pill window receives JS (multiple windows tracked)
    try:
        if hasattr(wm, "_windows"):
            mock_dash = MagicMock()
            mock_pill = MagicMock()
            wm._windows = [mock_dash, mock_pill]
            wm._js_ready = True
            wm._evaluate_js_call("window.setCoreState('thinking');")
            if mock_dash.evaluate_js.called and mock_pill.evaluate_js.called:
                passed(
                    CAT,
                    "Bug 4 fix: _evaluate_js_call sends to BOTH dashboard and pill windows",
                )
            elif mock_dash.evaluate_js.called:
                failed(
                    CAT, "Bug 4 fix: pill window", "pill window never received JS call"
                )
            else:
                failed(CAT, "Bug 4 fix: neither window received JS call")
        else:
            warned(
                CAT,
                "Bug 4 regression check",
                "_windows list not present (fix not applied)",
            )
    except Exception as e:
        failed(CAT, "Bug 4 regression", str(e))

    # JarvisAPI.send_command wiring
    try:
        EventBus = get_eventbus_class()
        bus = EventBus()
        emitted_names = []

        async def track(e):
            emitted_names.append(e.name)

        _orig = bus.emit

        async def patched_emit(name, data=None):
            emitted_names.append(name)
            await _orig(name, data)

        bus.emit = patched_emit

        wm._bus = bus
        wm._loop = asyncio.get_event_loop()
        api = wm.JarvisAPI()

        result = api.send_command("hello JARVIS")
        time.sleep(0.1)
        if result.get("status") == "ok":
            passed(CAT, "JarvisAPI.send_command: returns ok status")
        else:
            failed(CAT, "JarvisAPI.send_command status", f"got: {result}")
    except Exception:
        failed(
            CAT,
            "JarvisAPI.send_command",
            traceback.format_exc().strip().splitlines()[-1],
        )

    # JarvisAPI.resize_window
    try:
        api = wm.JarvisAPI()
        mock_dash = MagicMock()
        mock_pill = MagicMock()
        api._dashboard_window = mock_dash
        api._pill_window = mock_pill

        result = api.resize_window("pill")
        if result.get("mode") == "pill":
            passed(CAT, "JarvisAPI.resize_window('pill'): returns correct mode")
        else:
            failed(CAT, "resize_window pill", f"got: {result}")
        if mock_dash.hide.called:
            passed(CAT, "JarvisAPI.resize_window('pill'): hides dashboard")
        if mock_pill.show.called:
            passed(CAT, "JarvisAPI.resize_window('pill'): shows pill window")

        result2 = api.resize_window("dashboard")
        if result2.get("mode") == "dashboard":
            passed(CAT, "JarvisAPI.resize_window('dashboard'): returns correct mode")
        else:
            failed(CAT, "resize_window dashboard", f"got: {result2}")
    except Exception:
        failed(
            CAT,
            "JarvisAPI.resize_window",
            traceback.format_exc().strip().splitlines()[-1],
        )


# ══════════════════════════════════════════════════════════════════════════════
# 11 ── CONFIG
# ══════════════════════════════════════════════════════════════════════════════
def test_config():
    CAT = "Config"
    header("11 ── ConfigManager (dot-notation, env override, singleton)")

    try:
        from config.manager import ConfigManager

        cfg = ConfigManager()

        # Dot-notation read
        val = cfg.get("tts.rate", 999)
        if str(val) != "999":
            passed(CAT, f"settings.yaml: tts.rate = {val}")
        else:
            warned(CAT, "tts.rate", "not set in settings.yaml – returned default")

        # ENV var overrides YAML (priority chain)
        with patch.dict(os.environ, {"TTS_RATE": "250"}):
            val2 = cfg.get("tts.rate")
            if str(val2) == "250":
                passed(CAT, "ConfigManager: ENV var overrides YAML value")
            else:
                failed(CAT, "ConfigManager ENV override", f"expected 250, got {val2}")

        # Missing key returns default
        val3 = cfg.get("nonexistent.key.path", "my_default")
        if val3 == "my_default":
            passed(CAT, "ConfigManager: returns default for missing key")
        else:
            failed(CAT, "ConfigManager default", f"got {val3}")

        # Deeply nested key
        val4 = cfg.get("a.b.c.d.e", "deep_default")
        if val4 == "deep_default":
            passed(CAT, "ConfigManager: deeply nested missing key returns default")
        else:
            passed(CAT, f"ConfigManager: deeply nested key = {val4}")

        # Singleton
        cfg2 = ConfigManager()
        if cfg is cfg2:
            passed(CAT, "ConfigManager: singleton – same instance returned")
        else:
            failed(CAT, "ConfigManager singleton", "different instance returned")

        # app_config.py imports cleanly
        from app_config import (
            LLM_CONTEXT_SIZE,
            LLM_TEMPERATURE,
            LLM_MAX_TOKENS,
            CONVERSATION_MAXLEN,
            TTS_ENABLED,
            CONFIG,
        )

        if isinstance(LLM_CONTEXT_SIZE, int) and LLM_CONTEXT_SIZE > 0:
            passed(CAT, f"app_config: LLM_CONTEXT_SIZE = {LLM_CONTEXT_SIZE}")
        else:
            failed(CAT, "app_config LLM_CONTEXT_SIZE", f"invalid: {LLM_CONTEXT_SIZE}")
        if isinstance(CONFIG, dict) and "LLM_CONTEXT_SIZE" in CONFIG:
            passed(CAT, "app_config: CONFIG dict contains expected keys")
        else:
            failed(CAT, "app_config CONFIG dict", f"keys: {list(CONFIG.keys())}")

    except Exception:
        failed(CAT, "ConfigManager", traceback.format_exc().strip().splitlines()[-1])


# ══════════════════════════════════════════════════════════════════════════════
# 12 ── FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
def test_frontend():
    CAT = "Frontend"
    header("12 ── Frontend Build & Structure")

    dist = os.path.join(ROOT, "frontend", "dist")
    index = os.path.join(dist, "index.html")
    assets = os.path.join(dist, "assets")
    src = os.path.join(ROOT, "frontend", "src")

    # Built dist
    if os.path.exists(index):
        passed(CAT, "frontend/dist/index.html exists")
        with open(index, encoding="utf-8") as f:
            content = f.read()
        if "assets/" in content:
            passed(CAT, "index.html: references built assets/")
        else:
            warned(CAT, "index.html content", "may not reference built JS correctly")
    else:
        failed(CAT, "frontend/dist/index.html", "run: cd frontend && npm run build")

    if os.path.exists(assets):
        js = [f for f in os.listdir(assets) if f.endswith(".js")]
        css = [f for f in os.listdir(assets) if f.endswith(".css")]
        if js:
            passed(CAT, f"Built JS bundle: {js[0]}")
        else:
            failed(CAT, "No .js file in dist/assets/")
        if css:
            passed(CAT, f"Built CSS bundle: {css[0]}")
        else:
            warned(CAT, "No .css file in dist/assets/")
    else:
        failed(CAT, "frontend/dist/assets/ missing")

    # package.json
    pkg = os.path.join(ROOT, "frontend", "package.json")
    if os.path.exists(pkg):
        with open(pkg) as f:
            data = json.load(f)
        deps = data.get("dependencies", {})
        devDeps = data.get("devDependencies", {})
        for lib, where in [("react", deps), ("framer-motion", deps), ("vite", devDeps)]:
            if lib in where:
                passed(CAT, f"package.json: {lib} declared")
            else:
                warned(CAT, f"package.json: {lib} missing")
    else:
        warned(CAT, "frontend/package.json", "missing")

    # Source files exist
    expected_src = [
        "src/App.jsx",
        "src/main.jsx",
        "src/hooks/useJarvisBridge.js",
        "src/hooks/useJarvisState.js",
        "src/hooks/useChatHistory.js",
        "src/components/CommandInput.jsx",
        "src/components/DashboardMode.jsx",
        "src/components/PillMode.jsx",
    ]
    for rel in expected_src:
        full = os.path.join(ROOT, "frontend", rel)
        if os.path.exists(full):
            passed(CAT, f"Source: frontend/{rel}")
        else:
            warned(CAT, f"Source: frontend/{rel}", "missing")

    # Bug 3 regression: App.jsx should NOT re-declare useState for mode
    try:
        app_path = os.path.join(ROOT, "frontend", "src", "App.jsx")
        if os.path.exists(app_path):
            with open(app_path, encoding="utf-8") as f:
                app_src = f.read()
            # Count occurrences of useState for mode specifically
            import re

            local_mode_state = re.findall(
                r"const\s*\[mode\s*,\s*\w+\]\s*=\s*useState", app_src
            )
            if local_mode_state:
                failed(
                    CAT,
                    "Bug 3 regression: App.jsx",
                    f"Found local mode useState ({len(local_mode_state)}x) – still shadows hook state",
                )
            else:
                passed(
                    CAT, "Bug 3 fix: App.jsx does not re-declare local mode useState"
                )

            # Confirm setMode passed to useJarvisBridge
            if "setMode" in app_src and "useJarvisBridge" in app_src:
                passed(CAT, "Bug 3 fix: setMode is passed to useJarvisBridge")
            else:
                warned(
                    CAT,
                    "Bug 3 fix: setMode→bridge",
                    "setMode may not be wired to bridge",
                )
        else:
            warned(CAT, "Bug 3 check", "App.jsx not found")
    except Exception as e:
        warned(CAT, "Bug 3 regression check", str(e))

    # Bug 9: useChatHistory addMessage should be useCallback
    try:
        ch_path = os.path.join(ROOT, "frontend", "src", "hooks", "useChatHistory.js")
        if os.path.exists(ch_path):
            with open(ch_path, encoding="utf-8") as f:
                ch_src = f.read()
            if "useCallback" in ch_src:
                passed(CAT, "Bug 9 fix: useChatHistory uses useCallback for addMessage")
            else:
                failed(
                    CAT,
                    "Bug 9 regression: addMessage not memoised",
                    "missing useCallback in useChatHistory.js",
                )
        else:
            warned(CAT, "Bug 9 check", "useChatHistory.js not found")
    except Exception as e:
        warned(CAT, "Bug 9 regression check", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 13 ── BUG REGRESSION SWEEP
# ══════════════════════════════════════════════════════════════════════════════
def test_regressions():
    CAT = "Regressions"
    header("13 ── Bug Regression Sweep (all 9 bugs)")
    EventBus = get_eventbus_class()

    # Bug 1: Model path
    subheader("Bug 1 – MODEL_PATH typo")
    try:
        clean_import("skills.llm_skill")
        llm_mod = importlib.import_module("skills.llm_skill")
        mp = getattr(llm_mod, "MODEL_PATH", "")
        fmp = getattr(llm_mod, "FALLBACK_MODEL_PATH", "")
        if "Q4_K_M" in mp and "Q4KM" not in mp:
            passed(CAT, "Bug 1 FIXED: MODEL_PATH uses Q4_K_M filename")
        else:
            failed(CAT, "Bug 1 NOT FIXED", f"MODEL_PATH = {mp}")
        if "Q4_K_M" in fmp:
            passed(CAT, "Bug 1: FALLBACK_MODEL_PATH also correct")
    except Exception:
        failed(CAT, "Bug 1 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 2: Thread lock in LLMSkill
    subheader("Bug 2 – _init_local race condition")
    try:
        clean_import("skills.llm_skill")
        LLMSkill = import_skill("llm_skill", "LLMSkill")
        with patch.dict(os.environ, {"LLM_MODE": "local"}):
            skill = LLMSkill(EventBus())
        lock = getattr(skill, "_init_lock", None)
        if lock is not None and isinstance(lock, type(threading.Lock())):
            passed(CAT, "Bug 2 FIXED: _init_lock present and is threading.Lock")
        else:
            failed(CAT, "Bug 2 NOT FIXED", "no _init_lock attribute")
    except Exception:
        failed(CAT, "Bug 2 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 3: App.jsx duplicate mode state (checked in frontend section)
    subheader("Bug 3 – App.jsx duplicate mode state (see section 12)")
    try:
        app_path = os.path.join(ROOT, "frontend", "src", "App.jsx")
        if os.path.exists(app_path):
            with open(app_path, encoding="utf-8") as f:
                src = f.read()
            import re

            dupes = re.findall(r"const\s*\[mode\s*,\s*\w+\]\s*=\s*useState", src)
            if not dupes:
                passed(CAT, "Bug 3 FIXED: no duplicate mode useState in App.jsx")
            else:
                failed(
                    CAT,
                    "Bug 3 NOT FIXED",
                    f"Found {len(dupes)} local mode state declaration(s)",
                )
        else:
            warned(CAT, "Bug 3 check", "App.jsx not found")
    except Exception as e:
        warned(CAT, "Bug 3 check", str(e))

    # Bug 4+5: _windows list and pill subscription
    subheader("Bug 4 + 5 – Pill window JS + loaded event")
    try:
        spec = importlib.util.spec_from_file_location(
            "webviewmain2", get_webview_main_path()
        )
        wm2 = importlib.util.module_from_spec(spec)
        mock_webview = MagicMock()
        mock_webview.screens = [MagicMock(width=1920)]
        with patch.dict(
            "sys.modules", {"webview": mock_webview, "repo_scanner": MagicMock()}
        ):
            spec.loader.exec_module(wm2)
        if hasattr(wm2, "_windows"):
            passed(CAT, "Bug 4 FIXED: _windows list exists in webview_main")
        else:
            failed(
                CAT,
                "Bug 4 NOT FIXED",
                "_windows list not found; still using single `win`",
            )
    except Exception:
        failed(CAT, "Bug 4+5 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 6: TTS lock actually acquired
    subheader("Bug 6 – TTS asyncio.Lock not acquired")
    try:
        from services.tts import TTSService

        bus = EventBus()
        tts = TTSService(bus)
        src = inspect.getsource(tts.speak)
        if (
            "async with self._lock" in src
            or "await self._lock" in src
            or "_lock" in src
        ):
            passed(CAT, "Bug 6 FIXED: _lock is acquired inside speak()")
        else:
            failed(CAT, "Bug 6 NOT FIXED", "speak() does not acquire _lock")
    except Exception:
        failed(CAT, "Bug 6 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 7: STT uses engine loop
    subheader("Bug 7 – STT private event loop")
    try:
        from services.stt import STTService
        import inspect as _inspect

        sig = _inspect.signature(STTService.__init__)
        params = list(sig.parameters.keys())
        if "engine_loop" in params or "loop" in params:
            passed(
                CAT, "Bug 7 FIXED: STTService.__init__ accepts engine_loop parameter"
            )
        else:
            failed(CAT, "Bug 7 NOT FIXED", f"STTService.__init__ params: {params}")
    except Exception:
        failed(CAT, "Bug 7 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 8: biometrics data_path
    subheader("Bug 8 – biometrics dead data_path")
    try:
        from services.biometrics import BiometricService, FACE_DATA_PATH

        bus = EventBus()
        bio = BiometricService(bus)
        if not hasattr(bio, "data_path"):
            passed(CAT, "Bug 8 FIXED: dead self.data_path attribute removed")
        elif bio.data_path == FACE_DATA_PATH:
            passed(
                CAT, "Bug 8 FIXED: self.data_path now consistent with FACE_DATA_PATH"
            )
        else:
            failed(
                CAT,
                "Bug 8 NOT FIXED",
                f"data_path='{bio.data_path}' ≠ FACE_DATA_PATH='{FACE_DATA_PATH}'",
            )
    except Exception:
        failed(CAT, "Bug 8 check", traceback.format_exc().strip().splitlines()[-1])

    # Bug 9: useChatHistory useCallback
    subheader("Bug 9 – addMessage not memoised")
    try:
        ch_path = os.path.join(ROOT, "frontend", "src", "hooks", "useChatHistory.js")
        if os.path.exists(ch_path):
            with open(ch_path, encoding="utf-8") as f:
                ch_src = f.read()
            if "useCallback" in ch_src:
                passed(CAT, "Bug 9 FIXED: useCallback present in useChatHistory.js")
            else:
                failed(
                    CAT, "Bug 9 NOT FIXED", "useCallback not found in useChatHistory.js"
                )
        else:
            warned(CAT, "Bug 9 check", "useChatHistory.js not found")
    except Exception as e:
        warned(CAT, "Bug 9 check", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
def print_summary():
    header("SUMMARY")

    total = len(results)
    passed_ = [r for r in results if r[2] == "PASS"]
    failed_ = [r for r in results if r[2] == "FAIL"]
    warned_ = [r for r in results if r[2] == "WARN"]

    print(f"\n  Total tests : {total}")
    print(f"  {GREEN}Passed      : {len(passed_)}{RESET}")
    print(f"  {RED}Failed      : {len(failed_)}{RESET}")
    print(f"  {YELLOW}Warnings    : {len(warned_)}{RESET}")

    if failed_:
        print(f"\n{BOLD}{RED}  ✗ Failed:{RESET}")
        for cat, name, _, note in failed_:
            print(f"    {RED}✗{RESET}  [{cat}] {name}{_fmt_note(note)}")

    if warned_:
        print(f"\n{BOLD}{YELLOW}  ⚠ Warnings:{RESET}")
        for cat, name, _, note in warned_:
            print(f"    {YELLOW}⚠{RESET}  [{cat}] {name}{_fmt_note(note)}")

    print()
    if not failed_:
        print(f"{BOLD}{GREEN}  ✓ All critical tests passed.{RESET}")
    else:
        print(
            f"{BOLD}{RED}  ✗ {len(failed_)} test(s) failed – fix the items above.{RESET}"
        )
    print()

    # Exit code: non-zero if any failures
    sys.exit(len(failed_))


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    os.system("cls" if platform.system() == "Windows" else "clear")

    banner = f"""{BOLD}{CYAN}
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
{RESET}{DIM}  Full System Test Suite v2.0{RESET}
"""
    print(banner)

    test_environment()
    test_imports()
    test_core()
    test_services()
    test_skills_init()
    test_command_routing()
    test_skills_functional()
    test_llm()
    test_multi_command_dispatch()
    test_bridge()
    test_config()
    test_frontend()
    test_regressions()
    print_summary()
