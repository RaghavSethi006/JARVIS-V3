"""
core/memory/manager.py

Central MemoryManager — the single interface all other code
uses to interact with the memory system.
"""

import json
import threading
from datetime import datetime
from typing import Optional

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    spacy = None
    _SPACY_AVAILABLE = False

from core.logger import logger

if not _SPACY_AVAILABLE:
    logger.warning("MemoryManager: spacy not installed. Entity name pre-pass disabled.")

try:
    from core.llm_client import LLMClient
    _LLM_AVAILABLE = True
except ImportError:
    LLMClient = None
    _LLM_AVAILABLE = False
    logger.warning("MemoryManager: LLMClient not available. Session summarization disabled.")

from .working import WorkingMemory
from .episodic import EpisodicMemory
from .semantic import SemanticMemory
from .procedural import ProceduralMemory
from .entity_store import EntityStore
from .entity_extractor import EntityExtractor


SESSION_SUMMARIZER_PROMPT = """\
You are a memory archivist for an AI assistant named JARVIS.
Given the conversation below, produce a concise summary (3-5 sentences) capturing:
- What the user asked or did
- Key information revealed about the user
- Any decisions made or actions taken
- The user's apparent mood or tone

Also extract a list of topics discussed (max 6 words each).
Also extract any personal facts about the user (preferences, habits, work, personal life).

Return ONLY valid JSON:
{{
  "summary": "...",
  "topics": ["topic1", "topic2"],
  "mood": "neutral|positive|frustrated|rushed|relaxed",
  "user_facts": [
    {{"category": "preference|habit|personal|work|technical|social", "key": "...", "value": "...", "confidence": 0.9}}
  ]
}}

Conversation:
{conversation}
"""


class MemoryManager:
    """
    Singleton memory coordinator. Owns all four memory tiers and provides
    context building for LLM prompts + session archival on close.
    """
    _instance: Optional["MemoryManager"] = None

    def __init__(self) -> None:
        self.working = WorkingMemory(
            session_id=self._new_session_id(),
            session_start=datetime.now().isoformat()
        )
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self.entity_store = EntityStore()
        self.entity_extractor = EntityExtractor(self.entity_store)
        self._llm = LLMClient.get() if _LLM_AVAILABLE else None

        # Initialize ChromaDB in background — heavy load, don't block UI
        threading.Thread(target=self.episodic.initialize, daemon=True).start()
        logger.info("MemoryManager: Initialized (session %s).", self.working.session_id)

    @classmethod
    def get(cls) -> "MemoryManager":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _new_session_id(self) -> str:
        """Generate a unique session ID based on current timestamp."""
        return f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def add_exchange(self, role: str, content: str, intent: str = None,
                     entities: list = None) -> None:
        """Record a new exchange in working memory."""
        self.working.add_exchange(
            role=role,
            content=content,
            intent=intent,
            entities_mentioned=entities or []
        )

    def build_context(self, user_input: str) -> str:
        """
        Build the full memory context string to inject into the system prompt.
        Called before every LLM completion.
        """
        parts = []

        # User profile facts
        profile = self.semantic.to_context_string()
        if profile:
            parts.append(profile)

        # Relevant past episodes
        episodes = self.episodic.to_context_string(user_input)
        if episodes:
            parts.append(episodes)

        # Entity context — quick NER pre-pass with spacy, then entity lookup
        entity_names = self._quick_extract_names(user_input)
        if entity_names:
            entity_ctx = self.entity_store.to_context_string(entity_names)
            if entity_ctx:
                parts.append(entity_ctx)

        # Current session state
        session = self.working.to_context_string()
        if session:
            parts.append(session)

        return "\n\n".join(parts)

    def get_messages(self) -> list[dict]:
        """Get current session exchanges in LLM API format."""
        return self.working.to_messages()

    async def close_session(self) -> None:
        """
        Called when the app is closing or after a natural conversation break.
        Summarizes session, saves episode, extracts user facts.
        """
        if len(self.working.exchanges) < 2:
            logger.info("MemoryManager: Too few exchanges to summarize. Skipping.")
            return

        logger.info("MemoryManager: Closing session and summarizing...")
        try:
            await self._summarize_and_store()
        except Exception as e:
            logger.error(f"MemoryManager: Session close failed: {e}")

        # Start fresh working memory
        self.working = WorkingMemory(
            session_id=self._new_session_id(),
            session_start=datetime.now().isoformat()
        )

    async def _summarize_and_store(self) -> None:
        """Run LLM summarization and persist to all relevant stores."""
        if self._llm is None:
            logger.warning("MemoryManager: No LLM client — cannot summarize session.")
            return

        # Build conversation text
        conv_lines = []
        for ex in self.working.exchanges:
            speaker = "JARVIS" if ex.role in ("jarvis", "assistant") else "USER"
            conv_lines.append(f"{speaker}: {ex.content}")
        conversation_text = "\n".join(conv_lines)

        prompt = SESSION_SUMMARIZER_PROMPT.format(conversation=conversation_text)
        raw = await self._llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are a precise memory archivist. Return only valid JSON.",
            max_tokens=600,
            temperature=0.1
        )

        try:
            # Strip markdown fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1]  # remove first line
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()
            data = json.loads(clean)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"MemoryManager: Summary parse failed: {e}. Raw: {raw[:200]}")
            return

        # Save episode
        self.episodic.save_episode(
            episode_id=self.working.session_id,
            summary=data.get("summary", "Session with no summary."),
            topics=data.get("topics", []),
            session_start=self.working.session_start,
            session_end=datetime.now().isoformat(),
            mood=data.get("mood", "neutral")
        )

        # Save extracted user facts
        for fact in data.get("user_facts", []):
            self.semantic.set_fact(
                category=fact.get("category", "personal"),
                key=fact.get("key", "unknown"),
                value=fact.get("value", ""),
                confidence=float(fact.get("confidence", 0.8)),
                source_episode=self.working.session_id
            )

        logger.info(f"MemoryManager: Session {self.working.session_id} archived "
                     f"({len(data.get('user_facts', []))} facts extracted).")

    def _quick_extract_names(self, text: str) -> list[str]:
        """Fast spacy NER pass to find entity names before LLM extraction."""
        if not _SPACY_AVAILABLE:
            return []
        try:
            if not hasattr(self, "_nlp"):
                self._nlp = spacy.load("en_core_web_sm")
            doc = self._nlp(text)
            return list({
                ent.text
                for ent in doc.ents
                if ent.label_ in {"PERSON", "ORG", "GPE", "PRODUCT", "WORK_OF_ART"}
            })
        except Exception as e:
            logger.warning(f"MemoryManager: spacy NER failed: {e}")
            return []

    async def post_exchange_pipeline(self, user_input: str, jarvis_response: str):
        """
        Fire-and-forget after every exchange.
        Runs entity extraction in background without blocking.
        """
        if not hasattr(self, "entity_extractor"):
            return
            
        import asyncio
        asyncio.create_task(
            self.entity_extractor.process_exchange(
                user_input, jarvis_response,
                source_episode=self.working.session_id
            )
        )
