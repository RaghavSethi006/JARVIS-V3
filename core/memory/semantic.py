"""
Semantic Memory — continuously-updated user profile.
Stores facts, preferences, habits, and behavioral patterns.
"""

from core.database import _conn, _lock, _ensure_schema
from core.logger import logger


class SemanticMemory:
    """User fact store — preferences, habits, personal info persisted in SQLite."""

    CATEGORIES = ["preference", "habit", "personal", "work", "technical", "social"]

    def set_fact(self, category: str, key: str, value: str,
                 confidence: float = 0.9, source_episode: str = "") -> None:
        """Set or update a user fact. Upserts on (category, key) unique constraint."""
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                conn.execute(
                    """INSERT INTO user_facts (category, key, value, confidence, source_episode)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(category, key) DO UPDATE SET
                       value=excluded.value, confidence=excluded.confidence,
                       source_episode=excluded.source_episode,
                       last_updated=datetime('now')""",
                    (category, key, value, confidence, source_episode)
                )
        except Exception as e:
            logger.error(f"SemanticMemory: set_fact failed: {e}")

    def get_fact(self, category: str, key: str) -> str | None:
        """Retrieve a single fact value by category and key."""
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                row = conn.execute(
                    "SELECT value FROM user_facts WHERE category=? AND key=?",
                    (category, key)
                ).fetchone()
                return row["value"] if row else None
        except Exception as e:
            logger.error(f"SemanticMemory: get_fact failed: {e}")
            return None

    def get_all_facts(self) -> list[dict]:
        """Retrieve all stored user facts."""
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                rows = conn.execute(
                    "SELECT category, key, value, confidence FROM user_facts ORDER BY category"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"SemanticMemory: get_all_facts failed: {e}")
            return []

    def to_context_string(self) -> str:
        """Build user profile block for prompt injection."""
        facts = self.get_all_facts()
        if not facts:
            return ""
        lines = ["[USER PROFILE]"]
        by_cat: dict[str, list[str]] = {}
        for f in facts:
            by_cat.setdefault(f["category"], []).append(f"{f['key']}: {f['value']}")
        for cat, items in by_cat.items():
            lines.append(f"  {cat.title()}: {', '.join(items)}")
        return "\n".join(lines)
