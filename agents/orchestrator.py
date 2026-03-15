"""
agents/orchestrator.py

The Orchestrator is the entry point for all user input.
It plans, delegates, and synthesises - never executes tools directly.
"""

import asyncio
import json
import logging
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
- SystemAgent: open_app, close_app, set_volume, set_brightness, minimize_window, maximize_window,
  take_screenshot, search_file, tell_joke
- MediaAgent: spotify_play, spotify_pause, spotify_next, spotify_prev, spotify_volume,
  play_youtube, search_youtube, download_video
- CommsAgent: send_email, read_emails, send_whatsapp, get_calendar, create_event, schedule_meeting
- BrowserAgent: open_url, search_web, browser_new_tab, browser_close_tab, browser_next_tab,
  browser_prev_tab, browser_close
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
            return "I'm not sure how to handle that. Could you rephrase?"

        synthesis_task = next((t for t in plan if t.agent == "Orchestrator"), None)
        execution_tasks = [t for t in plan if t.agent != "Orchestrator"]

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
            clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
            task_dicts = json.loads(clean)
            if not isinstance(task_dicts, list):
                raise ValueError("Plan is not a list")
            return [Task(**t) for t in task_dicts]
        except Exception as exc:
            logger.error("Orchestrator: Plan parse failed: %s | raw: %s", exc, raw[:200])
            return []

    async def _synthesise(self, tasks: list[Any], synthesis_task: Any) -> str:
        if not tasks:
            return "I couldn't complete that request."

        if len(tasks) == 1 and not synthesis_task:
            t = tasks[0]
            return t.result.get("speech", "Done.") if t.result else "Done."

        results_text = "\n".join(
            f"- {t.action}: {t.result.get('speech', 'completed') if t.result else 'failed'}"
            for t in tasks
        )
        user_name = "sir"
        try:
            user_name = self.memory.semantic.get_fact("personal", "name") or "sir"
        except Exception:
            user_name = "sir"

        if self.llm is None:
            return results_text.replace("- ", "")

        prompt = SYNTHESIS_PROMPT.format(results=results_text, user_name=user_name)
        return await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are J.A.R.V.I.S. Give a single concise spoken response.",
            max_tokens=200,
            temperature=0.7,
        )
