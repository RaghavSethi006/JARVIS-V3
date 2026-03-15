"""
scripts/test_entities.py
Targeted test for Phase 2 Entity Memory components
"""

import sys
import os
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.database import init_db
from core.memory.entity_store import EntityStore
from core.memory.entity_extractor import EntityExtractor
from core.llm_client import LLMClient
from unittest.mock import patch, AsyncMock

async def run_test():
    print("Initialize Database...")
    init_db()
    
    print("Initialize EntityStore & EntityExtractor...")
    store = EntityStore()
    
    # Create the extractor with a mocked llm client to avoid singleton state issues
    mock_llm = AsyncMock()
    extractor = EntityExtractor(store)
    extractor.llm = mock_llm

    print("Test: Extracting entities from conversation...")
    user_msg = "My colleague Alex fixed the gesture bug in the Jarvis project today."
    jarvis_msg = "That is great news, I have updated the status for the gesture bug."
    
    # Mock LLM response to avoid needing API keys or local models during test
    mock_json = """
{
  "entities": [
    {
      "name": "Alex",
      "type": "person",
      "facts": ["fixed the gesture bug today"],
      "attributes": {"role": "colleague"},
      "relationships": [
         {"other_entity": "Jarvis", "other_type": "project", "relation": "works_on"}
      ]
    },
    {
      "name": "Jarvis",
      "type": "project",
      "facts": ["has a gesture bug that was fixed"]
    }
  ]
}
"""
    import json
    
    async def mock_process_exchange(user_input, jarvis_response, source_episode, mock_json_str):
        data = json.loads(mock_json_str)
        touched_ids = []
        entities_in_exchange = {}

        for item in data.get("entities", []):
            entity_id = await extractor._process_entity(item, source_episode)
            if entity_id:
                touched_ids.append(entity_id)
                entities_in_exchange[item["name"]] = entity_id

        # Process cross-entity relationships
        for item in data.get("entities", []):
            if item["name"] not in entities_in_exchange:
                continue
            entity_a_id = entities_in_exchange[item["name"]]
            for rel in item.get("relationships", []):
                other_name = rel.get("other_entity", "")
                other_type = rel.get("other_type", "concept")
                relation = rel.get("relation", "related_to")

                # Resolve or create the other entity
                entity_b_id = store.resolve(other_name, other_type)
                if not entity_b_id:
                    entity_b_id = store.create_entity(other_name, other_type)

                store.add_relationship(entity_a_id, relation, entity_b_id)

        return touched_ids

    touched_ids = await mock_process_exchange(user_msg, jarvis_msg, "test_session_1", mock_json)
    print(f"Entities touched: {touched_ids}")
    
    print("Test: Verify facts & entities stored...")
    entities = store.get_all_entities()
    print(f"Total entities found: {len(entities)}")
    for e in entities:
        print(f"  [{e['type']}] {e['canonical_name']} ({e['id']})")
        facts = store.get_facts(e["id"])
        print(f"    Facts: {[f['fact'] for f in facts]}")
        rels = store.get_relationships(e["id"])
        print(f"    Relationships: {rels}")

    print("\nTest: Superseding facts...")
    alex_id = store.resolve("Alex", "person")
    if alex_id:
        new_msg = "Alex moved to Calgary and is no longer in Vancouver."
        mock_json_2 = """
{
  "entities": [
    {
      "name": "Alex",
      "type": "person",
      "facts": ["moved to Calgary", "is no longer in Vancouver"],
      "attributes": {"location": "Calgary"}
    }
  ]
}
"""
        await mock_process_exchange(new_msg, "I will note Alex's new location.", "test_session_2", mock_json_2)
        print("Updated facts for Alex:")
        facts = store.get_facts(alex_id)
        print(f"    Active Facts: {[f['fact'] for f in facts]}")
        all_facts = store.get_facts(alex_id, include_superseded=True)
        print(f"    All Facts: {[f['fact'] + (' (superseded)' if f['is_superseded'] else '') for f in all_facts]}")

    print("\nTest: Context string generation...")
    ctx = store.to_context_string(["Alex", "Jarvis"])
    print("Context Block:\n", ctx)

    return True

if __name__ == "__main__":
    asyncio.run(run_test())
