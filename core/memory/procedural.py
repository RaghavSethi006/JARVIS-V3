"""
Procedural Memory — named multi-step workflows learned from usage.
"""

import json
from core.database import _conn, _lock, _ensure_schema
from core.logger import logger


class ProceduralMemory:
    """Stores and retrieves named multi-step procedures/workflows."""

    def save_procedure(self, procedure_id: str, name: str,
                       trigger_phrases: list[str], steps: list[dict]) -> None:
        """Save or replace a named procedure."""
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                conn.execute(
                    """INSERT OR REPLACE INTO procedures
                       (id, name, trigger_phrases, steps)
                       VALUES (?, ?, ?, ?)""",
                    (procedure_id, name, json.dumps(trigger_phrases), json.dumps(steps))
                )
            logger.info(f"ProceduralMemory: Saved procedure '{name}'")
        except Exception as e:
            logger.error(f"ProceduralMemory: save_procedure failed: {e}")

    def find_by_trigger(self, user_input: str) -> dict | None:
        """Find a procedure matching user input via trigger phrase."""
        all_procs = self.get_all()
        user_lower = user_input.lower()
        for proc in all_procs:
            for phrase in proc["trigger_phrases"]:
                if phrase.lower() in user_lower:
                    return proc
        return None

    def get_all(self) -> list[dict]:
        """Retrieve all stored procedures."""
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                rows = conn.execute("SELECT * FROM procedures").fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d["trigger_phrases"] = json.loads(d["trigger_phrases"])
                    d["steps"] = json.loads(d["steps"])
                    result.append(d)
                return result
        except Exception as e:
            logger.error(f"ProceduralMemory: get_all failed: {e}")
            return []

    def increment_usage(self, procedure_id: str) -> None:
        """Increment usage count and update last_used timestamp."""
        try:
            with _lock, _conn() as conn:
                conn.execute(
                    "UPDATE procedures SET usage_count = usage_count + 1, last_used = datetime('now') WHERE id = ?",
                    (procedure_id,)
                )
        except Exception as e:
            logger.error(f"ProceduralMemory: increment_usage failed: {e}")
