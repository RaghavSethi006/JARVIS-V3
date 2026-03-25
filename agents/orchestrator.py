"""
agents/orchestrator.py

The Orchestrator is the entry point for all user input.
It plans, delegates, and synthesises - never executes tools directly.
"""

import asyncio
import json
import logging
import re
from typing import Any, Optional

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("orchestrator: core.logger import failed: %s", exc)

try:
    from core.llm_client import LLMClient
except ImportError as exc:
    LLMClient = None
    logger.warning("orchestrator: core.llm_client import failed: %s", exc)

try:
    from core.task_queue import Task, TaskQueue
except ImportError as exc:
    Task = None
    TaskQueue = None
    logger.warning("orchestrator: core.task_queue import failed: %s", exc)

try:
    from core.memory import MemoryManager
except ImportError as exc:
    MemoryManager = None
    logger.warning("orchestrator: core.memory import failed: %s", exc)

try:
    from core.reasoning import ReasoningLayer
except ImportError as exc:
    ReasoningLayer = None
    logger.warning("orchestrator: core.reasoning import failed: %s", exc)


ORCHESTRATOR_PROMPT = """
You are the Orchestrator module of JARVIS.
Your job: decompose user input into a task plan and assign each task to the correct agent.

Available agents and actions:
- InfoAgent: get_weather, get_time, get_news, get_info
- SystemAgent: open_app, close_app, set_volume, volume_up, volume_down, mute,
  set_brightness, brightness_up, brightness_down, minimize_window, maximize_window,
  take_screenshot, search_file, tell_joke
- MediaAgent: spotify_play, spotify_pause, spotify_next, spotify_prev, spotify_volume,
  play_youtube, search_youtube, download_video, play_media
- CommsAgent: send_email, read_emails, send_whatsapp, send_message, get_calendar,
  create_event, schedule_meeting
- BrowserAgent: open_url, open_website, search_web, browser_new_tab, browser_close_tab,
  browser_next_tab, browser_prev_tab, browser_close
- PersonalAgent: set_alarm, set_reminder, auth_login, auth_register, toggle_gesture_control

Return ONLY a valid JSON array of tasks. Each task:
{
  "task_id": "unique_id",
  "action": "exact_action_name",
  "params": {},
  "agent": "AgentName",
  "depends_on": []   // task_ids this must wait for, empty if independent
}

If the input is a simple single action, return a single-item array.
If tasks are independent, set depends_on to [].
Add a final "synthesise" task with agent "Orchestrator" that depends on all others
only if synthesis is needed (i.e., multiple results must be combined into one response).

User input: {user_input}
Memory context: {memory_context}
"""

SYNTHESIS_PROMPT = """
You are J.A.R.V.I.S. Combine the following task results into a single,
natural spoken response. Be concise. Do not repeat yourself.
Address the user as {user_name}.

Results:
{results}
"""

ACTION_AGENT_MAP = {
    "get_weather": "InfoAgent",
    "get_time": "InfoAgent",
    "get_news": "InfoAgent",
    "get_info": "InfoAgent",
    "open_app": "SystemAgent",
    "close_app": "SystemAgent",
    "set_volume": "SystemAgent",
    "volume_up": "SystemAgent",
    "volume_down": "SystemAgent",
    "mute": "SystemAgent",
    "set_brightness": "SystemAgent",
    "brightness_up": "SystemAgent",
    "brightness_down": "SystemAgent",
    "minimize_window": "SystemAgent",
    "maximize_window": "SystemAgent",
    "take_screenshot": "SystemAgent",
    "search_file": "SystemAgent",
    "tell_joke": "SystemAgent",
    "spotify_play": "MediaAgent",
    "spotify_pause": "MediaAgent",
    "spotify_next": "MediaAgent",
    "spotify_prev": "MediaAgent",
    "spotify_volume": "MediaAgent",
    "play_youtube": "MediaAgent",
    "search_youtube": "MediaAgent",
    "download_video": "MediaAgent",
    "play_media": "MediaAgent",
    "send_email": "CommsAgent",
    "read_emails": "CommsAgent",
    "send_whatsapp": "CommsAgent",
    "send_message": "CommsAgent",
    "get_calendar": "CommsAgent",
    "create_event": "CommsAgent",
    "schedule_meeting": "CommsAgent",
    "open_url": "BrowserAgent",
    "open_website": "BrowserAgent",
    "search_web": "BrowserAgent",
    "browser_new_tab": "BrowserAgent",
    "browser_close_tab": "BrowserAgent",
    "browser_next_tab": "BrowserAgent",
    "browser_prev_tab": "BrowserAgent",
    "browser_close": "BrowserAgent",
    "set_alarm": "PersonalAgent",
    "set_reminder": "PersonalAgent",
    "auth_login": "PersonalAgent",
    "auth_register": "PersonalAgent",
    "toggle_gesture_control": "PersonalAgent",
}


