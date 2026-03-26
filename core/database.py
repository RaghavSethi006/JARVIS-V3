"""SQLite persistence for alarms, reminders, conversation history, and user preferences."""

import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvis.db")

_lock = threading.Lock()
_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS alarms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_time TEXT NOT NULL,
        message TEXT NOT NULL,
        alarm_type TEXT DEFAULT 'once',
        cron_expr TEXT
    );
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_time TEXT NOT NULL,
        task TEXT NOT NULL
    );
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

    -- Phase 1: Memory Core tables
    CREATE TABLE IF NOT EXISTS episodes (
        id TEXT PRIMARY KEY,
        session_start TEXT NOT NULL,
        session_end TEXT NOT NULL,
        summary TEXT NOT NULL,
        topics TEXT,
        mood TEXT,
        embedding_id TEXT,
        raw_exchange_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS user_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        confidence REAL DEFAULT 0.9,
        source_episode TEXT,
        last_updated TEXT DEFAULT (datetime('now')),
        UNIQUE(category, key)
    );

    CREATE TABLE IF NOT EXISTS procedures (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        trigger_phrases TEXT,
        steps TEXT NOT NULL,
        usage_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        last_used TEXT
    );

    CREATE TABLE IF NOT EXISTS working_memory_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        timestamp TEXT DEFAULT (datetime('now'))
    );

    -- Phase 2: Entity Memory tables
    CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        aliases TEXT DEFAULT '[]',
        attributes TEXT DEFAULT '{}',
        interest_score REAL DEFAULT 0.5,
        created_at TEXT DEFAULT (datetime('now')),
        last_updated TEXT DEFAULT (datetime('now')),
        last_mentioned TEXT
    );

    CREATE TABLE IF NOT EXISTS entity_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT NOT NULL,
        fact TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        source_episode TEXT DEFAULT '',
        contradicts_fact_id INTEGER DEFAULT NULL,
        is_superseded INTEGER DEFAULT 0,
        timestamp TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (entity_id) REFERENCES entities(id)
    );

    CREATE TABLE IF NOT EXISTS entity_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_a TEXT NOT NULL,
        relation TEXT NOT NULL,
        entity_b TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        timestamp TEXT DEFAULT (datetime('now')),
        UNIQUE(entity_a, relation, entity_b)
    );

    CREATE TABLE IF NOT EXISTS entity_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT NOT NULL,
        question TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        created_at TEXT DEFAULT (datetime('now')),
        resolved_at TEXT DEFAULT NULL
    );

    -- Phase 4.5: Synthesised skills registry
    CREATE TABLE IF NOT EXISTS synthesised_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_name TEXT NOT NULL UNIQUE,
        class_name TEXT NOT NULL,
        file_name TEXT NOT NULL,
        capability_description TEXT NOT NULL,
        trigger_gap TEXT NOT NULL,
        bus_events TEXT NOT NULL,
        dependencies TEXT DEFAULT '[]',
        synthesis_date TEXT DEFAULT (datetime('now')),
        last_loaded TEXT,
        active INTEGER DEFAULT 1,
        validation_passed INTEGER DEFAULT 0,
        version INTEGER DEFAULT 1,
        notes TEXT DEFAULT ''
    );
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn):
    conn.executescript(_SCHEMA_SQL)


def init_db():
    with _lock, _conn() as conn:
        _ensure_schema(conn)


def save_alarm(trigger_time: datetime, message: str) -> int:
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO alarms (trigger_time, message) VALUES (?, ?)",
            (trigger_time.isoformat(), message),
        )
        return cur.lastrowid


def load_pending_alarms() -> list[dict]:
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM alarms WHERE datetime(trigger_time) > datetime('now')"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_alarm(alarm_id: int):
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))


def append_history(role: str, content: str):
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO conversation_history (role, content) VALUES (?, ?)",
            (role, content),
        )
        conn.execute(
            """
            DELETE FROM conversation_history WHERE id NOT IN (
                SELECT id FROM conversation_history ORDER BY id DESC LIMIT 20
            )
            """
        )


def load_history() -> list[dict]:
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT role, content FROM conversation_history ORDER BY id"
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


def set_pref(key: str, value: str):
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO user_prefs (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_pref(key: str, default: str = "") -> str:
    with _lock, _conn() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT value FROM user_prefs WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default
