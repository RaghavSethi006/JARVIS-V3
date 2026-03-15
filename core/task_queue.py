"""
core/task_queue.py

Parallel task execution with dependency resolution.
The Orchestrator creates a TaskPlan; the TaskQueue executes it.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("task_queue: core.logger import failed: %s", exc)


@dataclass
class Task:
    """A single unit of work within a task plan."""

    task_id: str
    action: str
    params: dict
    agent: str
    depends_on: list[str] = field(default_factory=list)
    result: Optional[dict] = None
    status: str = "pending"  # pending | running | done | failed


class TaskQueue:
    """Executes task plans with simple dependency resolution and parallelism."""

    def __init__(self, agent_registry: dict):
        """
        Initialize with a registry of agent instances.

        agent_registry: {"InfoAgent": InfoAgent instance, ...}
        """
        self.agents = agent_registry

    async def execute_plan(self, tasks: list[Task], timeout: float = 15.0) -> list[Task]:
        """
        Execute a task plan respecting dependencies.
        Independent tasks run in parallel.
        Returns the completed task list.
        """
        completed: set[str] = set()
        failed: set[str] = set()

        while len(completed) + len(failed) < len(tasks):
            ready = [
                t
                for t in tasks
                if t.status == "pending"
                and all(dep in completed for dep in t.depends_on)
                and not any(dep in failed for dep in t.depends_on)
            ]

            if not ready:
                # Deadlock or all remaining depend on failed tasks
                logger.warning("TaskQueue: No runnable tasks remain; stopping execution.")
                break

            results = await asyncio.gather(
                *[self._run_task(t, timeout) for t in ready],
                return_exceptions=True,
            )

            for task, result in zip(ready, results):
                if isinstance(result, Exception):
                    task.status = "failed"
                    task.result = {"status": "error", "speech": str(result)}
                    failed.add(task.task_id)
                    logger.error("TaskQueue: Task %s failed: %s", task.task_id, result)
                else:
                    task.status = "done"
                    task.result = result
                    completed.add(task.task_id)

        # Mark unresolved tasks that depended on failures
        for task in tasks:
            if task.status == "pending" and any(dep in failed for dep in task.depends_on):
                task.status = "failed"
                task.result = {
                    "status": "error",
                    "speech": "Skipped because a dependency failed.",
                }

        return tasks

    async def _run_task(self, task: Task, timeout: float) -> dict:
        task.status = "running"
        agent = self.agents.get(task.agent)
        if not agent:
            return {"status": "error", "speech": f"Agent {task.agent} not found."}

        try:
            return await asyncio.wait_for(
                agent.handle({"action": task.action, "params": task.params, "task_id": task.task_id}),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {"status": "error", "speech": f"Task {task.task_id} timed out."}
        except Exception as exc:
            logger.error("TaskQueue: Task %s raised error: %s", task.task_id, exc)
            return {"status": "error", "speech": str(exc)}