class _NullSemantic:
    """Fallback semantic store."""

    def get_fact(self, category: str, key: str) -> Optional[str]:
        return None


class _NullMemory:
    """Fallback memory manager when the real memory system is unavailable."""

    semantic = _NullSemantic()

    def build_context(self, user_input: str) -> str:
        return ""

    async def post_exchange_pipeline(self, user_input: str, response: str) -> None:
        return None

    def add_exchange(self, role: str, content: str) -> None:
        return None

    def get_messages(self) -> list[dict]:
        return []


class _NullReasoning:
    """Fallback reasoning layer when reasoning is unavailable."""

    async def reason(self, user_input: str, memory_context: str) -> str:
        return ""


class OrchestratorAgent:
    """Plans, delegates, and synthesises multi-step tasks across agents."""

    def __init__(self, bus: Any, agent_registry: dict):
        self.bus = bus
        self.llm = LLMClient.get() if LLMClient is not None else None
        if TaskQueue is None:
            raise RuntimeError("TaskQueue unavailable; cannot initialize OrchestratorAgent.")
        self.agent_registry = agent_registry
        self.task_queue = TaskQueue(agent_registry)
        self.memory = MemoryManager.get() if MemoryManager is not None else _NullMemory()
        self.reasoning = ReasoningLayer() if ReasoningLayer is not None else _NullReasoning()

    async def process(self, user_input: str) -> str:
        """Main entry point. Returns the final spoken response."""
        memory_ctx = ""
        try:
            memory_ctx = self.memory.build_context(user_input)
        except Exception as exc:
            logger.warning("Orchestrator: memory context build failed: %s", exc)

        scratchpad = ""
        try:
            scratchpad = await self.reasoning.reason(user_input, memory_ctx)
        except Exception as exc:
            logger.warning("Orchestrator: reasoning failed: %s", exc)

        plan = await self._plan(user_input, memory_ctx, scratchpad)
        if not plan:
            return "I'm not sure how to handle that yet. Please try rephrasing the request."

        synthesis_task = next((t for t in plan if t.agent == "Orchestrator"), None)
        execution_tasks = [t for t in plan if t.agent != "Orchestrator"]
        if not execution_tasks:
            return "I couldn't build an actionable plan for that request."

        completed = await self.task_queue.execute_plan(execution_tasks)
        response = await self._synthesise(completed, synthesis_task)

        asyncio.create_task(self._post_exchange(user_input, response))
        return response

    async def _post_exchange(self, user_input: str, response: str) -> None:
        """Update memory asynchronously without blocking the response."""
        try:
            await self.memory.post_exchange_pipeline(user_input, response)
            self.memory.add_exchange("user", user_input)
            self.memory.add_exchange("jarvis", response)
        except Exception as exc:
            logger.warning("Orchestrator: post-exchange memory update failed: %s", exc)

    async def _plan(self, user_input: str, memory_ctx: str, scratchpad: str) -> list[Any]:
        if Task is None:
            return []
        if self.llm is None:
            logger.warning("Orchestrator: LLM unavailable; falling back to single InfoAgent task.")
            return [Task(task_id="t1", action="get_info", params={"query": user_input}, agent="InfoAgent")]

        prompt = ORCHESTRATOR_PROMPT.format(
            user_input=user_input,
            memory_context=memory_ctx + ("\n\n[Reasoning]: " + scratchpad if scratchpad else ""),
        )

        raw = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are a task planning system. Return only valid JSON.",
            max_tokens=600,
            temperature=0.1,
        )

        try:
            task_dicts = self._parse_task_array(raw)
            normalized = self._normalize_plan(task_dicts)
            if normalized:
                return normalized
            logger.warning("Orchestrator: Planner returned no actionable tasks. Falling back.")
        except Exception as exc:
            logger.error("Orchestrator: Plan parse failed: %s | raw: %s", exc, raw[:200])
        return self._fallback_plan(user_input)

    def _parse_task_array(self, raw: str) -> list[dict]:
        """Parse the planner response, tolerating markdown fences and extra prose."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

        start = clean.find("[")
        end = clean.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("Planner response did not contain a JSON array.")
        parsed = json.loads(clean[start : end + 1])
        if not isinstance(parsed, list):
            raise ValueError("Planner response was not a task array.")
        return parsed

    def _normalize_plan(self, task_dicts: list[dict]) -> list[Any]:
        """Normalize planner output into validated Task objects."""
        if Task is None:
            return []

        normalized: list[Any] = []
        for index, item in enumerate(task_dicts, start=1):
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "")).strip()
            if not action:
                continue
            params = item.get("params", {})
            if not isinstance(params, dict):
                params = {}
            agent_name = str(item.get("agent") or ACTION_AGENT_MAP.get(action, "InfoAgent")).strip()
            if agent_name != "Orchestrator":
                agent_name = ACTION_AGENT_MAP.get(action, agent_name)
            if agent_name != "Orchestrator" and agent_name not in self.agent_registry:
                logger.warning("Orchestrator: Unknown agent %s for action %s. Re-routing.", agent_name, action)
                agent_name = ACTION_AGENT_MAP.get(action, "InfoAgent")
            depends_on = item.get("depends_on", [])
            if not isinstance(depends_on, list):
                depends_on = []
            task_id = str(item.get("task_id") or f"t{index}")
            normalized.append(
                Task(
                    task_id=task_id,
                    action=action,
                    params=params,
                    agent=agent_name,
                    depends_on=[str(dep) for dep in depends_on if dep],
                )
            )
        return normalized

    def _fallback_plan(self, user_input: str) -> list[Any]:
        """Conservative fallback for common single-command requests."""
        if Task is None:
            return []

        text = user_input.strip()
        lower = text.lower()
        if "weather" in lower:
            location = ""
            if " in " in lower:
                location = text.rsplit(" in ", 1)[-1].strip(" ?.")
            return [Task(task_id="t1", action="get_weather", params={"location": location}, agent="InfoAgent")]
        if "time" in lower and ("what" in lower or "tell" in lower):
            return [Task(task_id="t1", action="get_time", params={}, agent="InfoAgent")]
        if lower.startswith("open "):
            app = re.split(r"\band\b|,", text[5:], maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if app:
                return [Task(task_id="t1", action="open_app", params={"app": app}, agent="SystemAgent")]
        if lower.startswith("close "):
            app = re.split(r"\band\b|,", text[6:], maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if app:
                return [Task(task_id="t1", action="close_app", params={"app": app}, agent="SystemAgent")]
        if lower.startswith("play "):
            query = re.sub(r"\bon spotify\b|\bon youtube\b", "", text[5:], flags=re.IGNORECASE).strip()
            platform = "youtube" if "youtube" in lower else "spotify"
            return [Task(task_id="t1", action="play_media", params={"query": query, "platform": platform}, agent="MediaAgent")]
        if "remind me" in lower:
            return [Task(task_id="t1", action="set_reminder", params={"task": text, "time": ""}, agent="PersonalAgent")]
        return [Task(task_id="t1", action="get_info", params={"query": text}, agent="InfoAgent")]

    async def _synthesise(self, tasks: list[Any], synthesis_task: Any) -> str:
        if not tasks:
            return "I couldn't complete that request."

        if len(tasks) == 1 and not synthesis_task:
            t = tasks[0]
            return t.result.get("speech", "Done.") if t.result else "Done."

        results_text = "\n".join(
            f"- {t.action} [{t.result.get('status', 'error') if t.result else 'error'}]: "
            f"{t.result.get('speech', 'completed') if t.result else 'failed'}"
            for t in tasks
        )
        user_name = "sir"
        try:
            user_name = self.memory.semantic.get_fact("personal", "name") or "sir"
        except Exception:
            user_name = "sir"

        if self.llm is None:
            return " ".join(
                t.result.get("speech", "Done.")
                for t in tasks
                if t.result and t.result.get("speech")
            )

        prompt = SYNTHESIS_PROMPT.format(results=results_text, user_name=user_name)
        return await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are J.A.R.V.I.S. Give a single concise spoken response.",
            max_tokens=200,
            temperature=0.7,
        )
