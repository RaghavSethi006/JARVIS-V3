from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
from app_config import (
    CONVERSATION_MAXLEN,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    TTS_ENABLED,
    AGENTS_ENABLED,
)
from core.database import append_history, load_history
import os
import asyncio
import datetime
import json
import subprocess
import threading
from collections import deque

try:
    from core.llm_client import LLMClient
except ImportError:
    LLMClient = None
    logger.warning("LLMSkill: core.llm_client not available. LLM features disabled.")

try:
    from app_config import MEMORY_ENABLED
    if MEMORY_ENABLED:
        from core.memory import MemoryManager
    else:
        MemoryManager = None
except ImportError:
    MemoryManager = None
    MEMORY_ENABLED = False
    logger.warning("LLMSkill: Memory system not available.")

try:
    from core.reasoning import ReasoningLayer
except ImportError:
    ReasoningLayer = None
    logger.warning("LLMSkill: ReasoningLayer not available. Reasoning disabled.")

try:
    from core.capability_manifest import CAPABILITY_MANIFEST
except ImportError:
    CAPABILITY_MANIFEST = ""
    logger.warning("LLMSkill: Capability manifest not available.")

SYSTEM_PROMPT = """
You are J.A.R.V.I.S. - Just A Rather Very Intelligent System - the personal AI
assistant of {user_name}. You were built to be indispensable.

PERSONALITY:
- Tone: Dry wit, measured intelligence, British understatement. Never sycophantic.
- Address the user by name when you know it, or as "sir" / "ma'am" by default.
- Confident but never arrogant. Acknowledge uncertainty precisely.
- When you don't know something, say so clearly rather than hallucinating.
- Responses are calibrated to context: concise when the user is rushed,
  expansive in exploratory conversation.

BEHAVIOUR:
- Take ownership of tasks. Narrate multi-step progress: "Opening Chrome...
  searching now... found 3 results."
- Reference prior context naturally when relevant. Don't repeat yourself.
- If a request is ambiguous, make a reasonable assumption and state it,
  rather than asking 3 clarifying questions.
- When you complete a multi-part request, synthesise all results into one
  coherent spoken response.
- Flag when something looks wrong or contradicts what you know.

CONSTRAINTS:
- Never fabricate capabilities you don't have.
- Never invent facts about real people.
- Keep spoken responses under 3 sentences unless detail is explicitly requested.
  (Detailed written output can be longer.)

{memory_context}

{capability_manifest}
"""
INTENT_SYSTEM_PROMPT = """\
You are a command parser for a desktop AI assistant. Given a user \
utterance, extract ALL intended commands and return ONLY a valid JSON array.
Each object must have an "action" field (string) and a "params" field (dict).

Available actions and their expected params:
- open_app:      { "app": "<app name>" }
- play_media:    { "query": "<search query>", "platform": "youtube|spotify|local" }
- search_web:    { "query": "<search query>" }
- get_time:      {}
- get_weather:   { "location": "<city or empty string>" }
- set_reminder:  { "task": "<task>", "time": "<time string or empty>" }
- get_info:      { "query": "<question or topic>" }
- system_control:{ "command": "shutdown|restart|sleep|volume_up|volume_down|mute" }
- send_message:  { "contact": "<name>", "message": "<text>" }
- close_app:     { "app": "<app name>" }
- volume_up:        { "value": 10 }
- volume_down:      { "value": 10 }
- mute:             {}
- brightness_up:    { "value": 20 }
- brightness_down:  { "value": 20 }
- take_screenshot:  {}
- read_emails:      { "count": 5 }
- get_calendar:     { "count": 5 }
- create_event:     { "title": "", "start": "<ISO datetime>", "end": "<ISO datetime>" }
- schedule_meeting: { "title": "", "start": "<ISO datetime>", "end": "<ISO datetime>" }
- search_file:      { "query": "<filename>" }
- minimize_window:  {}
- maximize_window:  {}
- tell_joke:        {}
- switch_voice:     { "name": "male|female|jarvis|friday" }
- spotify_play:     { "query": "<song or artist>" }
- spotify_pause:    {}
- spotify_next:     {}
- synthesis_confirm: {}
- synthesis_reject:  {}

Rules:
- Return ONLY the JSON array. No explanation, no markdown, no extra text.
- If the input is a general question or chat, use action "get_info".
- Preserve the order of commands as the user stated them.
- If a param is not specified, use an empty string for string params.

Examples:
Input:  "open chrome and play lofi on youtube"
Output: [{"action":"open_app","params":{"app":"chrome"}},
         {"action":"play_media","params":{"query":"lofi","platform":"youtube"}}]

Input:  "what time is it"
Output: [{"action":"get_time","params":{}}]

Input:  "who is elon musk"
Output: [{"action":"get_info","params":{"query":"who is elon musk"}}]
"""

