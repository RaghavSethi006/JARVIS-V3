"""
jarvis_cli.py  —  JARVIS Terminal Interface
============================================
A full Claude-Code-style terminal client for JARVIS.
Reuses the ENTIRE existing backend — same event bus, same skills,
same memory system, same LLM client. Zero duplication.

Usage:
    python jarvis_cli.py              # normal mode
    python jarvis_cli.py --debug      # show event flow
    python jarvis_cli.py --no-tts     # skip Kokoro audio output
    python jarvis_cli.py --no-memory  # disable memory system for this session

Special commands:
    /debug      toggle event tracing on/off
    /memory     dump current memory state (profile + entities + last episodes)
    /skills     list every registered skill and its subscribed events
    /events     show raw event log from this session
    /clear      clear terminal
    /exit       graceful shutdown with session archive
    /help       show this list

Place this file in the project root (same level as webview_main.py).
Run from the project root: python jarvis_cli.py
"""

import argparse
import asyncio
import os
import sys
import signal
import threading
import time
import textwrap
from collections import deque
from datetime import datetime

# ── Resolve project root ──────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Load .env before anything else ───────────────────────────────────────────
from config.manager import config  # noqa: F401  triggers dotenv load

# ── Colour / formatting helpers (zero dependencies) ──────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GREY = "\033[90m"


def c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"


def _width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def rule(char: str = "─") -> str:
    return c(GREY, char * _width())


def _wrap(text: str, indent: int = 4) -> str:
    prefix = " " * indent
    return textwrap.fill(
        text, width=_width() - indent, initial_indent=prefix, subsequent_indent=prefix
    )


# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
"""


# ── CLI state ─────────────────────────────────────────────────────────────────
class CLIState:
    def __init__(self):
        self.debug = False
        self.tts_enabled = True
        self.memory_enabled = True
        self.event_log = deque(maxlen=200)
        self.response_ready = asyncio.Event()
        self.last_response = ""
        self.thinking = False
        self.registered_skills: list[dict] = []  # {name, events}
        self._think_stop = threading.Event()
        self._think_thread = None

    # ── Thinking animation ────────────────────────────────────────────────
    def start_thinking(self):
        self._think_stop.clear()
        self._think_thread = threading.Thread(
            target=self._animate_thinking, daemon=True
        )
        self._think_thread.start()

    def stop_thinking(self):
        self._think_stop.set()
        if self._think_thread:
            self._think_thread.join(timeout=1)
        # Clear the animation line
        sys.stdout.write(f"\r{' ' * (_width())}\r")
        sys.stdout.flush()

    def _animate_thinking(self):
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        i = 0
        while not self._think_stop.is_set():
            frame = frames[i % len(frames)]
            sys.stdout.write(f"\r  {c(CYAN, frame)}  {c(DIM, 'JARVIS is thinking...')}")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1


_state = CLIState()


# ── Output helpers ────────────────────────────────────────────────────────────
def print_jarvis(text: str):
    """Print a JARVIS response with formatting."""
    _state.stop_thinking()
    ts = c(GREY, datetime.now().strftime("%H:%M:%S"))
    print(f"\n  {c(CYAN, 'J')} {ts}")
    # Stream character-by-character for that CLI feel
    print(f"  {c(CYAN, '│')}", end="  ")
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.012)
    print(f"\n  {c(CYAN, '╰')}{c(GREY, '─' * 40)}\n")


def print_event(name: str, data=None):
    """Print a debug event trace line."""
    if not _state.debug:
        return
    data_str = f" → {c(GREY, str(data)[:80])}" if data else ""
    print(f"  {c(MAGENTA, '◆')} {c(GREY, 'event')} {c(MAGENTA, name)}{data_str}")


def print_info(text: str):
    print(f"  {c(BLUE, '•')} {c(DIM, text)}")


def print_warn(text: str):
    print(f"  {c(YELLOW, '⚠')} {c(YELLOW, text)}")


def print_error(text: str):
    print(f"  {c(RED, '✗')} {c(RED, text)}")


def print_ok(text: str):
    print(f"  {c(GREEN, '✓')} {text}")


# ── Bus event hooks ───────────────────────────────────────────────────────────
def _make_bus_hooks(bus, loop):
    """Subscribe to bus events to drive the CLI display."""

    async def on_tts_speak(event):
        text = str(event.data or "").strip()
        if not text:
            return
        _state.last_response = text
        print_jarvis(text)
        _state.response_ready.set()

    async def on_set_core_state(event):
        state = str(event.data or "idle")
        _state.event_log.append(("set_core_state", state))
        print_event("set_core_state", state)
        if state == "thinking":
            _state.thinking = True
            _state.start_thinking()
        elif state == "idle" and _state.thinking:
            _state.thinking = False
            _state.stop_thinking()

    async def on_set_status(event):
        text = str(event.data or "")
        _state.event_log.append(("set_status", text))
        print_event("set_status", text)
        if _state.debug:
            print_info(f"Status: {text}")

    async def on_auth_success(event):
        print_ok("Biometric authentication successful.")

    async def on_auth_failed(event):
        print_warn("Biometric authentication failed.")

    async def on_any(name):
        async def _handler(event):
            _state.event_log.append((name, event.data))
            print_event(name, event.data)

        return _handler

    bus.subscribe("tts_speak", on_tts_speak)
    bus.subscribe("set_core_state", on_set_core_state)
    bus.subscribe("set_status", on_set_status)
    bus.subscribe("auth_success", on_auth_success)
    bus.subscribe("auth_failed", on_auth_failed)

    # In debug mode also trace these
    for ev in [
        "stt_recognition",
        "toggle_listening",
        "set_listening",
        "wake_word_detected",
        "auth_login",
        "auth_register",
        "download_video",
        "play_youtube",
        "search_google",
        "open_website",
        "browser_open",
        "browser_search",
        "send_email",
        "send_whatsapp",
        "check_weather",
        "get_news",
        "system_launch",
        "system_close",
        "quick_launch",
        "set_alarm",
        "set_reminder",
        "set_volume",
        "set_brightness",
        "take_screenshot",
        "tell_joke",
        "spotify_play",
        "spotify_pause",
        "get_calendar",
        "create_event",
        "read_emails",
    ]:
        asyncio.ensure_future(_register_trace(bus, ev))


async def _register_trace(bus, event_name: str):
    async def _handler(event):
        _state.event_log.append((event_name, event.data))
        print_event(event_name, event.data)

    bus.subscribe(event_name, _handler)


# ── Skill boot ────────────────────────────────────────────────────────────────
def _boot_skills(bus) -> list[str]:
    """
    Register all skills onto the bus.
    Same set as webview_main.py — CLI reuses everything.
    Returns list of (skill_name, [events]) for /skills command.
    """
    from core.database import init_db

    init_db()

    skills_info = []

    def _reg(skill_cls, *args):
        try:
            before = set()
            for subs in bus.subscribers.values():
                before.update(id(s) for s in subs)

            instance = skill_cls(bus, *args)
            instance.register()

            after_events = []
            for ev_name, subs in bus.subscribers.items():
                for s in subs:
                    if id(s) not in before:
                        after_events.append(ev_name)

            skills_info.append(
                {
                    "name": skill_cls.__name__,
                    "events": sorted(set(after_events)),
                    "ok": True,
                }
            )
        except Exception as exc:
            skills_info.append(
                {
                    "name": skill_cls.__name__,
                    "events": [],
                    "ok": False,
                    "error": str(exc),
                }
            )

    # ── Import all skills ─────────────────────────────────────────────────
    from skills.llm_skill import LLMSkill
    from skills.weather_skill import WeatherSkill
    from skills.browser_control import BrowserControlSkill
    from skills.media_control import MediaControlSkill
    from skills.media_downloader import MediaSkill
    from skills.news_skill import NewsSkill
    from skills.productivity import ProductivitySkill
    from skills.whatsapp_skill import WhatsAppSkill
    from skills.communication import CommunicationSkill
    from skills.quick_launch import QuickLaunchSkill
    from skills.system import SystemSkill
    from skills.system_control import SystemControlSkill
    from skills.spotify_skill import SpotifySkill
    from skills.calendar_skill import CalendarSkill
    from skills.web_automation import WebAutomationSkill

    for cls in [
        LLMSkill,
        WeatherSkill,
        BrowserControlSkill,
        MediaControlSkill,
        MediaSkill,
        NewsSkill,
        ProductivitySkill,
        WhatsAppSkill,
        CommunicationSkill,
        QuickLaunchSkill,
        SystemSkill,
        SystemControlSkill,
        SpotifySkill,
        CalendarSkill,
        WebAutomationSkill,
    ]:
        _reg(cls)

    return skills_info


# ── Special commands ──────────────────────────────────────────────────────────
async def _handle_special(cmd: str, bus, loop) -> bool:
    """
    Returns True if command was handled (don't pass to LLM).
    """
    cmd = cmd.strip().lower()

    # ── /debug ────────────────────────────────────────────────────────────
    if cmd == "/debug":
        _state.debug = not _state.debug
        status = c(GREEN, "ON") if _state.debug else c(RED, "OFF")
        print_info(f"Event tracing {status}")
        return True

    # ── /memory ───────────────────────────────────────────────────────────
    if cmd == "/memory":
        try:
            from core.memory import MemoryManager

            mm = MemoryManager.get()
            print(f"\n{rule()}")
            print(c(CYAN, "  MEMORY STATE"))
            print(rule())

            profile = mm.semantic.to_context_string()
            print(c(BOLD, "\n  User Profile"))
            print(_wrap(profile if profile else "No facts stored yet.", 4))

            facts = mm.semantic.get_all_facts()
            print(c(BOLD, f"\n  Stored Facts  ({len(facts)} total)"))
            for f in facts[:15]:
                print(
                    f"    {c(CYAN, f['category'])} › {f['key']}: {c(WHITE, f['value'])}"
                )
            if len(facts) > 15:
                print(c(GREY, f"    ... and {len(facts) - 15} more"))

            print(
                c(
                    BOLD,
                    f"\n  Working Memory  ({len(mm.working.exchanges)} exchanges this session)",
                )
            )
            for ex in list(mm.working.exchanges)[-6:]:
                role_c = CYAN if ex.role == "jarvis" else YELLOW
                print(f"    {c(role_c, ex.role.upper())}: {ex.content[:90]}")

            entities = mm.entity_store.search("", limit=10)
            if entities:
                print(c(BOLD, f"\n  Known Entities  ({len(entities)} shown)"))
                for e in entities:
                    print(
                        f"    {c(MAGENTA, e.get('type','?'))} › {c(WHITE, e.get('canonical_name','?'))}"
                    )

            print(f"\n{rule()}\n")
        except ImportError:
            print_warn("Memory system not available.")
        except Exception as exc:
            print_error(f"Memory read failed: {exc}")
        return True

    # ── /skills ───────────────────────────────────────────────────────────
    if cmd == "/skills":
        print(f"\n{rule()}")
        print(c(CYAN, "  REGISTERED SKILLS"))
        print(rule())
        for s in _state.registered_skills:
            status = c(GREEN, "✓") if s["ok"] else c(RED, "✗")
            evts = c(GREY, ", ".join(s["events"])) if s["events"] else c(GREY, "none")
            print(f"  {status}  {c(WHITE, s['name'])}")
            print(f"      {evts}")
            if not s["ok"]:
                print(f"      {c(RED, s.get('error', ''))}")
        print(f"\n{rule()}\n")
        return True

    # ── /events ───────────────────────────────────────────────────────────
    if cmd == "/events":
        print(f"\n{rule()}")
        print(c(CYAN, "  EVENT LOG (last 40)"))
        print(rule())
        for name, data in list(_state.event_log)[-40:]:
            data_str = f" → {str(data)[:70]}" if data else ""
            print(f"  {c(MAGENTA, '◆')} {c(WHITE, name)}{c(GREY, data_str)}")
        print(f"\n{rule()}\n")
        return True

    # ── /clear ────────────────────────────────────────────────────────────
    if cmd == "/clear":
        os.system("cls" if os.name == "nt" else "clear")
        _print_mini_banner()
        return True

    # ── /exit ─────────────────────────────────────────────────────────────
    if cmd in ("/exit", "/quit", "exit", "quit"):
        print_info("Archiving session memory...")
        try:
            from core.memory import MemoryManager

            mm = MemoryManager.get()
            await mm.close_session()
            print_ok("Session archived.")
        except Exception:
            pass
        print(c(CYAN, "\n  Goodbye, sir.\n"))
        loop.stop()
        return True

    # ── /help ─────────────────────────────────────────────────────────────
    if cmd in ("/help", "/?"):
        help_text = [
            ("/debug", "Toggle live event tracing"),
            ("/memory", "Dump current memory state"),
            ("/skills", "List registered skills and events"),
            ("/events", "Show raw event log from this session"),
            ("/clear", "Clear terminal"),
            ("/exit", "Graceful shutdown with session archive"),
        ]
        print(f"\n{rule()}")
        print(c(CYAN, "  COMMANDS"))
        print(rule())
        for cmd_name, desc in help_text:
            print(f"  {c(CYAN, cmd_name):<28} {c(GREY, desc)}")
        print(f"\n{rule()}\n")
        return True

    return False


# ── Mini banner for /clear ────────────────────────────────────────────────────
def _print_mini_banner():
    print(
        c(
            CYAN,
            f"\n  JARVIS  {c(GREY, '─')} Terminal Interface  {c(GREY, '─')} type /help for commands\n",
        )
    )


# ── Input loop ────────────────────────────────────────────────────────────────
async def _input_loop(bus, loop):
    """Async input loop. Runs in executor to avoid blocking the event loop."""

    def _read_input() -> str:
        try:
            return input(f"  {c(YELLOW, 'you')} {c(GREY, '›')} ").strip()
        except (EOFError, KeyboardInterrupt):
            return "/exit"

    while True:
        # Run blocking input() in a thread so the event loop stays alive
        user_input = await asyncio.get_event_loop().run_in_executor(None, _read_input)

        if not user_input:
            continue

        # Special commands
        if user_input.startswith("/"):
            handled = await _handle_special(user_input, bus, loop)
            if handled:
                if user_input.lower() in ("/exit", "/quit"):
                    break
                continue

        # Reset response event
        _state.response_ready.clear()

        # Emit to the real event bus — LLMSkill handles everything from here
        await bus.emit("process_user_input", {"text": user_input})

        # Wait up to 30s for a tts_speak response
        try:
            await asyncio.wait_for(_state.response_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            _state.stop_thinking()
            print_warn("No response received within 30 seconds.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="JARVIS CLI")
    parser.add_argument("--debug", action="store_true", help="Enable event tracing")
    parser.add_argument("--no-tts", action="store_true", help="Disable audio output")
    parser.add_argument(
        "--no-memory", action="store_true", help="Disable memory system"
    )
    args = parser.parse_args()

    _state.debug = args.debug
    _state.tts_enabled = not args.no_tts

    # Suppress logger noise to stdout (it still goes to jarvis.log)
    import logging

    logging.getLogger("Jarvis").setLevel(logging.WARNING)

    # ── Print banner ──────────────────────────────────────────────────────
    os.system("cls" if os.name == "nt" else "clear")
    print(c(CYAN, BANNER))
    print(rule())

    # ── Boot ──────────────────────────────────────────────────────────────
    from core.event_bus import EventBus

    bus = EventBus()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Disable TTS audio if --no-tts
    if not _state.tts_enabled:
        import app_config

        app_config.TTS_ENABLED = False
        os.environ["TTS_CLI_MODE"] = "1"  # picked up by tts.py
        print_info("Audio output disabled (--no-tts)")

    # Disable memory if --no-memory
    if args.no_memory:
        import app_config

        app_config.MEMORY_ENABLED = False
        print_info("Memory system disabled (--no-memory)")

    # Register bus hooks
    loop.run_until_complete(_register_trace_boot(bus))
    _make_bus_hooks(bus, loop)

    # Boot all skills
    print_info("Booting skills...")
    skills_info = _boot_skills(bus)
    _state.registered_skills = skills_info

    ok_count = sum(1 for s in skills_info if s["ok"])
    bad_count = sum(1 for s in skills_info if not s["ok"])

    print_ok(f"{ok_count} skills online")
    if bad_count:
        print_warn(f"{bad_count} skills failed to load (run /skills for details)")

    # Show LLM mode
    llm_mode = os.environ.get("LLM_MODE", "groq")
    model = os.environ.get("LLM_API_MODEL", "llama-3.3-70b-versatile")
    print_ok(f"LLM: {c(CYAN, llm_mode)} › {c(GREY, model)}")

    # Show memory status
    mem_status = c(GREEN, "enabled") if not args.no_memory else c(GREY, "disabled")
    print_ok(f"Memory: {mem_status}")

    print(rule())
    print(c(GREY, "  Type a message or command. Type /help for options.\n"))

    # ── Run ───────────────────────────────────────────────────────────────
    # Emit startup (skills like LLMSkill subscribe to this)
    loop.run_until_complete(bus.emit("startup"))

    # Start input loop
    try:
        loop.run_until_complete(_input_loop(bus, loop))
    except KeyboardInterrupt:
        print(c(CYAN, "\n\n  Goodbye, sir.\n"))
    finally:
        loop.close()


async def _register_trace_boot(bus):
    """Dummy — just ensures the event loop is running for initial setup."""
    pass


if __name__ == "__main__":
    main()
