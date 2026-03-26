"""Focused Phase 5 validation suite for Jarvis v3."""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@dataclass
class TestResult:
    """Simple result record for a named test."""

    name: str
    passed: bool
    detail: str = ""


RESULTS: list[TestResult] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    """Store and print a test result."""
    RESULTS.append(TestResult(name=name, passed=passed, detail=detail))
    prefix = "[PASS]" if passed else "[FAIL]"
    suffix = f" - {detail}" if detail else ""
    print(f"{prefix} {name}{suffix}")


def run_test(name: str, fn: Callable[[], None]) -> None:
    """Run a single test and convert uncaught exceptions into failures."""
    try:
        fn()
    except Exception as exc:
        record(name, False, str(exc))


@contextmanager
def temporary_database():
    """Temporarily redirect the database layer to an isolated SQLite file."""
    import core.database as database

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "jarvis_test.db")
        original_path = database.DB_PATH
        database.DB_PATH = db_path
        try:
            yield db_path
        finally:
            database.DB_PATH = original_path


def _table_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def test_schema_bootstrap() -> None:
    """Phase 0-4.5 schema smoke test."""
    import core.database as database

    with temporary_database() as db_path:
        database.init_db()
        names = _table_names(db_path)
        expected = {
            "conversation_history",
            "episodes",
            "user_facts",
            "working_memory_snapshots",
            "entities",
            "entity_facts",
            "entity_relationships",
            "entity_threads",
            "synthesised_skills",
        }
        missing = sorted(expected - names)
        if missing:
            raise AssertionError(f"Missing tables: {', '.join(missing)}")


def test_working_memory_roundtrip() -> None:
    """Phase 1 working-memory serialization round-trip."""
    from core.memory.working import WorkingMemory

    working = WorkingMemory(session_id="ep_test", session_start="2026-01-01T00:00:00")
    working.current_task = "check weather"
    working.active_entities = ["Sarah", "Jarvis"]
    working.add_exchange("user", "I prefer jazz music", intent="get_info")
    working.add_exchange("jarvis", "Noted. You prefer jazz.")

    restored = WorkingMemory.deserialize(working.serialize())
    if restored.current_task != "check weather":
        raise AssertionError("Current task did not survive round-trip.")
    if len(restored.exchanges) != 2:
        raise AssertionError("Exchange count mismatch after deserialize.")


def test_entity_store_contradictions() -> None:
    """Phase 2 entity storage and contradiction handling."""
    import core.database as database
    from core.memory.entity_store import EntityStore

    with temporary_database():
        database.init_db()
        store = EntityStore()
        entity_id = store.create_entity("Sarah", "person")
        store.add_fact(entity_id, "Sarah lives in Vancouver")
        store.add_fact(entity_id, "Sarah moved to Toronto")
        entity = store.get_entity(entity_id)
        active_facts = [fact["fact"] for fact in entity["facts"] if not fact["is_superseded"]]
        superseded = [fact["fact"] for fact in entity["facts"] if fact["is_superseded"]]

        if "Sarah moved to Toronto" not in active_facts:
            raise AssertionError("Latest entity fact was not retained as active.")
        if "Sarah lives in Vancouver" not in superseded:
            raise AssertionError("Contradicted fact was not superseded.")


def test_reasoning_trigger() -> None:
    """Phase 3 reasoning heuristic should trigger on compound requests."""
    from core.reasoning import ReasoningLayer

    layer = ReasoningLayer()
    if not layer.needs_reasoning("Plan my morning and remind me to stretch after my meeting"):
        raise AssertionError("Reasoning heuristic missed a clearly compound request.")


def test_task_queue_parallelism() -> None:
    """Phase 4 task queue should execute independent tasks in parallel."""
    from core.task_queue import Task, TaskQueue

    class DummyAgent:
        def __init__(self) -> None:
            self.bus = None

        async def handle(self, task: dict) -> dict:
            await asyncio.sleep(0.15)
            return {"status": "ok", "result": task["task_id"], "speech": task["task_id"]}

    async def _run() -> float:
        queue = TaskQueue({"InfoAgent": DummyAgent(), "MediaAgent": DummyAgent()})
        tasks = [
            Task(task_id="t1", action="get_info", params={}, agent="InfoAgent"),
            Task(task_id="t2", action="play_media", params={}, agent="MediaAgent"),
        ]
        start = time.perf_counter()
        completed = await queue.execute_plan(tasks, timeout=2.0)
        elapsed = time.perf_counter() - start
        if any(task.status != "done" for task in completed):
            raise AssertionError("Independent tasks did not complete successfully.")
        return elapsed

    elapsed = asyncio.run(_run())
    if elapsed >= 0.28:
        raise AssertionError(f"Tasks did not run in parallel enough (elapsed={elapsed:.3f}s).")