MAX_HISTORY_TOKENS = 2000


class LLMSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self.llm_client = LLMClient.get() if LLMClient is not None else None
        self.memory = MemoryManager.get() if MemoryManager is not None else None
        self.reasoning = ReasoningLayer() if ReasoningLayer is not None else None
        self.conversation_history = deque(maxlen=max(CONVERSATION_MAXLEN, 20))
        self._load_conversation_history()
        self._last_dispatch_summary = ""
        self.command_handlers = {
            "open_app": self.handle_open_app,
            "play_media": self.handle_play_media,
            "search_web": self.handle_search_web,
            "get_time": self.handle_get_time,
            "get_weather": self.handle_get_weather,
            "set_reminder": self.handle_set_reminder,
            "get_info": self.handle_get_info,
            "system_control": self.handle_system_control,
            "send_message": self.handle_send_message,
            "close_app": self.handle_close_app,
            "volume_up": self.handle_volume_up,
            "volume_down": self.handle_volume_down,
            "mute": self.handle_mute,
            "brightness_up": self.handle_brightness_up,
            "brightness_down": self.handle_brightness_down,
            "take_screenshot": self.handle_screenshot,
            "read_emails": self.handle_read_emails,
            "get_calendar": self.handle_get_calendar,
            "create_event": self.handle_create_event,
            "schedule_meeting": self.handle_schedule_meeting,
            "search_file": self.handle_search_file,
            "minimize_window": self.handle_minimize,
            "maximize_window": self.handle_maximize,
            "tell_joke": self.handle_joke,
            "switch_voice": self.handle_switch_voice,
            "spotify_play": self.handle_spotify_play,
            "spotify_pause": self.handle_spotify_pause,
            "spotify_next": self.handle_spotify_next,
            "synthesis_confirm": self.handle_synthesis_confirm,
            "synthesis_reject": self.handle_synthesis_reject,
        }

    def register(self):
        self.bus.subscribe("ask_gpt", self.handle_ask)
        if not AGENTS_ENABLED:
            self.bus.subscribe("process_user_input", self.handle_user_input)
        else:
            logger.info("LLMSkill: AGENTS_ENABLED is True; skipping process_user_input.")
        self.bus.subscribe("startup", self.handle_startup)

    async def handle_startup(self, event: Event):
        """Phase 0: LLMClient initialises on import — nothing to warm up for API mode."""
        if self.llm_client is None:
            logger.warning("LLMSkill: No LLM client available at startup.")

    async def handle_ask(self, event: Event):
        prompt = (event.data or {}).get("prompt")
        if not prompt:
            return

        await self._maybe_summarize_history()
        await self.bus.emit("set_core_state", "thinking")
        try:
            # Use same memory-enriched path as handle_get_info
            response = await self.handle_get_info(query=prompt)
            self.conversation_history.append({"role": "user", "content": prompt})
            self.conversation_history.append({"role": "assistant", "content": response})
            append_history("user", prompt)
            append_history("assistant", response)
        except Exception as e:
            logger.exception("LLM Error: %s", e)
            if TTS_ENABLED:
                await self.bus.emit(
                    "tts_speak",
                    "My cognitive systems encountered an error.",
                )

    async def handle_user_input(self, event: Event):
        user_input = (event.data or {}).get("text", "").strip()
        if not user_input:
            return

        await self._maybe_summarize_history()
        await self.bus.emit("set_core_state", "thinking")
        commands = await self.parse_intents(user_input)
        await self.dispatch_commands(commands, user_input=user_input)

    # ── Intent parsing via LLMClient ──────────────────────────────────────

    async def parse_intents(self, user_input: str) -> list[dict]:
        fallback = [{"action": "get_info", "params": {"query": user_input}}]

        if self.llm_client is None:
            logger.warning("Intent parsing fallback: LLM client unavailable.")
            return fallback

        context_text = self._format_recent_context()
        user_prompt = (
            f"{context_text}\nCurrent input:\n{user_input}" if context_text else f"Current input:\n{user_input}"
        )

        try:
            raw = await self.llm_client.complete(
                messages=[{"role": "user", "content": user_prompt}],
                system=INTENT_SYSTEM_PROMPT,
                max_tokens=512,
                temperature=0.1,
            )
            return self._parse_intent_json(raw, user_input)
        except Exception as exc:
            logger.warning("Intent parsing failed. Falling back to get_info. Error: %s", exc)
            return fallback

    async def dispatch_commands(self, commands: list[dict], user_input: str = ""):
        if not isinstance(commands, list):
            commands = []

        summaries = []
        for cmd in commands:
            action = (cmd or {}).get("action", "")
            params = (cmd or {}).get("params", {})
            if not isinstance(params, dict):
                params = {}

            handler = self.command_handlers.get(action, self.handle_get_info)
            try:
                result = await handler(**params)
                if result:
                    summaries.append(str(result))
            except Exception as exc:
                logger.exception("Command '%s' failed with params %s: %s", action, params, exc)
                continue

        assistant_summary = "; ".join(summaries).strip() or "Processed request."
        self._last_dispatch_summary = assistant_summary
        if user_input:
            self.conversation_history.append({"role": "user", "content": user_input})
            self.conversation_history.append({"role": "assistant", "content": assistant_summary})
            append_history("user", user_input)
            append_history("assistant", assistant_summary)
            # Record in memory system
            if self.memory:
                await self.memory.post_exchange_pipeline(user_input, assistant_summary)

    # ── JSON extraction (unchanged from v2) ───────────────────────────────

    def _parse_intent_json(self, raw: str, user_input: str) -> list[dict]:
        fallback = [{"action": "get_info", "params": {"query": user_input}}]
        def _normalize(parsed):
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for key in ("actions", "intents", "commands"):
                    nested = parsed.get(key)
                    if isinstance(nested, list):
                        return nested
                if "action" in parsed:
                    return [parsed]
            return None

        try:
            parsed = json.loads(raw)
            normalized = _normalize(parsed)
            if normalized is not None:
                return normalized
            logger.warning("Intent parser returned non-list JSON. Falling back. Raw: %s", raw)
            return fallback
        except Exception:
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(raw[start : end + 1])
                    normalized = _normalize(parsed)
                    if normalized is not None:
                        return normalized
                except Exception:
                    pass
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(raw[start : end + 1])
                    normalized = _normalize(parsed)
                    if normalized is not None:
                        return normalized
                except Exception:
                    pass
            logger.warning("Intent JSON parse failed; falling back. Raw: %s", raw)
            return fallback

    # ── Response generation via LLMClient ─────────────────────────────────

    async def _generate_response(self, prompt: str) -> str:
        if self.llm_client is None:
            return "My language model is currently unavailable."

        history = list(self.conversation_history)
        messages = history + [{"role": "user", "content": prompt}]
        memory_context = self.memory.build_context(prompt) if self.memory else ""
        full_system = self._build_system_prompt(memory_context)
        return await self.llm_client.complete(
            messages=messages,
            system=full_system,
        )

    def _get_user_name(self) -> str:
        if self.memory:
            for key in ("name", "user_name", "preferred_name"):
                value = self.memory.semantic.get_fact("personal", key)
                if value:
                    return value
        return "sir"

    def _build_system_prompt(self, memory_context: str = "") -> str:
        context = memory_context.strip() if memory_context else "No relevant memory context."
        return SYSTEM_PROMPT.format(
            user_name=self._get_user_name(),
            memory_context=context,
            capability_manifest=CAPABILITY_MANIFEST or "",
        )

    # ── Conversation history management ───────────────────────────────────

    def _format_recent_context(self) -> str:
        recent_entries = list(self.conversation_history)[-6:]
        if not recent_entries:
            return ""
        lines = ["Conversation context:"]
        for item in recent_entries:
            if "role" in item and "content" in item:
                role = str(item.get("role", "user")).capitalize()
                lines.append(f"{role}: {item.get('content', '')}")
            else:
                lines.append(f'User: {item.get("user", "")}')
                lines.append(f'Assistant: {item.get("assistant", "")}')
        return "\n".join(lines)

    def _load_conversation_history(self):
        try:
            for row in load_history():
                role = row.get("role", "")
                content = row.get("content", "")
                if role and content:
                    self.conversation_history.append({"role": role, "content": content})
        except Exception as exc:
            logger.warning("Failed to load DB conversation history: %s", exc)

    def _estimate_tokens(self) -> int:
        total = 0
        for item in self.conversation_history:
            if "content" in item:
                total += len(item.get("content", ""))
            else:
                total += len(item.get("user", "")) + len(item.get("assistant", ""))
        return total // 4

    async def _maybe_summarize_history(self):
        if self._estimate_tokens() < MAX_HISTORY_TOKENS:
            return
        if self.llm_client is None:
            return

        normalized = []
        for item in self.conversation_history:
            if "role" in item and "content" in item:
                normalized.append({"role": item["role"], "content": item["content"]})
            else:
                user_text = item.get("user", "")
                assistant_text = item.get("assistant", "")
                if user_text:
                    normalized.append({"role": "user", "content": user_text})
                if assistant_text:
                    normalized.append({"role": "assistant", "content": assistant_text})

        to_summarize = normalized[:-2]
        if not to_summarize:
            return

        summary_prompt = (
            "Summarize the following conversation in 2-3 sentences, preserving key facts:\n\n"
            + "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
        )
        try:
            summary = await self.llm_client.complete(
                messages=[{"role": "user", "content": summary_prompt}],
                system="You are a concise summarizer. Output only the summary.",
                max_tokens=300,
                temperature=0.3,
            )
            last_two = normalized[-2:]
            self.conversation_history.clear()
            self.conversation_history.append(
                {"role": "system", "content": f"[Conversation summary]: {summary}"}
            )
            self.conversation_history.extend(last_two)
        except Exception as exc:
            logger.error("History summarization failed: %s", exc)

    # ══════════════════════════════════════════════════════════════════════
    # Command handlers — ALL UNCHANGED from v2
    # ══════════════════════════════════════════════════════════════════════

    async def handle_open_app(self, **kwargs):
        app = (kwargs.get("app") or "").strip()
        if not app:
            return None
        await self.bus.emit("system_launch", {"app": app})
        return f"Opened {app}"

    async def handle_play_media(self, **kwargs):
        query = (kwargs.get("query") or "").strip()
        platform_name = (kwargs.get("platform") or "").strip().lower()
        if not query and not platform_name:
            return None
        if platform_name == "spotify":
            await self.bus.emit("spotify_play", {"query": query})
            return f"Playing {query or 'Spotify'} on Spotify"
        if platform_name == "youtube":
            await self.bus.emit("play_youtube", {"query": query})
            return f"Playing {query}"

        app = platform_name or query
        if not app:
            return None
        await self.bus.emit("quick_launch", {"app": app})
        return f"Opened {app}"

    async def handle_search_web(self, **kwargs):
        query = (kwargs.get("query") or "").strip()
        if not query:
            return None
        await self.bus.emit("search_google", {"query": query})
        return f"Searched for {query}"

    async def handle_get_time(self, **kwargs):
        now = datetime.datetime.now().strftime("%I:%M %p")
        if TTS_ENABLED:
            await self.bus.emit("tts_speak", f"The time is {now}.")
        return f"Time is {now}"

    async def handle_get_weather(self, **kwargs):
        location = (kwargs.get("location") or "").strip()
        await self.bus.emit("check_weather", {"city": location})
        return f"Fetching weather for {location or 'default city'}"

    async def handle_set_reminder(self, **kwargs):
        task = (kwargs.get("task") or "").strip()
        time_text = (kwargs.get("time") or "").strip()
        await self.bus.emit("set_reminder", {"task": task, "time_text": time_text})
        return "Setting reminder"

    async def handle_get_info(self, **kwargs):
        query = (kwargs.get("query") or "").strip()
        if not query:
            return None

        # Use memory-enriched context if available
        if self.memory and self.llm_client:
            memory_context = self.memory.build_context(query)
            full_system = self._build_system_prompt(memory_context)
            if self.reasoning:
                scratchpad = await self.reasoning.reason(query, memory_context)
                if scratchpad:
                    full_system = full_system + "\n\n[INTERNAL REASONING]\n" + scratchpad

            messages = self.memory.get_messages()
            messages.append({"role": "user", "content": query})

            response = await self.llm_client.complete(
                messages=messages,
                system=full_system,
            )

            # Record in memory
            self.memory.add_exchange("user", query, intent="get_info")
            self.memory.add_exchange("jarvis", response)
        else:
            response = await self._generate_response(query)

        await self.bus.emit("add_jarvis_response", response)
        if TTS_ENABLED:
            await self.bus.emit("tts_speak", response)
        return response

    async def handle_system_control(self, **kwargs):
        command = (kwargs.get("command") or "").strip().lower()
        if not command:
            return None
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._system_control_sync, command)
        return f"Executed {command}"

    async def handle_send_message(self, **kwargs):
        contact = (kwargs.get("contact") or "").strip()
        message = (kwargs.get("message") or "").strip()
        await self.bus.emit("send_whatsapp", {"contact": contact, "message": message})
        return f"Sending message to {contact}"

    async def handle_close_app(self, **kwargs):
        app = (kwargs.get("app") or "").strip()
        if not app:
            return None
        await self.bus.emit("system_close", {"app": app})
        return f"Closing {app}"

    async def handle_volume_up(self, **kwargs):
        value = int(kwargs.get("value", 10))
        await self.bus.emit("set_volume", {"action": "up", "value": value})
        return "Increasing volume"

    async def handle_volume_down(self, **kwargs):
        value = int(kwargs.get("value", 10))
        await self.bus.emit("set_volume", {"action": "down", "value": value})
        return "Decreasing volume"

    async def handle_mute(self, **kwargs):
        await self.bus.emit("set_volume", {"action": "mute"})
        return "Muting volume"

    async def handle_brightness_up(self, **kwargs):
        value = int(kwargs.get("value", 20))
        await self.bus.emit("set_brightness", {"action": "up", "value": value})
        return "Increasing brightness"

    async def handle_brightness_down(self, **kwargs):
        value = int(kwargs.get("value", 20))
        await self.bus.emit("set_brightness", {"action": "down", "value": value})
        return "Decreasing brightness"

    async def handle_screenshot(self, **kwargs):
        await self.bus.emit("take_screenshot", {})
        return "Taking screenshot"

    async def handle_read_emails(self, **kwargs):
        count = int(kwargs.get("count", 5))
        await self.bus.emit("read_emails", {"count": count})
        return "Reading emails"

    async def handle_get_calendar(self, **kwargs):
        count = int(kwargs.get("count", 5))
        await self.bus.emit("get_calendar", {"count": count})
        return "Checking calendar"

    async def handle_create_event(self, **kwargs):
        await self.bus.emit(
            "create_event",
            {
                "title": kwargs.get("title", ""),
                "start": kwargs.get("start", ""),
                "end": kwargs.get("end", ""),
            },
        )
        return "Creating calendar event"

    async def handle_schedule_meeting(self, **kwargs):
        await self.bus.emit(
            "schedule_meeting",
            {
                "title": kwargs.get("title", ""),
                "start": kwargs.get("start", ""),
                "end": kwargs.get("end", ""),
            },
        )
        return "Scheduling meeting"

    async def handle_search_file(self, **kwargs):
        await self.bus.emit("search_file", {"query": kwargs.get("query", "")})
        return "Searching files"

    async def handle_minimize(self, **kwargs):
        await self.bus.emit("minimize_window", {})
        return "Minimizing window"

    async def handle_maximize(self, **kwargs):
        await self.bus.emit("maximize_window", {})
        return "Maximizing window"

    async def handle_joke(self, **kwargs):
        await self.bus.emit("tell_joke", {})
        return "Telling a joke"

    async def handle_switch_voice(self, **kwargs):
        await self.bus.emit("switch_voice", kwargs)
        return "Switching voice"

    async def handle_spotify_play(self, **kwargs):
        await self.bus.emit("spotify_play", kwargs)
        return "Playing on Spotify"

    async def handle_spotify_pause(self, **kwargs):
        await self.bus.emit("spotify_pause", {})
        return "Pausing Spotify"

    async def handle_spotify_next(self, **kwargs):
        await self.bus.emit("spotify_next", {})
        return "Skipping track"

    async def handle_synthesis_confirm(self, **kwargs):
        await self.bus.emit("synthesis_confirm", {})
        return "Activating pending generated skill"

    async def handle_synthesis_reject(self, **kwargs):
        await self.bus.emit("synthesis_reject", {})
        return "Discarding pending generated skill"

    def _system_control_sync(self, command: str):
        try:
            if command == "shutdown":
                if os.name == "nt":
                    subprocess.Popen(["shutdown", "/s", "/t", "1"])
                else:
                    subprocess.Popen(["shutdown", "-h", "now"])
            elif command == "restart":
                if os.name == "nt":
                    subprocess.Popen(["shutdown", "/r", "/t", "1"])
                else:
                    subprocess.Popen(["shutdown", "-r", "now"])
            elif command == "sleep":
                if os.name == "nt":
                    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            elif command in {"volume_up", "volume_down", "mute"}:
                import pyautogui

                if command == "volume_up":
                    pyautogui.press("volumeup")
                elif command == "volume_down":
                    pyautogui.press("volumedown")
                else:
                    pyautogui.press("volumemute")
            else:
                logger.warning("Unsupported system control command: %s", command)
        except Exception as exc:
            logger.exception("System control command failed: %s", exc)
