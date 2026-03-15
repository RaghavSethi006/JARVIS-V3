"""
core/memory/entity_store.py

CRUD operations for the entity knowledge graph.
Handles storage, retrieval, fact management,
relationship tracking, and alias resolution.
"""

import difflib
import json
import re
from datetime import datetime

try:
    from rapidfuzz import fuzz as _rapidfuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _rapidfuzz = None
    _RAPIDFUZZ_AVAILABLE = False

from core.database import _conn, _lock
from core.logger import logger

if not _RAPIDFUZZ_AVAILABLE:
    logger.warning("EntityStore: rapidfuzz not installed. Falling back to difflib for fuzzy matching.")


def _slugify(name: str, entity_type: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"{entity_type}_{slug}"


def _fuzz_ratio(a: str, b: str) -> float:
    if _RAPIDFUZZ_AVAILABLE and _rapidfuzz is not None:
        return float(_rapidfuzz.ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


class EntityStore:
    """CRUD and lookup utilities for the entity knowledge graph."""

    # -- CRUD -----------------------------------------------------------------

    def create_entity(
        self,
        name: str,
        entity_type: str,
        attributes: dict | None = None,
        aliases: list | None = None,
    ) -> str:
        """Create a new entity record or return the existing id."""
        entity_id = _slugify(name, entity_type)
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO entities
                   (id, type, canonical_name, aliases, attributes, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entity_id,
                    entity_type,
                    name,
                    json.dumps(aliases or []),
                    json.dumps(attributes or {}),
                    datetime.now().isoformat(),
                ),
            )
        logger.info(f"EntityStore: Created entity [{entity_id}]")
        return entity_id

    def get_entity(self, entity_id: str) -> dict | None:
        """Fetch a fully hydrated entity by id."""
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["aliases"] = json.loads(data["aliases"])
            data["attributes"] = json.loads(data["attributes"])
            data["facts"] = self.get_facts(entity_id)
            data["relationships"] = self.get_relationships(entity_id)
            data["open_threads"] = self.get_open_threads(entity_id)
            return data

    def update_attribute(self, entity_id: str, key: str, value) -> None:
        """Update a single attribute for an entity."""
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if not row:
                return
            attrs = json.loads(row["attributes"])
            attrs[key] = value
            conn.execute(
                "UPDATE entities SET attributes = ?, last_updated = ? WHERE id = ?",
                (json.dumps(attrs), datetime.now().isoformat(), entity_id),
            )

    def add_alias(self, entity_id: str, alias: str) -> None:
        """Add a new alias to an entity if it does not already exist."""
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT aliases FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if not row:
                return
            aliases = json.loads(row["aliases"])
            if alias.lower() not in [a.lower() for a in aliases]:
                aliases.append(alias)
                conn.execute(
                    "UPDATE entities SET aliases = ? WHERE id = ?",
                    (json.dumps(aliases), entity_id),
                )

    def touch(self, entity_id: str) -> None:
        """Update last_mentioned timestamp."""
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE entities SET last_mentioned = ? WHERE id = ?",
                (datetime.now().isoformat(), entity_id),
            )

    # -- Facts ----------------------------------------------------------------

    def add_fact(
        self,
        entity_id: str,
        fact: str,
        confidence: float = 1.0,
        source_episode: str = "",
    ) -> int:
        """Add a fact to the entity, superseding contradictions if detected."""
        existing = self.get_facts(entity_id)
        for ef in existing:
            if self._facts_contradict(fact, ef["fact"]):
                with _lock, _conn() as conn:
                    conn.execute(
                        "UPDATE entity_facts SET is_superseded = 1 WHERE id = ?",
                        (ef["id"],),
                    )
                logger.info(f"EntityStore: Superseded fact [{ef['id']}] for {entity_id}")

        with _lock, _conn() as conn:
            cur = conn.execute(
                """INSERT INTO entity_facts (entity_id, fact, confidence, source_episode)
                   VALUES (?, ?, ?, ?)""",
                (entity_id, fact, confidence, source_episode),
            )
            return int(cur.lastrowid)

    def get_facts(self, entity_id: str, include_superseded: bool = False) -> list[dict]:
        """Return facts for an entity, optionally including superseded ones."""
        with _lock, _conn() as conn:
            query = "SELECT * FROM entity_facts WHERE entity_id = ?"
            params = [entity_id]
            if not include_superseded:
                query += " AND is_superseded = 0"
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def _facts_contradict(self, new_fact: str, old_fact: str) -> bool:
        """Heuristic fallback to detect contradictory facts."""
        new_lower = new_fact.lower()
        old_lower = old_fact.lower()
        contradiction_indicators = [
            ("lives in", "moved to"),
            ("works at", "now works at"),
            ("is", "is no longer"),
            ("has", "no longer has"),
        ]
        for old_key, new_key in contradiction_indicators:
            if old_key in old_lower and new_key in new_lower:
                return True
        return False

    # -- Relationships ---------------------------------------------------------

    def add_relationship(
        self,
        entity_a: str,
        relation: str,
        entity_b: str,
        confidence: float = 1.0,
    ) -> None:
        """Create or update a relationship between two entities."""
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entity_relationships
                   (entity_a, relation, entity_b, confidence, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity_a, relation, entity_b, confidence, datetime.now().isoformat()),
            )

    def get_relationships(self, entity_id: str) -> list[dict]:
        """Return relationships where the entity is either side of the edge."""
        with _lock, _conn() as conn:
            rows = conn.execute(
                """SELECT * FROM entity_relationships
                   WHERE entity_a = ? OR entity_b = ?""",
                (entity_id, entity_id),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Threads ---------------------------------------------------------------

    def add_thread(self, entity_id: str, question: str) -> int:
        """Create an open thread (question) for an entity."""
        with _lock, _conn() as conn:
            cur = conn.execute(
                "INSERT INTO entity_threads (entity_id, question) VALUES (?, ?)",
                (entity_id, question),
            )
            return int(cur.lastrowid)

    def resolve_thread(self, thread_id: int) -> None:
        """Mark an entity thread as resolved."""
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE entity_threads SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), thread_id),
            )

    def get_open_threads(self, entity_id: str) -> list[dict]:
        """Return open threads for a given entity."""
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_threads WHERE entity_id = ? AND status = 'open'",
                (entity_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Resolution ------------------------------------------------------------

    def resolve(self, name: str, entity_type: str) -> str | None:
        """Resolve a name to an existing entity id, if possible."""
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT id FROM entities WHERE LOWER(canonical_name) = LOWER(?) AND type = ?",
                (name, entity_type),
            ).fetchone()
            if row:
                return row["id"]

            all_entities = conn.execute(
                "SELECT id, aliases FROM entities WHERE type = ?", (entity_type,)
            ).fetchall()
            for entity in all_entities:
                aliases = json.loads(entity["aliases"])
                for alias in aliases:
                    if alias.lower() == name.lower():
                        return entity["id"]

            for entity in all_entities:
                canonical = conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (entity["id"],)
                ).fetchone()["canonical_name"]
                score = _fuzz_ratio(name.lower(), canonical.lower())
                if score >= 85:
                    return entity["id"]

        return None

    def get_all_entities(self, entity_type: str | None = None) -> list[dict]:
        """Return all entities, optionally filtered by type."""
        with _lock, _conn() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE type = ? ORDER BY last_mentioned DESC",
                    (entity_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY last_mentioned DESC"
                ).fetchall()
            result = []
            for r in rows:
                data = dict(r)
                data["aliases"] = json.loads(data["aliases"])
                data["attributes"] = json.loads(data["attributes"])
                result.append(data)
            return result

    def to_context_string(self, entity_names: list[str]) -> str:
        """Build a context block for the given entity names."""
        if not entity_names:
            return ""
        lines: list[str] = []
        for name in entity_names:
            for etype in ["person", "project", "topic", "organization", "place", "concept"]:
                entity_id = self.resolve(name, etype)
                if entity_id:
                    entity = self.get_entity(entity_id)
                    if entity:
                        lines.append(self._format_entity(entity))
                    break
        if not lines:
            return ""
        return "[ENTITY MEMORY]\n" + "\n".join(lines)

    def _format_entity(self, entity: dict) -> str:
        lines = [f"  [{entity['type'].upper()}] {entity['canonical_name']}"]
        attrs = entity.get("attributes", {})
        if attrs:
            attr_str = ", ".join(f"{k}: {v}" for k, v in attrs.items())
            lines.append(f"    Attributes: {attr_str}")
        facts = entity.get("facts", [])
        if facts:
            for fact in facts[:5]:
                lines.append(f"    - {fact['fact']}")
        threads = entity.get("open_threads", [])
        if threads:
            for thread in threads[:2]:
                lines.append(f"    ? Open: {thread['question']}")
        return "\n".join(lines)