def test_synthesis_safety() -> None:
    """Phase 4.5 path traversal and AST blocking checks."""
    from core.code_sandbox import ast_scan
    from core.skill_loader import _safe_filepath

    try:
        _safe_filepath("../../evil.py")
    except ValueError:
        pass
    else:
        raise AssertionError("_safe_filepath accepted a traversal attempt.")

    blocked = ast_scan('import os\nos.system("dir")\n')
    if blocked.passed:
        raise AssertionError("AST scanner allowed os.system.")

    allowed = ast_scan('import requests\nprint("ok")\n')
    if not allowed.passed:
        raise AssertionError("AST scanner rejected a safe requests import.")


def test_migration_script() -> None:
    """Phase 5 migration script should bootstrap schema and archive legacy history once."""
    import core.database as database
    from scripts.migrate_v2_to_v3 import LEGACY_EPISODE_ID, MIGRATION_PREF_KEY, migrate_database

    with temporary_database() as db_path:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS user_prefs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO conversation_history (role, content, ts) VALUES (?, ?, ?)",
                ("user", "I prefer jazz music", "2026-03-01T09:00:00"),
            )
            conn.execute(
                "INSERT INTO conversation_history (role, content, ts) VALUES (?, ?, ?)",
                ("assistant", "Understood. Jazz it is.", "2026-03-01T09:00:10"),
            )

        result = migrate_database(db_path)
        if not result.conversation_rows:
            raise AssertionError("Migration did not see the legacy conversation rows.")

        with sqlite3.connect(db_path) as conn:
            episode = conn.execute(
                "SELECT id FROM episodes WHERE id = ?",
                (LEGACY_EPISODE_ID,),
            ).fetchone()
            pref = conn.execute(
                "SELECT value FROM user_prefs WHERE key = ?",
                (MIGRATION_PREF_KEY,),
            ).fetchone()

        if not episode:
            raise AssertionError("Legacy conversation was not migrated into episodes.")
        if not pref or pref[0] != "1":
            raise AssertionError("Migration completion marker was not written.")


def test_frontend_phase5_sources() -> None:
    """Phase 5 frontend sources should include the entity panel and agent status plumbing."""
    dashboard_path = ROOT_DIR / "frontend" / "src" / "components" / "DashboardMode.jsx"
    entity_panel_path = ROOT_DIR / "frontend" / "src" / "components" / "EntityPanel.jsx"
    titlebar_path = ROOT_DIR / "frontend" / "src" / "components" / "TitleBar.jsx"
    app_path = ROOT_DIR / "frontend" / "src" / "App.jsx"

    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    entity_panel_source = entity_panel_path.read_text(encoding="utf-8")
    titlebar_source = titlebar_path.read_text(encoding="utf-8")
    app_source = app_path.read_text(encoding="utf-8")

    if "EntityPanel" not in dashboard_source:
        raise AssertionError("DashboardMode is not rendering the entity panel.")
    if "ENTITY MEMORY" not in entity_panel_source:
        raise AssertionError("EntityPanel header text is missing.")
    if "activeAgent" not in titlebar_source:
        raise AssertionError("TitleBar is not deriving/displaying active agent status.")
    if "get_entity_panel" not in app_source:
        raise AssertionError("App is not polling backend entity data for the dashboard.")


def test_phase_modules_import() -> None:
    """Critical Phase 4.5 modules should import cleanly."""
    modules = [
        "agents.orchestrator",
        "agents.synthesis_agent",
        "core.code_sandbox",
        "core.skill_loader",
    ]
    for module_name in modules:
        importlib.import_module(module_name)


def main() -> int:
    """Run the focused suite and return an exit code."""
    print("Jarvis v3 Phase 5 validation suite")
    print("=" * 36)

    suite = [
        ("Schema bootstrap", test_schema_bootstrap),
        ("Working memory round-trip", test_working_memory_roundtrip),
        ("Entity contradiction handling", test_entity_store_contradictions),
        ("Reasoning trigger heuristic", test_reasoning_trigger),
        ("Task queue parallelism", test_task_queue_parallelism),
        ("Synthesis safety checks", test_synthesis_safety),
        ("Migration script", test_migration_script),
        ("Frontend Phase 5 sources", test_frontend_phase5_sources),
        ("Critical phase imports", test_phase_modules_import),
    ]

    for name, fn in suite:
        run_test(name, fn)

    failures = [result for result in RESULTS if not result.passed]
    print("=" * 36)
    print(f"Passed: {len(RESULTS) - len(failures)}")
    print(f"Failed: {len(failures)}")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
