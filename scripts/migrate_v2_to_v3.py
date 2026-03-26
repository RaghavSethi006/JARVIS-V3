"""Idempotent migration helper for upgrading a legacy Jarvis database to v3."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import DB_PATH as DEFAULT_DB_PATH
from core.database import _SCHEMA_SQL


LEGACY_EPISODE_ID = "legacy_v2_conversation"
MIGRATION_PREF_KEY = "migration.v2_to_v3.completed"


@dataclass
class MigrationResult:
    """Summary of what the migration script changed."""

    migrated_history: bool
    conversation_rows: int
    notes: list[str]


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the target database with row access enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all v3 tables if they do not already exist."""
    conn.executescript(_SCHEMA_SQL)


def _migration_complete(conn: sqlite3.Connection) -> bool:
    """Return whether the idempotency marker already exists."""
    row = conn.execute(
        "SELECT value FROM user_prefs WHERE key = ?",
        (MIGRATION_PREF_KEY,),
    ).fetchone()
    return bool(row and row["value"] == "1")


def _fetch_conversation_history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Read any legacy conversation rows in stable order."""
    return conn.execute(
        "SELECT role, content, ts FROM conversation_history ORDER BY id"
    ).fetchall()


def _build_summary(rows: list[sqlite3.Row]) -> tuple[str, list[str], str]:
    """Create a deterministic summary for the migrated legacy conversation."""
    user_examples = [row["content"] for row in rows if row["role"] == "user"][:3]
    assistant_examples = [row["content"] for row in rows if row["role"] != "user"][:2]

    summary_parts = [
        "Migrated legacy Jarvis conversation history from a pre-v3 database.",
        f"The archive contains {len(rows)} exchanges.",
    ]
    if user_examples:
        summary_parts.append("User topics included: " + "; ".join(user_examples))
    if assistant_examples:
        summary_parts.append("Jarvis responses included: " + "; ".join(assistant_examples))

    topics = ["legacy migration", "conversation history"]
    if user_examples:
        topics.append("user requests")
    mood = "neutral"
    return " ".join(summary_parts), topics[:5], mood


def _build_snapshot(rows: list[sqlite3.Row], session_start: str) -> str:
    """Create a working-memory style snapshot of the migrated history."""
    exchanges = [
        {
            "role": row["role"],
            "content": row["content"],
            "timestamp": row["ts"],
            "intent": None,
            "entities_mentioned": [],
            "emotional_tone": None,
        }
        for row in rows
    ]
    return json.dumps(
        {
            "session_id": LEGACY_EPISODE_ID,
            "session_start": session_start,
            "exchanges": exchanges,
            "current_task": None,
            "current_goal": None,
            "active_entities": [],
            "user_mood": "neutral",
        }
    )


def migrate_database(db_path: str) -> MigrationResult:
    """Apply the v3 schema and migrate legacy conversation history once."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    with _connect(db_path) as conn:
        _ensure_schema(conn)

        if _migration_complete(conn):
            return MigrationResult(
                migrated_history=False,
                conversation_rows=0,
                notes=["Migration marker already present; nothing else to do."],
            )

        rows = _fetch_conversation_history(conn)
        notes: list[str] = []
        migrated_history = False

        if rows:
            existing_episode = conn.execute(
                "SELECT 1 FROM episodes WHERE id = ?",
                (LEGACY_EPISODE_ID,),
            ).fetchone()
            if not existing_episode:
                session_start = rows[0]["ts"] or "1970-01-01T00:00:00"
                session_end = rows[-1]["ts"] or session_start
                summary, topics, mood = _build_summary(rows)
                conn.execute(
                    """
                    INSERT INTO episodes (
                        id, session_start, session_end, summary, topics, mood, raw_exchange_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        LEGACY_EPISODE_ID,
                        session_start,
                        session_end,
                        summary,
                        json.dumps(topics),
                        mood,
                        len(rows),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO working_memory_snapshots (session_id, snapshot)
                    VALUES (?, ?)
                    """,
                    (
                        LEGACY_EPISODE_ID,
                        _build_snapshot(rows, session_start),
                    ),
                )
                migrated_history = True
                notes.append(f"Migrated {len(rows)} conversation rows into episode '{LEGACY_EPISODE_ID}'.")
            else:
                notes.append("Legacy history episode already exists; skipped reinsertion.")
        else:
            notes.append("No legacy conversation history rows found.")

        conn.execute(
            "INSERT OR REPLACE INTO user_prefs (key, value) VALUES (?, ?)",
            (MIGRATION_PREF_KEY, "1"),
        )
        notes.append("Recorded migration completion marker.")
        return MigrationResult(
            migrated_history=migrated_history,
            conversation_rows=len(rows),
            notes=notes,
        )


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Upgrade an existing Jarvis database to the v3 schema.")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database to migrate. Defaults to the main jarvis.db.",
    )
    args = parser.parse_args()

    result = migrate_database(os.path.abspath(args.db))
    print(f"Database: {os.path.abspath(args.db)}")
    print(f"Legacy rows scanned: {result.conversation_rows}")
    print(f"Legacy history migrated: {'yes' if result.migrated_history else 'no'}")
    for note in result.notes:
        print(f"- {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
