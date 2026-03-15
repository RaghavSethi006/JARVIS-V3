"""
core/memory/entity_extractor.py

Post-exchange async pipeline.
Extracts entities, facts, and relationships from a conversation exchange
and persists them to the EntityStore. Runs in background - never blocks responses.
"""

import json
from core.logger import logger

try:
    from core.llm_client import LLMClient
    _LLM_AVAILABLE = True
except ImportError:
    LLMClient = None
    _LLM_AVAILABLE = False
    logger.warning("EntityExtractor: LLMClient not available. Entity extraction disabled.")

from .entity_store import EntityStore


EXTRACTION_PROMPT = """
You are an entity extraction system for a personal AI assistant.
Given a conversation exchange, extract ALL named entities.

Entity types to detect: person, project, topic, place, organization, concept

For each entity found, extract:
- Facts stated or implied about it
- Relationships between entities (e.g., "Alex works on the Jarvis project")
- Any open questions or unresolved status (e.g., "unclear if bug was fixed")
- Updated attributes (e.g., location changed, status changed)

Return ONLY valid JSON:
{
  "entities": [
    {
      "name": "canonical name",
      "type": "person|project|topic|place|organization|concept",
      "facts": ["fact 1", "fact 2"],
      "attributes": {"key": "value"},
      "aliases": ["other name used"],
      "open_questions": ["question about this entity"],
      "relationships": [
        {"other_entity": "name", "other_type": "type", "relation": "works_on|knows|part_of|uses|owns|contains"}
      ]
    }
  ]
}

If no meaningful entities found, return {"entities": []}

Exchange:
{exchange}
"""


class EntityExtractor:
    """LLM-powered entity extraction pipeline for memory updates."""

    def __init__(self, entity_store: EntityStore):
        self.store = entity_store
        self.llm = LLMClient.get() if _LLM_AVAILABLE else None

    async def process_exchange(
        self,
        user_input: str,
        jarvis_response: str,
        source_episode: str = "",
    ) -> list[str]:
        """
        Main entry point. Call this after every exchange (fire-and-forget async).
        Returns list of entity_ids that were found/updated.
        """
        if self.llm is None:
            return []

        exchange_text = f"User: {user_input}\nJARVIS: {jarvis_response}"

        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(exchange=exchange_text)}],
                system="You are a precise entity extraction system. Return only valid JSON.",
                max_tokens=800,
                temperature=0.0,
            )
            clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(clean)
        except Exception as e:
            logger.debug(f"EntityExtractor: Extraction failed: {e}")
            return []

        touched_ids = []
        entities_in_exchange = {}

        for item in data.get("entities", []):
            entity_id = await self._process_entity(item, source_episode)
            if entity_id:
                touched_ids.append(entity_id)
                entities_in_exchange[item["name"]] = entity_id

        for item in data.get("entities", []):
            if item["name"] not in entities_in_exchange:
                continue
            entity_a_id = entities_in_exchange[item["name"]]
            for rel in item.get("relationships", []):
                other_name = rel.get("other_entity", "")
                other_type = rel.get("other_type", "concept")
                relation = rel.get("relation", "related_to")

                entity_b_id = self.store.resolve(other_name, other_type)
                if not entity_b_id:
                    entity_b_id = self.store.create_entity(other_name, other_type)

                self.store.add_relationship(entity_a_id, relation, entity_b_id)

        return touched_ids

    async def _process_entity(self, item: dict, source_episode: str) -> str | None:
        name = item.get("name", "").strip()
        entity_type = item.get("type", "concept")
        if not name:
            return None

        entity_id = self.store.resolve(name, entity_type)
        if not entity_id:
            entity_id = self.store.create_entity(
                name=name,
                entity_type=entity_type,
                attributes=item.get("attributes", {}),
                aliases=item.get("aliases", []),
            )
        else:
            for key, value in item.get("attributes", {}).items():
                self.store.update_attribute(entity_id, key, value)
            for alias in item.get("aliases", []):
                self.store.add_alias(entity_id, alias)

        for fact in item.get("facts", []):
            self.store.add_fact(
                entity_id,
                fact,
                confidence=0.95,
                source_episode=source_episode,
            )

        for question in item.get("open_questions", []):
            self.store.add_thread(entity_id, question)

        self.store.touch(entity_id)
        return entity_id
