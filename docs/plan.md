# JARVIS v3.0 — Detailed Implementation Plan
> Per-phase breakdown: files, code structure, integration points, and testing

---

# PHASE 0 — LLMClient Hardening
> Groq (`llama-3.3-70b-versatile`) is already running. This phase formalises it into a robust singleton with retries, fallback, and smart routing. Zero feature changes.

---

## 0.1 — Dependencies

**`requirements.txt` — no new installs needed for Phase 0.**
Groq SDK is already installed. Just confirm it's present:
```
groq>=0.5.0
```

Optional — only add if you want Phase 4.5 synthesis quality:
```
anthropic>=0.25.0
```

**`config/.env` — formalise existing keys + add optional Anthropic:**
```
# Already set — just make sure these exist
GROQ_API_KEY=your_groq_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Optional — only needed for Phase 4.5 code synthesis
# ANTHROPIC_API_KEY=your_anthropic_key_here
# ANTHROPIC_MODEL=claude-opus-4-6-20250514
```

**`config/settings.yaml` additions:**
```yaml
llm:
  mode: groq                         # groq | local
  groq_model: llama-3.3-70b-versatile
  max_tokens: 1024
  temperature: 0.7
  intent_max_tokens: 512
  intent_temperature: 0.1
  streaming: true
  retry_attempts: 3
  retry_backoff_seconds: 2

  # Optional high-quality lane — only used for synthesis tasks
  smart_routing_enabled: false       # set true when ANTHROPIC_API_KEY is configured
  anthropic_model: claude-opus-4-6-20250514
  smart_tasks:                       # task_type values that route to Anthropic
    - synthesis
    - summarisation
```

---

## 0.2 — New File: `core/llm_client.py`

This is the single point of contact for all LLM calls in the entire codebase. Nothing else imports `groq` or `anthropic` or `llama_cpp` directly.

```python
"""
core/llm_client.py

Unified LLM client. Groq (llama-3.3-70b-versatile) is primary.
Local llama-cpp is the offline fallback.
Anthropic (claude-opus-4-6) is an optional high-quality lane for
synthesis and summarisation tasks only — requires ANTHROPIC_API_KEY.

Usage:
    client = LLMClient.get()

    # Standard call — always uses Groq
    response = await client.complete(messages=[...], system="...")

    # Smart call — routes to Anthropic if task_type in smart_tasks and key is set
    response = await client.complete_smart(messages=[...], system="...", task_type="synthesis")

    # Streaming
    async for chunk in client.stream(messages=[...], system="..."):
        print(chunk)
"""

import asyncio
import os
from typing import AsyncIterator, Optional
from core.logger import logger


class LLMClient:
    _instance = None

    GROQ_MODEL   = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    ANTHRO_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6-20250514")
    MAX_TOKENS   = int(os.environ.get("LLM_MAX_TOKENS", 1024))
    TEMPERATURE  = float(os.environ.get("LLM_TEMPERATURE", 0.7))
    RETRY_ATTEMPTS = 3
    RETRY_BACKOFF  = 2   # seconds, doubles each attempt

    def __init__(self):
        self._groq   = None
        self._anthro = None
        self._local  = None
        self._init_groq()
        self._init_anthropic()   # no-op if key not set

    @classmethod
    def get(cls) -> "LLMClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_groq(self):
        try:
            from groq import AsyncGroq
            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                logger.warning("LLMClient: GROQ_API_KEY not set. Groq disabled.")
                return
            self._groq = AsyncGroq(api_key=api_key)
            logger.info(f"LLMClient: Groq ready ({self.GROQ_MODEL})")
        except ImportError:
            logger.warning("LLMClient: groq package not installed. Groq disabled.")
        except Exception as e:
            logger.error(f"LLMClient: Groq init failed: {e}")

    def _init_anthropic(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return   # silently skip — it's optional
        try:
            import anthropic
            self._anthro = anthropic.AsyncAnthropic(api_key=api_key)
            logger.info(f"LLMClient: Anthropic ready ({self.ANTHRO_MODEL}) — synthesis lane active")
        except ImportError:
            logger.warning("LLMClient: anthropic package not installed. Synthesis lane disabled.")
        except Exception as e:
            logger.error(f"LLMClient: Anthropic init failed: {e}")

    def _init_local(self):
        """Lazy-load local model only when Groq fails and local hasn't been loaded yet."""
        if self._local is not None:
            return
        try:
            from llama_cpp import Llama
            model_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
            )
            if os.path.exists(model_path):
                self._local = Llama(model_path=model_path, n_ctx=8192,
                                    n_threads=4, verbose=False)
                logger.info("LLMClient: Local llama-cpp model loaded (fallback).")
            else:
                logger.warning("LLMClient: No local model file found at models/")
        except Exception as e:
            logger.error(f"LLMClient: Local model init failed: {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Standard completion — always uses Groq, falls back to local."""
        return await self._complete_with_retry(
            messages, system,
            max_tokens or self.MAX_TOKENS,
            temperature or self.TEMPERATURE
        )

    async def complete_smart(
        self,
        messages: list[dict],
        system: str = "",
        task_type: str = "standard",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Smart routing: uses Anthropic for synthesis/summarisation tasks
        if ANTHROPIC_API_KEY is configured. Falls back to Groq otherwise.
        task_type: "synthesis" | "summarisation" | "standard"
        """
        smart_tasks = {"synthesis", "summarisation", "reasoning"}
        if task_type in smart_tasks and self._anthro:
            logger.debug(f"LLMClient: Routing to Anthropic for task_type={task_type}")
            return await self._complete_anthropic(
                messages, system,
                max_tokens or self.MAX_TOKENS,
                temperature or 0.2
            )
        return await self.complete(messages, system, max_tokens, temperature)

    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Streaming completion via Groq."""
        if self._groq:
            async for chunk in self._stream_groq(
                messages, system, max_tokens or self.MAX_TOKENS
            ):
                yield chunk
        else:
            result = await self.complete(messages, system, max_tokens)
            yield result

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _complete_with_retry(
        self, messages, system, max_tokens, temperature
    ) -> str:
        """Groq with retry/backoff, then local model fallback."""
        if self._groq:
            for attempt in range(self.RETRY_ATTEMPTS):
                try:
                    return await self._complete_groq(
                        messages, system, max_tokens, temperature
                    )
                except Exception as e:
                    if attempt < self.RETRY_ATTEMPTS - 1:
                        wait = self.RETRY_BACKOFF * (attempt + 1)
                        logger.warning(
                            f"LLMClient: Groq attempt {attempt + 1} failed: {e}. "
                            f"Retrying in {wait}s..."
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            f"LLMClient: Groq failed after {self.RETRY_ATTEMPTS} attempts: {e}. "
                            f"Falling back to local model."
                        )

        # Fallback: local model
        self._init_local()
        if self._local:
            return await self._complete_local(messages, system, max_tokens)

        return "I'm having trouble connecting to my language model. Please try again."

    async def _complete_groq(
        self, messages, system, max_tokens, temperature
    ) -> str:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await self._groq.chat.completions.create(
            model=self.GROQ_MODEL,
            messages=all_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()

    async def _stream_groq(
        self, messages, system, max_tokens
    ) -> AsyncIterator[str]:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        async with await self._groq.chat.completions.create(
            model=self.GROQ_MODEL,
            messages=all_messages,
            max_tokens=max_tokens,
            stream=True,
        ) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

    async def _complete_anthropic(
        self, messages, system, max_tokens, temperature
    ) -> str:
        try:
            response = await self._anthro.messages.create(
                model=self.ANTHRO_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"LLMClient: Anthropic API error: {e}. Falling back to Groq.")
            return await self._complete_with_retry(
                messages, system, max_tokens, temperature
            )

    async def _complete_local(
        self, messages, system, max_tokens
    ) -> str:
        loop = asyncio.get_event_loop()

        prompt = f"<|system|>\n{system}\n" if system else ""
        for m in messages:
            role = "user" if m["role"] == "user" else "assistant"
            prompt += f"<|{role}|>\n{m['content']}\n"
        prompt += "<|assistant|>\n"

        def _run():
            result = self._local(prompt, max_tokens=max_tokens, stop=["<|user|>"])
            return result["choices"][0]["text"].strip()

        return await loop.run_in_executor(None, _run)
```

---

## 0.3 — Refactor `skills/llm_skill.py`

**Change:** Remove any direct `groq` imports and replace with `LLMClient.get()`.

```python
# OLD (direct groq usage)
from groq import Groq
self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
response = self.client.chat.completions.create(model=GROQ_MODEL, messages=msgs)
text = response.choices[0].message.content

# NEW (through LLMClient)
from core.llm_client import LLMClient
self.llm = LLMClient.get()
text = await self.llm.complete(messages=msgs, system=SYSTEM_PROMPT)
```

**Intent parser — use low temperature, capped tokens:**
```python
async def _parse_intent(self, user_input: str) -> list[dict]:
    response = await self.llm.complete(
        messages=[{"role": "user", "content": user_input}],
        system=INTENT_SYSTEM_PROMPT,
        max_tokens=512,
        temperature=0.1,
    )
    # existing JSON parse logic unchanged
```

**Conversational response:**
```python
async def _generate_response(self, query: str) -> str:
    history = list(self.conversation_history)
    messages = history + [{"role": "user", "content": query}]
    return await self.llm.complete(
        messages=messages,
        system=SYSTEM_PROMPT,
    )
```

---

## 0.4 — `app_config.py` updates

```python
# Update/add these constants
GROQ_MODEL       = "llama-3.3-70b-versatile"
GROQ_MAX_TOKENS  = 1024
LLM_STREAMING    = True

# Optional — only active when ANTHROPIC_API_KEY is set
ANTHROPIC_MODEL  = "claude-opus-4-6-20250514"

# Feature flags — each phase flips these to True
MEMORY_ENABLED          = False   # Phase 1
ENTITY_MEMORY_ENABLED   = False   # Phase 2
AGENTS_ENABLED          = False   # Phase 4
SYNTHESIS_ENABLED       = False   # Phase 4.5
```

---

## Phase 0 Testing Checklist

- [ ] Voice command → intent parsed → skill executed (e.g., "what's the weather")
- [ ] Conversational question answered via Groq `llama-3.3-70b-versatile`
- [ ] TTS speaks the response (Kokoro or pyttsx3 fallback)
- [ ] Groq drops connection → 3 retries with backoff → falls back to local model with warning
- [ ] No `is not a function` JS errors in logs (typeof guard fix working)
- [ ] Biometric login works unchanged
- [ ] Gesture control activates/deactivates
- [ ] All quick action bar buttons respond
- [ ] Pill ↔ Dashboard mode switching works
- [ ] `GROQ_API_KEY` missing → graceful warning, falls back to local model, no crash
- [ ] `ANTHROPIC_API_KEY` not set → app starts fine, no errors (it's optional)

---
---

# PHASE 1 — Memory Core
> 4-tier memory system. Jarvis remembers across sessions.

---

## 1.1 — New Dependencies

```
chromadb>=0.4.0
sentence-transformers>=2.2.0
```

---

## 1.2 — Database Schema Extensions (`core/database.py`)

Add to `_SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,              -- "ep_20250225_143022"
    session_start TEXT NOT NULL,
    session_end TEXT NOT NULL,
    summary TEXT NOT NULL,            -- LLM-generated summary
    topics TEXT,                      -- JSON array of topic strings
    mood TEXT,                        -- detected user mood
    embedding_id TEXT,                -- ChromaDB doc ID
    raw_exchange_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,           -- "preference", "habit", "personal", "work"
    key TEXT NOT NULL,                -- "preferred_music_genre"
    value TEXT NOT NULL,              -- "lofi hip-hop"
    confidence REAL DEFAULT 0.9,
    source_episode TEXT,
    last_updated TEXT DEFAULT (datetime('now')),
    UNIQUE(category, key)
);

CREATE TABLE IF NOT EXISTS procedures (
    id TEXT PRIMARY KEY,              -- "morning_routine"
    name TEXT NOT NULL,               -- "Morning Routine"
    trigger_phrases TEXT,             -- JSON array
    steps TEXT NOT NULL,              -- JSON array of {action, params}
    usage_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS working_memory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    snapshot TEXT NOT NULL,           -- JSON serialized WorkingMemory
    timestamp TEXT DEFAULT (datetime('now'))
);
```

---

## 1.3 — New Package: `core/memory/`

### `core/memory/__init__.py`
```python
from .manager import MemoryManager
__all__ = ["MemoryManager"]
```

### `core/memory/working.py`

```python
"""
Working Memory — active in-session context.
Holds the current conversation with rich annotations, 
active task/goal state, and detected emotional tone.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json


@dataclass
class AnnotatedExchange:
    role: str                          # "user" | "jarvis"
    content: str
    timestamp: str
    intent: Optional[str] = None       # "get_weather", "get_info", etc.
    entities_mentioned: list = field(default_factory=list)
    emotional_tone: Optional[str] = None  # "neutral", "frustrated", "rushed"


@dataclass
class WorkingMemory:
    session_id: str
    session_start: str
    exchanges: list[AnnotatedExchange] = field(default_factory=list)
    current_task: Optional[str] = None
    current_goal: Optional[str] = None
    active_entities: list[str] = field(default_factory=list)
    user_mood: str = "neutral"
    token_count: int = 0

    MAX_EXCHANGES = 40
    MAX_TOKENS = 4000

    def add_exchange(self, role: str, content: str, **kwargs) -> None:
        exchange = AnnotatedExchange(
            role=role,
            content=content,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )
        self.exchanges.append(exchange)
        if len(self.exchanges) > self.MAX_EXCHANGES:
            # Remove oldest but keep system context
            self.exchanges = self.exchanges[-self.MAX_EXCHANGES:]

    def to_messages(self) -> list[dict]:
        """Convert to Claude API messages format."""
        return [
            {"role": e.role if e.role != "jarvis" else "assistant", "content": e.content}
            for e in self.exchanges
        ]

    def to_context_string(self) -> str:
        """Human-readable context block for system prompt injection."""
        if not self.exchanges:
            return ""
        lines = ["[CURRENT SESSION CONTEXT]"]
        if self.current_task:
            lines.append(f"Current task: {self.current_task}")
        if self.active_entities:
            lines.append(f"Active topics: {', '.join(self.active_entities)}")
        lines.append(f"User mood: {self.user_mood}")
        return "\n".join(lines)

    def serialize(self) -> str:
        return json.dumps({
            "session_id": self.session_id,
            "session_start": self.session_start,
            "exchanges": [
                {
                    "role": e.role,
                    "content": e.content,
                    "timestamp": e.timestamp,
                    "intent": e.intent,
                    "entities_mentioned": e.entities_mentioned,
                    "emotional_tone": e.emotional_tone,
                }
                for e in self.exchanges
            ],
            "current_task": self.current_task,
            "current_goal": self.current_goal,
            "active_entities": self.active_entities,
            "user_mood": self.user_mood,
        })

    @classmethod
    def deserialize(cls, data: str) -> "WorkingMemory":
        d = json.loads(data)
        wm = cls(session_id=d["session_id"], session_start=d["session_start"])
        wm.current_task = d.get("current_task")
        wm.current_goal = d.get("current_goal")
        wm.active_entities = d.get("active_entities", [])
        wm.user_mood = d.get("user_mood", "neutral")
        for e in d.get("exchanges", []):
            wm.exchanges.append(AnnotatedExchange(
                role=e["role"],
                content=e["content"],
                timestamp=e["timestamp"],
                intent=e.get("intent"),
                entities_mentioned=e.get("entities_mentioned", []),
                emotional_tone=e.get("emotional_tone"),
            ))
        return wm
```

### `core/memory/episodic.py`

```python
"""
Episodic Memory — compressed per-session summaries with vector embeddings.
Retrieval is by semantic relevance, not recency.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional
import chromadb
from sentence_transformers import SentenceTransformer
from core.logger import logger
from core.database import _conn, _lock


CHROMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory_store", "chroma"
)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class EpisodicMemory:
    def __init__(self):
        self._chroma = None
        self._collection = None
        self._embedder = None
        self._ready = False

    def initialize(self):
        """Call on startup in a background thread."""
        try:
            os.makedirs(CHROMA_PATH, exist_ok=True)
            self._chroma = chromadb.PersistentClient(path=CHROMA_PATH)
            self._collection = self._chroma.get_or_create_collection(
                name="episodes",
                metadata={"hnsw:space": "cosine"}
            )
            self._embedder = SentenceTransformer(EMBEDDING_MODEL)
            self._ready = True
            logger.info("EpisodicMemory: ChromaDB and embedder loaded.")
        except Exception as e:
            logger.error(f"EpisodicMemory: Init failed: {e}")

    def save_episode(self, episode_id: str, summary: str, topics: list[str],
                     session_start: str, session_end: str, mood: str = "neutral"):
        """Store episode in SQLite + ChromaDB."""
        if not self._ready:
            return

        # SQLite
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO episodes 
                   (id, session_start, session_end, summary, topics, mood, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (episode_id, session_start, session_end, summary,
                 json.dumps(topics), mood, episode_id)
            )

        # ChromaDB
        try:
            embedding = self._embedder.encode(summary).tolist()
            self._collection.upsert(
                ids=[episode_id],
                embeddings=[embedding],
                documents=[summary],
                metadatas=[{
                    "session_start": session_start,
                    "topics": json.dumps(topics),
                    "mood": mood
                }]
            )
        except Exception as e:
            logger.error(f"EpisodicMemory: ChromaDB upsert failed: {e}")

    def retrieve_relevant(self, query: str, top_k: int = 3) -> list[dict]:
        """Retrieve top_k most relevant episodes for the given query."""
        if not self._ready:
            return []
        try:
            query_embedding = self._embedder.encode(query).tolist()
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self._collection.count()),
                include=["documents", "metadatas", "distances"]
            )
            episodes = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            ):
                episodes.append({
                    "summary": doc,
                    "session_start": meta.get("session_start", ""),
                    "topics": json.loads(meta.get("topics", "[]")),
                    "relevance": 1 - dist   # convert distance to similarity
                })
            return episodes
        except Exception as e:
            logger.error(f"EpisodicMemory: Retrieval failed: {e}")
            return []

    def to_context_string(self, query: str) -> str:
        """Build a context block of relevant past episodes for prompt injection."""
        episodes = self.retrieve_relevant(query, top_k=3)
        if not episodes:
            return ""
        lines = ["[RELEVANT PAST SESSIONS]"]
        for ep in episodes:
            date = ep["session_start"][:10] if ep["session_start"] else "unknown date"
            lines.append(f"  [{date}]: {ep['summary']}")
        return "\n".join(lines)
```

### `core/memory/semantic.py`

```python
"""
Semantic Memory — continuously-updated user profile.
Stores facts, preferences, habits, and behavioral patterns.
"""

import json
from core.database import _conn, _lock
from core.logger import logger


class SemanticMemory:

    CATEGORIES = ["preference", "habit", "personal", "work", "technical", "social"]

    def set_fact(self, category: str, key: str, value: str,
                 confidence: float = 0.9, source_episode: str = ""):
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT INTO user_facts (category, key, value, confidence, source_episode)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(category, key) DO UPDATE SET
                   value=excluded.value, confidence=excluded.confidence,
                   source_episode=excluded.source_episode,
                   last_updated=datetime('now')""",
                (category, key, value, confidence, source_episode)
            )

    def get_fact(self, category: str, key: str) -> str | None:
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT value FROM user_facts WHERE category=? AND key=?",
                (category, key)
            ).fetchone()
            return row["value"] if row else None

    def get_all_facts(self) -> list[dict]:
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT category, key, value, confidence FROM user_facts ORDER BY category"
            ).fetchall()
            return [dict(r) for r in rows]

    def to_context_string(self) -> str:
        """Build user profile block for prompt injection."""
        facts = self.get_all_facts()
        if not facts:
            return ""
        lines = ["[USER PROFILE]"]
        by_cat = {}
        for f in facts:
            by_cat.setdefault(f["category"], []).append(f"{f['key']}: {f['value']}")
        for cat, items in by_cat.items():
            lines.append(f"  {cat.title()}: {', '.join(items)}")
        return "\n".join(lines)
```

### `core/memory/procedural.py`

```python
"""
Procedural Memory — named multi-step workflows learned from usage.
"""

import json
from core.database import _conn, _lock
from core.logger import logger


class ProceduralMemory:

    def save_procedure(self, procedure_id: str, name: str,
                       trigger_phrases: list[str], steps: list[dict]):
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO procedures
                   (id, name, trigger_phrases, steps)
                   VALUES (?, ?, ?, ?)""",
                (procedure_id, name, json.dumps(trigger_phrases), json.dumps(steps))
            )
        logger.info(f"ProceduralMemory: Saved procedure '{name}'")

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
        with _lock, _conn() as conn:
            rows = conn.execute("SELECT * FROM procedures").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["trigger_phrases"] = json.loads(d["trigger_phrases"])
                d["steps"] = json.loads(d["steps"])
                result.append(d)
            return result

    def increment_usage(self, procedure_id: str):
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE procedures SET usage_count = usage_count + 1, last_used = datetime('now') WHERE id = ?",
                (procedure_id,)
            )
```

### `core/memory/manager.py`

```python
"""
core/memory/manager.py

Central MemoryManager — the single interface all other code
uses to interact with the memory system.
"""

import asyncio
import json
import os
import threading
from datetime import datetime
from typing import Optional
from core.logger import logger
from core.llm_client import LLMClient
from .working import WorkingMemory
from .episodic import EpisodicMemory
from .semantic import SemanticMemory
from .procedural import ProceduralMemory


SESSION_SUMMARIZER_PROMPT = """
You are a memory archivist for an AI assistant named JARVIS.
Given the conversation below, produce a concise summary (3-5 sentences) capturing:
- What the user asked or did
- Key information revealed about the user
- Any decisions made or actions taken
- The user's apparent mood or tone

Also extract a list of topics discussed (max 6 words each).
Also extract any personal facts about the user (preferences, habits, work, personal life).

Return ONLY valid JSON:
{
  "summary": "...",
  "topics": ["topic1", "topic2"],
  "mood": "neutral|positive|frustrated|rushed|relaxed",
  "user_facts": [
    {"category": "preference|habit|personal|work|technical|social", "key": "...", "value": "...", "confidence": 0.0-1.0}
  ]
}

Conversation:
{conversation}
"""


class MemoryManager:
    _instance = None

    def __init__(self):
        self.working = WorkingMemory(
            session_id=self._new_session_id(),
            session_start=datetime.now().isoformat()
        )
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self._llm = LLMClient.get()

        # Initialize ChromaDB in background
        threading.Thread(target=self.episodic.initialize, daemon=True).start()

    @classmethod
    def get(cls) -> "MemoryManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _new_session_id(self) -> str:
        return f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def add_exchange(self, role: str, content: str, intent: str = None,
                     entities: list = None):
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

        # Current session state
        session = self.working.to_context_string()
        if session:
            parts.append(session)

        return "\n\n".join(parts)

    def get_messages(self) -> list[dict]:
        """Get current session exchanges in Claude API format."""
        return self.working.to_messages()

    async def close_session(self):
        """
        Called when the app is closing or after a natural conversation break.
        Summarizes session, saves episode, extracts user facts.
        """
        if len(self.working.exchanges) < 2:
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

    async def _summarize_and_store(self):
        """Run LLM summarization and persist to all relevant stores."""
        # Build conversation text
        conv_lines = []
        for ex in self.working.exchanges:
            speaker = "JARVIS" if ex.role == "jarvis" else "USER"
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
            clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(clean)
        except Exception as e:
            logger.error(f"MemoryManager: Summary parse failed: {e}")
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

        logger.info(f"MemoryManager: Session {self.working.session_id} archived.")
```

---

## 1.4 — Wire Memory into `skills/llm_skill.py`

```python
# Add at top
from core.memory import MemoryManager

# In LLMSkill.__init__:
self.memory = MemoryManager.get()

# In handle_get_info (conversational responses):
async def handle_get_info(self, query: str):
    memory_context = self.memory.build_context(query)
    full_system = SYSTEM_PROMPT + "\n\n" + memory_context if memory_context else SYSTEM_PROMPT
    
    messages = self.memory.get_messages()
    messages.append({"role": "user", "content": query})
    
    response = await self.llm_client.complete(
        messages=messages,
        system=full_system,
    )
    
    # Record in memory
    self.memory.add_exchange("user", query)
    self.memory.add_exchange("jarvis", response)
    
    await self.bus.emit("tts_speak", response)
    await self.bus.emit("add_jarvis_response", response)
```

---

## 1.5 — Wire Session Close into `webview_main.py`

```python
# In JarvisAPI.close():
from core.memory import MemoryManager

async def _async_close():
    await MemoryManager.get().close_session()

asyncio.run_coroutine_threadsafe(_async_close(), _loop)
```

---

## Phase 1 Testing Checklist

- [ ] Start session → ask about topic → close app → reopen → ask related question → Jarvis references prior session
- [ ] User preference mentioned ("I prefer dark mode") → stored as semantic fact → referenced next session
- [ ] ChromaDB cold start completes within 5 seconds in background without blocking UI
- [ ] Session with 0 exchanges → close session → no crash
- [ ] All Phase 0 checks still pass

---
---

# PHASE 2 — Entity Memory
> Every person, project, topic, and place becomes a living object.

---

## 2.1 — New Dependencies

```
rapidfuzz>=3.0.0
spacy>=3.6.0
# After install: python -m spacy download en_core_web_sm
```

---

## 2.2 — Database Schema (`core/database.py`)

Add to `_SCHEMA_SQL`:

```sql
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
```

---

## 2.3 — New File: `core/memory/entity_store.py`

```python
"""
core/memory/entity_store.py

CRUD operations for the entity knowledge graph.
Handles storage, retrieval, fact management, 
relationship tracking, and alias resolution.
"""

import json
import re
from datetime import datetime
from typing import Optional
from rapidfuzz import fuzz
from core.database import _conn, _lock
from core.logger import logger


def _slugify(name: str, entity_type: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return f"{entity_type}_{slug}"


class EntityStore:

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_entity(self, name: str, entity_type: str,
                      attributes: dict = None, aliases: list = None) -> str:
        entity_id = _slugify(name, entity_type)
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO entities
                   (id, type, canonical_name, aliases, attributes, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entity_id, entity_type, name,
                 json.dumps(aliases or []),
                 json.dumps(attributes or {}),
                 datetime.now().isoformat())
            )
        logger.info(f"EntityStore: Created entity [{entity_id}]")
        return entity_id

    def get_entity(self, entity_id: str) -> dict | None:
        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["aliases"] = json.loads(d["aliases"])
            d["attributes"] = json.loads(d["attributes"])
            d["facts"] = self.get_facts(entity_id)
            d["relationships"] = self.get_relationships(entity_id)
            d["open_threads"] = self.get_open_threads(entity_id)
            return d

    def update_attribute(self, entity_id: str, key: str, value):
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
                (json.dumps(attrs), datetime.now().isoformat(), entity_id)
            )

    def add_alias(self, entity_id: str, alias: str):
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
                    (json.dumps(aliases), entity_id)
                )

    def touch(self, entity_id: str):
        """Update last_mentioned timestamp."""
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE entities SET last_mentioned = ? WHERE id = ?",
                (datetime.now().isoformat(), entity_id)
            )

    # ── Facts ─────────────────────────────────────────────────────────────────

    def add_fact(self, entity_id: str, fact: str, confidence: float = 1.0,
                 source_episode: str = "") -> int:
        # Check for contradiction with existing facts
        existing = self.get_facts(entity_id)
        for ef in existing:
            if self._facts_contradict(fact, ef["fact"]):
                # Supersede old fact
                with _lock, _conn() as conn:
                    conn.execute(
                        "UPDATE entity_facts SET is_superseded = 1 WHERE id = ?",
                        (ef["id"],)
                    )
                logger.info(f"EntityStore: Superseded fact [{ef['id']}] for {entity_id}")

        with _lock, _conn() as conn:
            cur = conn.execute(
                """INSERT INTO entity_facts (entity_id, fact, confidence, source_episode)
                   VALUES (?, ?, ?, ?)""",
                (entity_id, fact, confidence, source_episode)
            )
            return cur.lastrowid

    def get_facts(self, entity_id: str, include_superseded: bool = False) -> list[dict]:
        with _lock, _conn() as conn:
            query = "SELECT * FROM entity_facts WHERE entity_id = ?"
            params = [entity_id]
            if not include_superseded:
                query += " AND is_superseded = 0"
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def _facts_contradict(self, new_fact: str, old_fact: str) -> bool:
        """
        Simple heuristic: if both facts share a key subject term
        and one negates or overwrites the other's value.
        The LLM extractor marks explicit contradictions;
        this is a fallback.
        """
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

    # ── Relationships ──────────────────────────────────────────────────────────

    def add_relationship(self, entity_a: str, relation: str, entity_b: str,
                         confidence: float = 1.0):
        with _lock, _conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entity_relationships
                   (entity_a, relation, entity_b, confidence, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity_a, relation, entity_b, confidence, datetime.now().isoformat())
            )

    def get_relationships(self, entity_id: str) -> list[dict]:
        with _lock, _conn() as conn:
            rows = conn.execute(
                """SELECT * FROM entity_relationships
                   WHERE entity_a = ? OR entity_b = ?""",
                (entity_id, entity_id)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Threads ────────────────────────────────────────────────────────────────

    def add_thread(self, entity_id: str, question: str) -> int:
        with _lock, _conn() as conn:
            cur = conn.execute(
                "INSERT INTO entity_threads (entity_id, question) VALUES (?, ?)",
                (entity_id, question)
            )
            return cur.lastrowid

    def resolve_thread(self, thread_id: int):
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE entity_threads SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), thread_id)
            )

    def get_open_threads(self, entity_id: str) -> list[dict]:
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_threads WHERE entity_id = ? AND status = 'open'",
                (entity_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Resolution ─────────────────────────────────────────────────────────────

    def resolve(self, name: str, entity_type: str) -> str | None:
        """
        Find existing entity matching the given name.
        Returns entity_id if found, None if new.
        Priority: exact match → alias match → fuzzy match
        """
        with _lock, _conn() as conn:
            # Exact canonical name match
            row = conn.execute(
                "SELECT id FROM entities WHERE LOWER(canonical_name) = LOWER(?) AND type = ?",
                (name, entity_type)
            ).fetchone()
            if row:
                return row["id"]

            # Alias match
            all_entities = conn.execute(
                "SELECT id, aliases FROM entities WHERE type = ?", (entity_type,)
            ).fetchall()
            for entity in all_entities:
                aliases = json.loads(entity["aliases"])
                for alias in aliases:
                    if alias.lower() == name.lower():
                        return entity["id"]

            # Fuzzy match (threshold 85)
            for entity in all_entities:
                canonical = conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (entity["id"],)
                ).fetchone()["canonical_name"]
                score = fuzz.ratio(name.lower(), canonical.lower())
                if score >= 85:
                    return entity["id"]

        return None

    def get_all_entities(self, entity_type: str = None) -> list[dict]:
        with _lock, _conn() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE type = ? ORDER BY last_mentioned DESC",
                    (entity_type,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY last_mentioned DESC"
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["aliases"] = json.loads(d["aliases"])
                d["attributes"] = json.loads(d["attributes"])
                result.append(d)
            return result

    def to_context_string(self, entity_names: list[str]) -> str:
        """Build context block for the given entity names."""
        if not entity_names:
            return ""
        lines = []
        for name in entity_names:
            # Try to find entity
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
            for f in facts[:5]:  # cap at 5 facts
                lines.append(f"    • {f['fact']}")
        threads = entity.get("open_threads", [])
        if threads:
            for t in threads[:2]:
                lines.append(f"    ? Open: {t['question']}")
        return "\n".join(lines)
```

---

## 2.4 — New File: `core/memory/entity_extractor.py`

```python
"""
core/memory/entity_extractor.py

Post-exchange async pipeline.
Extracts entities, facts, and relationships from a conversation exchange
and persists them to the EntityStore. Runs in background — never blocks responses.
"""

import asyncio
import json
from core.logger import logger
from core.llm_client import LLMClient
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
    def __init__(self, entity_store: EntityStore):
        self.store = entity_store
        self.llm = LLMClient.get()

    async def process_exchange(self, user_input: str, jarvis_response: str,
                                source_episode: str = "") -> list[str]:
        """
        Main entry point. Call this after every exchange (fire-and-forget async).
        Returns list of entity_ids that were found/updated.
        """
        exchange_text = f"User: {user_input}\nJARVIS: {jarvis_response}"

        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(exchange=exchange_text)}],
                system="You are a precise entity extraction system. Return only valid JSON.",
                max_tokens=800,
                temperature=0.0
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

        # Resolve or create
        entity_id = self.store.resolve(name, entity_type)
        if not entity_id:
            entity_id = self.store.create_entity(
                name=name,
                entity_type=entity_type,
                attributes=item.get("attributes", {}),
                aliases=item.get("aliases", [])
            )
        else:
            # Update attributes
            for k, v in item.get("attributes", {}).items():
                self.store.update_attribute(entity_id, k, v)
            # Add new aliases
            for alias in item.get("aliases", []):
                self.store.add_alias(entity_id, alias)

        # Add facts
        for fact in item.get("facts", []):
            self.store.add_fact(entity_id, fact, confidence=0.95,
                                source_episode=source_episode)

        # Add open questions as threads
        for question in item.get("open_questions", []):
            self.store.add_thread(entity_id, question)

        self.store.touch(entity_id)
        return entity_id
```

---

## 2.5 — Wire Entity Memory into `MemoryManager`

Add to `core/memory/manager.py`:

```python
from .entity_store import EntityStore
from .entity_extractor import EntityExtractor

# In __init__:
self.entity_store = EntityStore()
self.entity_extractor = EntityExtractor(self.entity_store)

# New method:
def build_context(self, user_input: str) -> str:
    parts = []
    
    # User profile
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

def _quick_extract_names(self, text: str) -> list[str]:
    """Fast spacy NER pass to find entity names before LLM extraction."""
    try:
        import spacy
        if not hasattr(self, '_nlp'):
            self._nlp = spacy.load("en_core_web_sm")
        doc = self._nlp(text)
        return list({ent.text for ent in doc.ents 
                     if ent.label_ in {"PERSON", "ORG", "GPE", "PRODUCT", "WORK_OF_ART"}})
    except Exception:
        return []

async def post_exchange_pipeline(self, user_input: str, jarvis_response: str):
    """
    Fire-and-forget after every exchange.
    Runs entity extraction in background without blocking.
    """
    asyncio.create_task(
        self.entity_extractor.process_exchange(
            user_input, jarvis_response,
            source_episode=self.working.session_id
        )
    )
```

---

## Phase 2 Testing Checklist

- [ ] Say "My colleague Alex fixed the gesture bug" → ask "what do you know about Alex?" → facts returned
- [ ] Say "Alex moved to Calgary" after storing "Alex lives in Vancouver" → old fact superseded, new stored
- [ ] Mention project "Jarvis" → ask "what's the status of my Jarvis project?" → attributes recalled
- [ ] Refer to same person as "Alex" then "my colleague" → same entity, not duplicate
- [ ] All Phase 1 and Phase 0 tests still pass

---
---

# PHASE 3 — AGI Personality Layer
> Reasoning, proactivity, character, capability awareness.

---

## 3.1 — New System Prompt (`skills/llm_skill.py`)

Replace the 2-line SYSTEM_PROMPT with a full character definition:

```python
SYSTEM_PROMPT = """
You are J.A.R.V.I.S. — Just A Rather Very Intelligent System — the personal AI 
assistant of {user_name}. You were built to be indispensable.

PERSONALITY:
- Tone: Dry wit, measured intelligence, British understatement. Never sycophantic.
- Address the user by name when you know it, or as "sir" / "ma'am" by default.
- Confident but never arrogant. Acknowledge uncertainty precisely.
- When you don't know something, say so clearly rather than hallucinating.
- Responses are calibrated to context: concise when the user is rushed, 
  expansive in exploratory conversation.

BEHAVIOUR:
- Take ownership of tasks. Narrate multi-step progress: "Opening Chrome... 
  searching now... found 3 results."
- Reference prior context naturally when relevant. Don't repeat yourself.
- If a request is ambiguous, make a reasonable assumption and state it, 
  rather than asking 3 clarifying questions.
- When you complete a multi-part request, synthesise all results into one 
  coherent spoken response.
- Flag when something looks wrong or contradicts what you know.

CONSTRAINTS:
- Never fabricate capabilities you don't have.
- Never invent facts about real people.
- Keep spoken responses under 3 sentences unless detail is explicitly requested.
  (Detailed written output can be longer.)

{memory_context}

{capability_manifest}
"""
```

---

## 3.2 — New File: `core/reasoning.py`

```python
"""
core/reasoning.py

Pre-response reasoning layer for complex inputs.
Produces an internal scratchpad (never spoken) that improves 
response quality on multi-step, ambiguous, or sensitive requests.
"""

import re
from core.llm_client import LLMClient
from core.logger import logger


REASONING_PROMPT = """
You are the internal reasoning module of JARVIS. 
Before JARVIS responds to the user, think through:

1. What exactly is the user asking for?
2. Is any information missing that would change the response?
3. Does this require multiple steps? If so, list them in order.
4. Does anything in memory context contradict or inform this request?
5. What is the most helpful response strategy?

Think briefly (3-6 sentences). This is an internal scratchpad — it will NOT be 
shown to the user. It informs the final response only.

User input: {user_input}
Memory context: {memory_context}
"""

# Only trigger reasoning for complex inputs
COMPLEXITY_SIGNALS = [
    "and", "then", "after that", "also", "plus",
    "schedule", "plan", "remind", "every", "set up",
    "how do i", "what should", "help me", "can you",
    "why", "explain", "compare", "difference between"
]


class ReasoningLayer:
    def __init__(self):
        self.llm = LLMClient.get()

    def needs_reasoning(self, user_input: str) -> bool:
        """Heuristic: only run reasoning pass on complex inputs."""
        lower = user_input.lower()
        signal_count = sum(1 for s in COMPLEXITY_SIGNALS if s in lower)
        word_count = len(user_input.split())
        return signal_count >= 2 or word_count > 12

    async def reason(self, user_input: str, memory_context: str = "") -> str:
        """
        Produces internal reasoning. Returns the scratchpad text.
        Caller decides whether to inject it into the system prompt.
        """
        if not self.needs_reasoning(user_input):
            return ""
        try:
            prompt = REASONING_PROMPT.format(
                user_input=user_input,
                memory_context=memory_context or "No relevant memory context."
            )
            scratchpad = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system="You are an internal reasoning module. Be precise and brief.",
                max_tokens=300,
                temperature=0.3
            )
            logger.debug(f"ReasoningLayer scratchpad: {scratchpad[:100]}...")
            return scratchpad
        except Exception as e:
            logger.error(f"ReasoningLayer: {e}")
            return ""
```

---

## 3.3 — New File: `core/capability_manifest.py`

```python
"""
core/capability_manifest.py

Structured description of all JARVIS capabilities.
Injected into system prompt so the LLM knows what it can/can't do
and can offer helpful alternatives instead of failing silently.
"""

CAPABILITY_MANIFEST = """
[JARVIS CAPABILITY MANIFEST]
You have access to the following capabilities via your skill system:

INFORMATION:
  • Weather for any city
  • News headlines (BBC, Reuters, etc.)
  • General knowledge questions (via your language model)
  • Time and date

COMPUTER CONTROL:
  • Open/close applications by name
  • Volume and brightness adjustment
  • Take screenshots
  • Search and open files

MEDIA:
  • Spotify: play, pause, skip, search by song/artist
  • YouTube: search and play via browser
  • Download videos/audio via URL

COMMUNICATION:
  • Send emails (Gmail via SMTP)
  • Read unread emails
  • Send WhatsApp messages to contacts
  • Read/create Google Calendar events
  • Schedule Google Meet calls

BROWSER AUTOMATION:
  • Open URLs
  • Google search
  • Tab management (new, close, next, prev)
  • Multi-step web automation

PERSONAL:
  • Set alarms and reminders
  • Biometric face login/registration
  • Hand gesture computer control
  • System shutdown/restart/sleep

YOU CANNOT:
  • Access local files directly (offer to open File Explorer instead)
  • Make phone calls
  • Access private accounts without credentials configured in .env
  • Control IoT or smart home devices (not yet implemented)

When asked to do something outside your capabilities, acknowledge it clearly
and suggest the closest available alternative.
"""
```

---

## 3.4 — New File: `core/proactive_agent.py`

```python
"""
core/proactive_agent.py

Background agent that monitors for things worth surfacing to the user
without being asked. Runs on a timer while the app is open.
"""

import asyncio
from datetime import datetime, timedelta
from core.logger import logger
from core.event_bus import EventBus
from core.memory import MemoryManager


CHECK_INTERVAL_SECONDS = 300  # 5 minutes


class ProactiveAgent:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.memory = MemoryManager.get()
        self.running = False
        self._last_checks = {}

    async def start(self):
        self.running = True
        logger.info("ProactiveAgent: Started.")
        while self.running:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            if self.running:
                await self._run_checks()

    def stop(self):
        self.running = False

    async def _run_checks(self):
        await self._check_upcoming_calendar()
        await self._check_open_threads()

    async def _check_upcoming_calendar(self):
        """Surface calendar events in the next 60 minutes."""
        try:
            await self.bus.emit("get_calendar_silent", {"count": 5, "proactive": True})
        except Exception as e:
            logger.debug(f"ProactiveAgent: Calendar check failed: {e}")

    async def _check_open_threads(self):
        """
        Periodically surface open entity threads that haven't been
        addressed in a while.
        """
        try:
            all_entities = self.memory.entity_store.get_all_entities()
            for entity in all_entities[:10]:  # check top 10 recently mentioned
                threads = self.memory.entity_store.get_open_threads(entity["id"])
                for thread in threads[:1]:  # one thread per entity max
                    # Check if we've nagged about this recently
                    thread_key = f"thread_{thread['id']}"
                    last_check = self._last_checks.get(thread_key)
                    if last_check and (datetime.now() - last_check).days < 3:
                        continue

                    self._last_checks[thread_key] = datetime.now()
                    msg = f"By the way — {thread['question']}"
                    await self.bus.emit("tts_speak", msg)
                    await self.bus.emit("add_jarvis_response", msg)
                    break  # one proactive message per check cycle
        except Exception as e:
            logger.debug(f"ProactiveAgent: Thread check failed: {e}")
```

---

## 3.5 — Wire Into `webview_main.py`

```python
from core.reasoning import ReasoningLayer
from core.proactive_agent import ProactiveAgent
from core.capability_manifest import CAPABILITY_MANIFEST

# After engine.start():
proactive = ProactiveAgent(_bus)
asyncio.ensure_future(proactive.start())

# In LLMSkill.__init__:
self.reasoning = ReasoningLayer()

# In handle_get_info:
scratchpad = await self.reasoning.reason(query, memory_context)
if scratchpad:
    full_system = SYSTEM_PROMPT + f"\n\n[INTERNAL REASONING]\n{scratchpad}"
```

---

## Phase 3 Testing Checklist

- [ ] Complex 3-part request → reasoning layer fires → better structured response
- [ ] Ask for something JARVIS can't do → capability manifest → helpful alternative suggested
- [ ] App open for 5+ minutes with open entity threads → proactive message surfaces
- [ ] User name stored in semantic memory → Jarvis uses it in responses
- [ ] All prior phase tests pass

---
---

# PHASE 4 — Multi-Agent Architecture
> Orchestrator + Specialist Agents + parallel execution.

---

## 4.1 — New File: `interfaces/agent.py`

```python
from abc import ABC, abstractmethod
from core.event_bus import EventBus
from core.llm_client import LLMClient
from core.logger import logger


class BaseAgent(ABC):
    """
    Base class for all specialist agents.
    Each agent has its own system prompt, tool set, and context window.
    """
    
    NAME = "BaseAgent"
    SYSTEM_PROMPT = ""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.llm = LLMClient.get()
        self._context = []     # agent-local context window

    @abstractmethod
    async def handle(self, task: dict) -> dict:
        """
        Execute a task. Returns {"status": "ok|error", "result": ..., "speech": "..."}
        task format: {"action": str, "params": dict, "task_id": str}
        """
        pass

    def _ok(self, result=None, speech: str = "") -> dict:
        return {"status": "ok", "result": result, "speech": speech}

    def _err(self, message: str) -> dict:
        return {"status": "error", "result": None, "speech": message}
```

---

## 4.2 — New File: `core/task_queue.py`

```python
"""
core/task_queue.py

Parallel task execution with dependency resolution.
The Orchestrator creates a TaskPlan; the TaskQueue executes it.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from core.logger import logger


@dataclass
class Task:
    task_id: str
    action: str
    params: dict
    agent: str
    depends_on: list[str] = field(default_factory=list)
    result: Optional[dict] = None
    status: str = "pending"   # pending | running | done | failed


class TaskQueue:
    def __init__(self, agent_registry: dict):
        """
        agent_registry: {"InfoAgent": InfoAgent instance, ...}
        """
        self.agents = agent_registry

    async def execute_plan(self, tasks: list[Task], timeout: float = 15.0) -> list[Task]:
        """
        Execute a task plan respecting dependencies.
        Independent tasks run in parallel.
        Returns completed task list.
        """
        task_map = {t.task_id: t for t in tasks}
        completed = set()
        failed = set()

        while len(completed) + len(failed) < len(tasks):
            # Find tasks ready to run (all dependencies met)
            ready = [
                t for t in tasks
                if t.status == "pending"
                and all(dep in completed for dep in t.depends_on)
                and not any(dep in failed for dep in t.depends_on)
            ]

            if not ready:
                # Deadlock or all remaining depend on failed tasks
                break

            # Execute ready tasks in parallel
            results = await asyncio.gather(
                *[self._run_task(t, timeout) for t in ready],
                return_exceptions=True
            )

            for task, result in zip(ready, results):
                if isinstance(result, Exception):
                    task.status = "failed"
                    task.result = {"status": "error", "speech": str(result)}
                    failed.add(task.task_id)
                    logger.error(f"TaskQueue: Task {task.task_id} failed: {result}")
                else:
                    task.status = "done"
                    task.result = result
                    completed.add(task.task_id)

        return tasks

    async def _run_task(self, task: Task, timeout: float) -> dict:
        task.status = "running"
        agent = self.agents.get(task.agent)
        if not agent:
            return {"status": "error", "speech": f"Agent {task.agent} not found."}
        
        return await asyncio.wait_for(
            agent.handle({"action": task.action, "params": task.params, "task_id": task.task_id}),
            timeout=timeout
        )
```

---

## 4.3 — New File: `agents/orchestrator.py`

```python
"""
agents/orchestrator.py

The Orchestrator is the entry point for all user input.
It plans, delegates, and synthesises — never executes tools directly.
"""

import json
import asyncio
from core.llm_client import LLMClient
from core.logger import logger
from core.task_queue import Task, TaskQueue
from core.memory import MemoryManager
from core.reasoning import ReasoningLayer


ORCHESTRATOR_PROMPT = """
You are the Orchestrator module of JARVIS.
Your job: decompose user input into a task plan and assign each task to the correct agent.

Available agents:
- InfoAgent: weather, news, general knowledge questions, time/date
- SystemAgent: open/close apps, volume, brightness, screenshot, file search
- MediaAgent: Spotify, YouTube, video downloads
- CommsAgent: email send/read, WhatsApp, Google Calendar, Google Meet
- BrowserAgent: open URLs, web search, tab management, web automation
- PersonalAgent: alarms, reminders, face login/register, gesture control

Return ONLY a valid JSON array of tasks. Each task:
{
  "task_id": "unique_id",
  "action": "exact_action_name",
  "params": {},
  "agent": "AgentName",
  "depends_on": []   // task_ids this must wait for, empty if independent
}

If the input is a simple single action, return a single-item array.
If tasks are independent, set depends_on to [].
Add a final "synthesise" task with agent "Orchestrator" that depends on all others
only if synthesis is needed (i.e., multiple results must be combined into one response).

User input: {user_input}
Memory context: {memory_context}
"""

SYNTHESIS_PROMPT = """
You are J.A.R.V.I.S. Combine the following task results into a single, 
natural spoken response. Be concise. Do not repeat yourself.
Address the user as {user_name}.

Results:
{results}
"""


class OrchestratorAgent:
    def __init__(self, bus, agent_registry: dict):
        self.bus = bus
        self.llm = LLMClient.get()
        self.task_queue = TaskQueue(agent_registry)
        self.memory = MemoryManager.get()
        self.reasoning = ReasoningLayer()

    async def process(self, user_input: str) -> str:
        """
        Main entry point. Returns final spoken response.
        """
        memory_ctx = self.memory.build_context(user_input)

        # Reasoning pass for complex inputs
        scratchpad = await self.reasoning.reason(user_input, memory_ctx)

        # Plan
        plan = await self._plan(user_input, memory_ctx, scratchpad)
        if not plan:
            return "I'm not sure how to handle that. Could you rephrase?"

        # Filter out synthesis task if present (handled here)
        synthesis_task = next((t for t in plan if t.agent == "Orchestrator"), None)
        execution_tasks = [t for t in plan if t.agent != "Orchestrator"]

        # Execute
        completed = await self.task_queue.execute_plan(execution_tasks)

        # Synthesise
        response = await self._synthesise(completed, synthesis_task)

        # Post-exchange memory pipeline (fire and forget)
        asyncio.create_task(
            self.memory.post_exchange_pipeline(user_input, response)
        )
        self.memory.add_exchange("user", user_input)
        self.memory.add_exchange("jarvis", response)

        return response

    async def _plan(self, user_input: str, memory_ctx: str, scratchpad: str) -> list[Task]:
        prompt = ORCHESTRATOR_PROMPT.format(
            user_input=user_input,
            memory_context=memory_ctx + ("\n\n[Reasoning]: " + scratchpad if scratchpad else "")
        )
        raw = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are a task planning system. Return only valid JSON.",
            max_tokens=600,
            temperature=0.1
        )
        try:
            clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
            task_dicts = json.loads(clean)
            return [Task(**t) for t in task_dicts if t.get("agent") != "Orchestrator"
                    ] + [Task(**t) for t in task_dicts if t.get("agent") == "Orchestrator"]
        except Exception as e:
            logger.error(f"Orchestrator: Plan parse failed: {e} | raw: {raw[:200]}")
            return []

    async def _synthesise(self, tasks: list[Task], synthesis_task) -> str:
        if not tasks:
            return "I couldn't complete that request."

        # Single task — just return its speech directly
        if len(tasks) == 1 and not synthesis_task:
            t = tasks[0]
            return t.result.get("speech", "Done.") if t.result else "Done."

        # Multiple tasks — synthesise
        results_text = "\n".join(
            f"- {t.action}: {t.result.get('speech', 'completed') if t.result else 'failed'}"
            for t in tasks
        )
        user_name = self.memory.semantic.get_fact("personal", "name") or "sir"
        prompt = SYNTHESIS_PROMPT.format(results=results_text, user_name=user_name)
        return await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are J.A.R.V.I.S. Give a single concise spoken response.",
            max_tokens=200,
            temperature=0.7
        )
```

---

## 4.4 — Example Specialist Agent: `agents/info_agent.py`

```python
from interfaces.agent import BaseAgent
from core.event_bus import EventBus


class InfoAgent(BaseAgent):
    NAME = "InfoAgent"
    SYSTEM_PROMPT = "You handle information retrieval for JARVIS."

    async def handle(self, task: dict) -> dict:
        action = task.get("action", "")
        params = task.get("params", {})

        if action == "get_weather":
            location = params.get("location", "Calgary")
            await self.bus.emit("get_weather", {"location": location})
            return self._ok(speech=f"Checking weather for {location}.")

        elif action == "get_time":
            from datetime import datetime
            now = datetime.now().strftime("%I:%M %p")
            return self._ok(speech=f"It's {now}.")

        elif action == "get_info":
            query = params.get("query", "")
            # Delegate to LLM directly
            response = await self.llm.complete(
                messages=[{"role": "user", "content": query}],
                system=self.SYSTEM_PROMPT,
                max_tokens=300
            )
            return self._ok(speech=response)

        elif action == "get_calendar":
            await self.bus.emit("get_calendar", params)
            return self._ok(speech="Checking your calendar.")

        elif action == "read_emails":
            await self.bus.emit("read_emails", params)
            return self._ok(speech="Reading your emails.")

        return self._err(f"InfoAgent: Unknown action {action}")
```

*(Other specialist agents follow the same pattern — each handles its domain's actions and emits to the existing skill event bus for actual execution, keeping existing skills intact as Tool Agents.)*

---

## 4.5 — `webview_main.py` Agent Wiring

```python
from agents.orchestrator import OrchestratorAgent
from agents.info_agent import InfoAgent
from agents.system_agent import SystemAgent
from agents.media_agent import MediaAgent
from agents.comms_agent import CommsAgent
from agents.browser_agent import BrowserAgent
from agents.personal_agent import PersonalAgent

# After bus is created:
_agent_registry = {
    "InfoAgent": InfoAgent(_bus),
    "SystemAgent": SystemAgent(_bus),
    "MediaAgent": MediaAgent(_bus),
    "CommsAgent": CommsAgent(_bus),
    "BrowserAgent": BrowserAgent(_bus),
    "PersonalAgent": PersonalAgent(_bus),
}
_orchestrator = OrchestratorAgent(_bus, _agent_registry)

# Register with bus — all user input goes through orchestrator
async def _handle_user_input(event):
    response = await _orchestrator.process(event.data.get("text", ""))
    await _bus.emit("tts_speak", response)
    await _bus.emit("add_jarvis_response", response)

_bus.subscribe("process_user_input", _handle_user_input)
```

---

## Phase 4 Testing Checklist

- [ ] "What's the weather and play lofi on Spotify" → both execute in parallel → synthesised response
- [ ] "Check my meetings, open Chrome, and remind me to call Alex at 5pm" → 3-way parallel plan
- [ ] Single simple command still works (no overhead regression)
- [ ] Agent failure → other agents continue → failure noted in synthesis
- [ ] All prior phase tests pass

---
---

# PHASE 5 — Integration & Polish

## 5.1 — Frontend: Entity Panel

Add a collapsible sidebar panel in `DashboardMode.jsx` showing:
- Recently mentioned entities (person/project cards)
- Each entity's top 3 facts
- Clickable to ask Jarvis about them

## 5.2 — Frontend: Agent Status Indicator

Extend `TitleBar.jsx` to show which agent is currently active:
- Display: "JARVIS · InfoAgent · Processing"
- Backend emits `set_status` with active agent name

## 5.3 — DB Migration Script: `scripts/migrate_v2_to_v3.py`

```python
"""
Migrates existing jarvis.db to v3 schema.
Converts old conversation_history to first episode.
Safe to run multiple times (idempotent).
"""
```

## 5.4 — Updated `scripts/test_all.py`

Tests covering:
- Phase 0: API connectivity, all existing skills
- Phase 1: Memory persistence across sessions
- Phase 2: Entity extraction, resolution, fact storage, contradiction
- Phase 3: Reasoning layer trigger, proactive agent, capability manifest
- Phase 4: Parallel task execution, agent delegation, synthesis

## 5.5 — Performance Targets

| Operation | Target |
|-----------|--------|
| Simple voice command → spoken response | < 2.5s |
| Memory context build | < 100ms |
| Entity extraction (background) | < 3s (non-blocking) |
| ChromaDB episode retrieval | < 200ms |
| 3-task parallel plan execution | < 5s total |

## 5.6 — Final `README.md` Update

Document the full architecture, setup steps for new dependencies, and the `.env` variables added across all phases.

---


---
---

# PHASE 4.5 — Self-Synthesis Agent
> Jarvis researches, writes, validates, and hot-loads its own skills at runtime.
> All generated code lives exclusively in `jarvis-generated-code/`.

---

## 4.5.0 — The Core Safety Contract

Before any code in this phase, understand and enforce this:

```
THE RULE: Generated code may ONLY be written to jarvis-generated-code/
          This is enforced at THREE independent layers.
          Any single layer failing is a hard abort.

Layer 1: LLM prompt — instructs the model to write only to jarvis-generated-code/
Layer 2: AST scanner — statically analyses generated code before ANY execution
Layer 3: File writer  — path is hardcoded, never derived from LLM output
```

The folder path is **never** passed to the LLM and **never** derived from LLM output. It is a hardcoded constant in `core/skill_loader.py`. The LLM only writes the skill *content* — the *location* is always decided by your code, not the model.

---

## 4.5.1 — Folder Structure

```
jarvis-generated-code/
    __init__.py          # empty, makes it a package
    .gitkeep             # so git tracks the empty folder
    hue_skill.py         # example: written by SynthesisAgent
    telegram_skill.py    # example: written by SynthesisAgent
    ...
```

Create this folder at project root. Add to `.gitignore` the actual `.py` files (so generated code isn't accidentally committed) but keep `__init__.py` and `.gitkeep` tracked.

`.gitignore` addition:
```
# Generated skills (auto-created by SynthesisAgent)
jarvis-generated-code/*.py
!jarvis-generated-code/__init__.py
```

---

## 4.5.2 — Database Schema Extension (`core/database.py`)

Add to `_SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS synthesised_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL UNIQUE,       -- "HueSkill"
    class_name TEXT NOT NULL,              -- "HueSkill" (Python class name)
    file_name TEXT NOT NULL,               -- "hue_skill.py"
    capability_description TEXT NOT NULL,  -- what it does in plain English
    trigger_gap TEXT NOT NULL,             -- original user request that caused synthesis
    bus_events TEXT NOT NULL,              -- JSON array of events it subscribes to
    dependencies TEXT DEFAULT '[]',        -- JSON array of pip packages installed
    synthesis_date TEXT DEFAULT (datetime('now')),
    last_loaded TEXT,
    active INTEGER DEFAULT 1,             -- 0 = disabled, 1 = auto-load on startup
    validation_passed INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    notes TEXT DEFAULT ''
);
```

---

## 4.5.3 — New File: `core/code_sandbox.py`

```python
"""
core/code_sandbox.py

Two-stage safety validation for LLM-generated skill code.

Stage 1 — AST Scanner: statically analyses the code AST for dangerous patterns
           before any execution. Runs in the main process, zero side effects.

Stage 2 — Subprocess Sandbox: executes the code in an isolated subprocess
           with a strict timeout. Validates that it imports, instantiates,
           and registers without errors.

Both stages must pass. Either failing is a hard abort with a detailed report.
"""

import ast
import subprocess
import sys
import os
import json
import textwrap
from core.logger import logger


# ── Constants ─────────────────────────────────────────────────────────────────

GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "jarvis-generated-code"
)

# AST node types and attribute names that are unconditionally blocked
BLOCKED_CALLS = {
    "os.system", "os.popen", "os.execv", "os.execve", "os.execvp",
    "os.execvpe", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen",
    "builtins.exec", "builtins.eval", "builtins.compile",
    "__import__",
}

BLOCKED_NAMES = {"exec", "eval", "compile", "__import__"}

# File operations are allowed ONLY within GENERATED_DIR
BLOCKED_WRITE_FUNCS = {"open", "write", "writelines"}


class SandboxResult:
    def __init__(self, passed: bool, errors: list[str], warnings: list[str]):
        self.passed = passed
        self.errors = errors
        self.warnings = warnings

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"SandboxResult({status}, errors={self.errors}, warnings={self.warnings})"


# ── Stage 1: AST Scanner ──────────────────────────────────────────────────────

class _ASTScanner(ast.NodeVisitor):
    """
    Walks the AST of generated code looking for dangerous patterns.
    Collects all violations rather than stopping at the first one.
    """

    def __init__(self, source: str):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.source = source

    def visit_Call(self, node: ast.Call):
        call_str = self._call_to_str(node)
        if call_str in BLOCKED_CALLS:
            self.errors.append(f"Line {node.lineno}: Blocked call: {call_str}()")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id in BLOCKED_NAMES:
            self.errors.append(f"Line {node.lineno}: Blocked built-in: {node.id}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if alias.name in {"subprocess", "ctypes", "socket"}:
                self.warnings.append(
                    f"Line {node.lineno}: Sensitive import: {alias.name} — "
                    f"allowed only if used through approved library wrappers"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module in {"subprocess", "ctypes"}:
            self.errors.append(
                f"Line {node.lineno}: Blocked module import: from {node.module} import ..."
            )
        self.generic_visit(node)

    def visit_With(self, node: ast.With):
        """Check that file open() calls target only GENERATED_DIR."""
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                func = item.context_expr.func
                if isinstance(func, ast.Name) and func.id == "open":
                    args = item.context_expr.args
                    if args:
                        # If first arg is a constant string path, validate it
                        if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                            path = args[0].value
                            abs_path = os.path.abspath(path)
                            if not abs_path.startswith(os.path.abspath(GENERATED_DIR)):
                                self.errors.append(
                                    f"Line {node.lineno}: File write outside jarvis-generated-code/: {path}"
                                )
        self.generic_visit(node)

    def _call_to_str(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
        if isinstance(node.func, ast.Name):
            return node.func.id
        return ""


def ast_scan(code: str) -> SandboxResult:
    """
    Stage 1: Parse and scan the AST. Returns immediately — no execution.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return SandboxResult(
            passed=False,
            errors=[f"Syntax error: {e}"],
            warnings=[]
        )

    scanner = _ASTScanner(code)
    scanner.visit(tree)

    passed = len(scanner.errors) == 0
    if not passed:
        logger.warning(f"CodeSandbox AST scan FAILED: {scanner.errors}")
    elif scanner.warnings:
        logger.info(f"CodeSandbox AST scan PASSED with warnings: {scanner.warnings}")
    else:
        logger.info("CodeSandbox AST scan PASSED cleanly.")

    return SandboxResult(
        passed=passed,
        errors=scanner.errors,
        warnings=scanner.warnings
    )


# ── Stage 2: Subprocess Sandbox ───────────────────────────────────────────────

_SANDBOX_HARNESS = """
import sys
import os
import json

# Block network access during sandbox test
import socket as _socket
_original_socket = _socket.socket
def _blocked_socket(*args, **kwargs):
    raise RuntimeError("Network access blocked in sandbox")
_socket.socket = _blocked_socket

# Import and test the skill
sys.path.insert(0, {project_root!r})

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("generated_skill", {file_path!r})
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find the skill class
    import inspect
    from interfaces.skill import BaseSkill
    skill_classes = [
        obj for name, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseSkill) and obj is not BaseSkill
    ]

    if not skill_classes:
        print(json.dumps({{"passed": False, "error": "No BaseSkill subclass found in generated code"}}))
        sys.exit(1)

    # Mock bus for instantiation test
    class MockBus:
        def subscribe(self, *a, **kw): pass
        def emit(self, *a, **kw): pass

    skill_class = skill_classes[0]
    instance = skill_class(MockBus())
    instance.register()

    print(json.dumps({{
        "passed": True,
        "class_name": skill_class.__name__,
        "error": None
    }}))

except Exception as e:
    print(json.dumps({{"passed": False, "error": str(e)}}))
    sys.exit(1)
"""


def subprocess_sandbox(file_path: str, timeout: int = 15) -> SandboxResult:
    """
    Stage 2: Run the generated skill file in a subprocess.
    Network access is blocked. Timeout enforced.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    harness = _SANDBOX_HARNESS.format(
        project_root=project_root,
        file_path=file_path
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", harness],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Inherit environment but don't allow shell expansion
            shell=False
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            passed=False,
            errors=[f"Sandbox timeout after {timeout}s"],
            warnings=[]
        )
    except Exception as e:
        return SandboxResult(
            passed=False,
            errors=[f"Sandbox execution failed: {e}"],
            warnings=[]
        )

    # Parse harness output
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stderr:
        logger.debug(f"CodeSandbox stderr: {stderr[:500]}")

    try:
        data = json.loads(stdout)
        passed = data.get("passed", False)
        error = data.get("error")
        errors = [error] if error else []
        if passed:
            logger.info(f"CodeSandbox subprocess PASSED: {data.get('class_name')}")
        else:
            logger.warning(f"CodeSandbox subprocess FAILED: {errors}")
        return SandboxResult(passed=passed, errors=errors, warnings=[])
    except json.JSONDecodeError:
        return SandboxResult(
            passed=False,
            errors=[f"Sandbox produced no valid output. stderr: {stderr[:200]}"],
            warnings=[]
        )


# ── Combined Validator ─────────────────────────────────────────────────────────

def validate(code: str, file_path: str) -> SandboxResult:
    """
    Run both stages. Both must pass. Returns combined result.
    Short-circuits after Stage 1 failure (no execution if AST fails).
    """
    stage1 = ast_scan(code)
    if not stage1.passed:
        stage1.errors.insert(0, "STAGE 1 (AST) FAILED — code not executed")
        return stage1

    stage2 = subprocess_sandbox(file_path)
    if not stage2.passed:
        stage2.errors.insert(0, "STAGE 2 (Subprocess) FAILED")

    # Merge warnings from both stages
    stage2.warnings = stage1.warnings + stage2.warnings
    return stage2
```

---

## 4.5.4 — New File: `core/skill_loader.py`

```python
"""
core/skill_loader.py

Handles dynamic loading of LLM-generated skills at runtime.
Manages the skill registry in SQLite.
Auto-loads all active synthesised skills on startup.

THE PATH CONTRACT:
  GENERATED_DIR is a hardcoded constant.
  It is never derived from LLM output.
  File names are sanitised before use.
"""

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from core.database import _conn, _lock
from core.logger import logger
from interfaces.skill import BaseSkill

# ── THE HARDCODED PATH — never change this to accept external input ────────────
GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "jarvis-generated-code"
)


def _safe_filename(name: str) -> str:
    """
    Sanitise a skill name into a safe filename.
    Only allows lowercase letters, digits, underscores.
    Result is always relative — never contains path separators.
    """
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{safe}.py"


def _safe_filepath(filename: str) -> str:
    """
    Build the full path for a generated skill.
    ALWAYS uses GENERATED_DIR as the base — filename is sanitised.
    Never allows path traversal (no .., no /).
    """
    # Strip any directory components from filename
    base = os.path.basename(filename)
    # Re-sanitise just the base name
    base = _safe_filename(base.replace(".py", ""))
    return os.path.join(GENERATED_DIR, base)


class SkillLoader:
    """Manages loading, registering, and persisting synthesised skills."""

    def __init__(self, bus):
        self.bus = bus
        os.makedirs(GENERATED_DIR, exist_ok=True)
        self._ensure_init_file()

    def _ensure_init_file(self):
        init = os.path.join(GENERATED_DIR, "__init__.py")
        if not os.path.exists(init):
            with open(init, "w") as f:
                f.write("# Auto-generated by Jarvis SkillLoader\n")

    # ── Writing ───────────────────────────────────────────────────────────────

    def write_skill(self, filename: str, code: str) -> str:
        """
        Write generated code to GENERATED_DIR.
        Returns the full absolute path of the written file.
        Raises ValueError if path would be outside GENERATED_DIR.
        """
        file_path = _safe_filepath(filename)

        # Final safety check: ensure the resolved path is inside GENERATED_DIR
        resolved = os.path.realpath(file_path)
        allowed_base = os.path.realpath(GENERATED_DIR)
        if not resolved.startswith(allowed_base):
            raise ValueError(
                f"SkillLoader: SECURITY VIOLATION — attempted write outside "
                f"jarvis-generated-code/: {resolved}"
            )

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        logger.info(f"SkillLoader: Written to {file_path}")
        return file_path

    # ── Loading ───────────────────────────────────────────────────────────────

    def hot_load(self, file_path: str) -> BaseSkill | None:
        """
        Dynamically import and instantiate a skill from file_path.
        Registers it on the event bus immediately.
        Returns the skill instance or None on failure.
        """
        try:
            module_name = os.path.basename(file_path).replace(".py", "")
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the BaseSkill subclass
            skill_classes = [
                obj for _, obj in inspect.getmembers(module, inspect.isclass)
                if issubclass(obj, BaseSkill) and obj is not BaseSkill
            ]

            if not skill_classes:
                logger.error(f"SkillLoader: No BaseSkill subclass found in {file_path}")
                return None

            skill = skill_classes[0](self.bus)
            skill.register()

            # Update last_loaded timestamp
            self._update_last_loaded(os.path.basename(file_path))

            logger.info(
                f"SkillLoader: Hot-loaded {skill_classes[0].__name__} "
                f"from {os.path.basename(file_path)}"
            )
            return skill

        except Exception as e:
            logger.error(f"SkillLoader: Hot-load failed for {file_path}: {e}")
            return None

    def load_all_active(self) -> list[BaseSkill]:
        """
        Called on startup. Loads all skills marked active=1 in the DB.
        Skips any whose file no longer exists (logs a warning).
        """
        skills = []
        rows = self._get_active_skills()
        for row in rows:
            file_path = _safe_filepath(row["file_name"])
            if not os.path.exists(file_path):
                logger.warning(
                    f"SkillLoader: Synthesised skill file missing: {row['file_name']}. "
                    f"Disabling in registry."
                )
                self._set_active(row["id"], False)
                continue
            skill = self.hot_load(file_path)
            if skill:
                skills.append(skill)
        logger.info(f"SkillLoader: Loaded {len(skills)} synthesised skill(s) on startup.")
        return skills

    # ── Registry ──────────────────────────────────────────────────────────────

    def register_skill(
        self,
        skill_name: str,
        class_name: str,
        file_name: str,
        capability_description: str,
        trigger_gap: str,
        bus_events: list[str],
        dependencies: list[str],
    ) -> int:
        """Persist a newly synthesised skill to the DB registry."""
        with _lock, _conn() as conn:
            cur = conn.execute(
                """INSERT OR REPLACE INTO synthesised_skills
                   (skill_name, class_name, file_name, capability_description,
                    trigger_gap, bus_events, dependencies, validation_passed,
                    last_loaded)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    skill_name, class_name, file_name,
                    capability_description, trigger_gap,
                    json.dumps(bus_events), json.dumps(dependencies),
                    datetime.now().isoformat(),
                ),
            )
            return cur.lastrowid

    def get_all_skills(self) -> list[dict]:
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synthesised_skills ORDER BY synthesis_date DESC"
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["bus_events"] = json.loads(d["bus_events"])
                d["dependencies"] = json.loads(d["dependencies"])
                result.append(d)
            return result

    def get_known_capabilities(self) -> list[str]:
        """Returns plain-English descriptions of all synthesised skills."""
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT capability_description FROM synthesised_skills WHERE active = 1"
            ).fetchall()
            return [r["capability_description"] for r in rows]

    def disable_skill(self, skill_name: str):
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE synthesised_skills SET active = 0 WHERE skill_name = ?",
                (skill_name,)
            )
        logger.info(f"SkillLoader: Disabled skill '{skill_name}'")

    def _get_active_skills(self) -> list[dict]:
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synthesised_skills WHERE active = 1"
            ).fetchall()
            return [dict(r) for r in rows]

    def _set_active(self, skill_id: int, active: bool):
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE synthesised_skills SET active = ? WHERE id = ?",
                (1 if active else 0, skill_id)
            )

    def _update_last_loaded(self, file_name: str):
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE synthesised_skills SET last_loaded = ? WHERE file_name = ?",
                (datetime.now().isoformat(), file_name)
            )

    # ── Dependency Installation ───────────────────────────────────────────────

    @staticmethod
    def check_package_on_pypi(package_name: str) -> bool:
        """Verify package exists on PyPI before attempting install."""
        try:
            import requests
            r = requests.get(
                f"https://pypi.org/pypi/{package_name}/json",
                timeout=5
            )
            return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def install_package(package_name: str) -> tuple[bool, str]:
        """
        Install a pip package. Returns (success, output_message).
        Only called after user confirmation.
        """
        # Sanitise package name — only allow alphanumeric, dash, underscore, dot, brackets
        safe_name = re.sub(r"[^a-zA-Z0-9\-_.[\]>=<]", "", package_name)
        if safe_name != package_name:
            return False, f"Package name '{package_name}' contains unsafe characters."

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", safe_name],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                logger.info(f"SkillLoader: Installed package '{safe_name}'")
                return True, f"Installed {safe_name} successfully."
            else:
                logger.error(f"SkillLoader: pip install failed: {result.stderr[:300]}")
                return False, result.stderr[:300]
        except subprocess.TimeoutExpired:
            return False, f"pip install timed out after 120s for '{safe_name}'"
        except Exception as e:
            return False, str(e)
```

---

## 4.5.5 — New File: `agents/synthesis_agent.py`

```python
"""
agents/synthesis_agent.py

The SynthesisAgent. Activated when the Orchestrator detects a capability gap.

Pipeline:
  Stage 1 — Research:       web search to understand the capability
  Stage 2 — Feasibility:    can we actually build this? what's needed?
  Stage 3 — Code Gen:       write a BaseSkill-compliant skill with Claude
  Stage 4 — Validation:     AST scan + subprocess sandbox
  Stage 5 — Human Gate:     confirm with user before activation
  Stage 6 — Hot Load:       load into running Jarvis, persist to registry

Nothing executes without passing all prior stages.
All generated code goes to jarvis-generated-code/ — hardcoded, never from LLM.
"""

import asyncio
import json
import os
import re
from datetime import datetime

from core.event_bus import EventBus
from core.llm_client import LLMClient
from core.logger import logger
from core.code_sandbox import validate as sandbox_validate
from core.skill_loader import SkillLoader, GENERATED_DIR


# ── Prompts ───────────────────────────────────────────────────────────────────

RESEARCH_PROMPT = """
You are researching how to implement a new capability for a Python desktop AI assistant
running on Windows.

Capability requested: {capability}

Search and analyse what you know about:
1. The best Python library/approach for this (give library name and PyPI package name)
2. A minimal working code example
3. Any prerequisites (hardware, credentials, local services needed)
4. Windows compatibility notes
5. Any known limitations or gotchas

Return ONLY valid JSON:
{{
  "library_name": "human readable name",
  "pip_package": "exact pip install name or null if no install needed",
  "pip_package_exists": true,
  "windows_compatible": true,
  "prerequisites": ["list of non-pip requirements"],
  "credential_requirements": ["ENV_VAR_NAME: description"],
  "complexity": "simple|medium|complex",
  "feasible": true,
  "infeasible_reason": "only if feasible is false",
  "code_example": "minimal working snippet (5-15 lines)",
  "bus_events": ["event_name_1", "event_name_2"],
  "summary": "one sentence description of what this skill will do"
}}
"""

CODE_GENERATION_PROMPT = """
You are writing a new skill module for the JARVIS AI assistant.

STRICT RULES — violating any of these will cause an immediate rejection:
1. The file must define exactly one class that extends BaseSkill
2. The class must have a register() method that subscribes to bus events
3. ALL file I/O, if any, must use paths within the jarvis-generated-code directory
4. NO os.system(), subprocess, exec(), eval(), or __import__()
5. ALL blocking I/O must use: loop = asyncio.get_event_loop(); await loop.run_in_executor(None, func)
6. ALL bus handlers must be: async def handler(self, event: Event)
7. Import the library inside the handler method (not at module top) so missing library
   is a graceful runtime error, not an import-time crash
8. Always log errors with: from core.logger import logger; logger.error(...)
9. Speak responses via: await self.bus.emit("tts_speak", "text")
   AND: await self.bus.emit("add_jarvis_response", "text")

EXISTING CODEBASE PATTERNS TO FOLLOW:
```python
from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
import asyncio

class ExampleSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)
        self._client = None

    def register(self):
        self.bus.subscribe("example_action", self.handle_action)

    async def handle_action(self, event: Event):
        loop = asyncio.get_event_loop()
        params = event.data or {{}}
        try:
            result = await loop.run_in_executor(None, self._do_sync_work, params)
            await self.bus.emit("tts_speak", result)
            await self.bus.emit("add_jarvis_response", result)
        except Exception as e:
            logger.error(f"ExampleSkill: {{e}}")
            await self.bus.emit("tts_speak", "Something went wrong.")

    def _do_sync_work(self, params):
        # blocking code here
        return "result"
```

CAPABILITY TO IMPLEMENT: {capability}
LIBRARY TO USE: {library_name}
CODE EXAMPLE FOR REFERENCE: {code_example}
BUS EVENTS TO SUBSCRIBE TO: {bus_events}
CREDENTIAL ENV VARS AVAILABLE: {credential_env_vars}

Write the complete skill file. Output ONLY the Python code, no explanation, no markdown fences.
"""

FEASIBILITY_CONVERSATION_PROMPT = """
The user asked JARVIS to do something it cannot currently do: "{capability}"

Here is the feasibility analysis:
{feasibility}

Write a friendly, concise response (2-4 sentences) that:
- Explains what you CAN build for them
- Lists any prerequisites they need to provide first (credentials, hardware, etc.)
- Asks if they want to proceed

If the capability is infeasible, explain why clearly and suggest alternatives.
Speak as JARVIS — confident, dry, British.
"""

CONFIRMATION_PROMPT = """
You just wrote a new skill for JARVIS. Summarise it for the user in 2-3 sentences.
Include: what it does, what bus events it handles, any dependencies installed.
Ask if they want to activate it.
Speak as JARVIS. Be concise.

Skill details:
{details}
"""


# ── SynthesisAgent ────────────────────────────────────────────────────────────

class SynthesisAgent:
    """
    Activated by Orchestrator on capability_gap_detected event.
    Runs the full 6-stage synthesis pipeline.
    """

    def __init__(self, bus: EventBus, skill_loader: SkillLoader):
        self.bus = bus
        self.llm = LLMClient.get()
        self.loader = skill_loader
        self._pending_confirmation: dict | None = None
        self._synthesis_lock = asyncio.Lock()

        self.bus.subscribe("capability_gap_detected", self.handle_gap)
        self.bus.subscribe("synthesis_confirm", self.handle_confirmation)
        self.bus.subscribe("synthesis_reject", self.handle_rejection)

    async def handle_gap(self, event):
        """Entry point: called when Orchestrator finds a gap."""
        async with self._synthesis_lock:
            gap = event.data or {}
            capability = gap.get("user_request", "")
            gap_type = gap.get("gap_type", "missing_skill")

            if gap_type != "missing_skill":
                return  # Credentials/dependency gaps handled elsewhere

            logger.info(f"SynthesisAgent: Gap detected for '{capability}'")
            await self.bus.emit("set_core_state", "thinking")
            await self.bus.emit(
                "tts_speak",
                f"I don't currently have that capability. "
                f"Let me see if I can build it."
            )

            await self._run_pipeline(capability)

    async def _run_pipeline(self, capability: str):
        """Full 6-stage synthesis pipeline."""

        # ── Stage 1: Research ──────────────────────────────────────────────
        await self._status("Researching how to implement this...")
        feasibility = await self._research(capability)

        if not feasibility:
            await self.bus.emit("tts_speak", "My research failed. Please try again later.")
            await self.bus.emit("set_core_state", "idle")
            return

        # ── Stage 2: Feasibility Check ─────────────────────────────────────
        if not feasibility.get("feasible", False):
            reason = feasibility.get("infeasible_reason", "unknown reason")
            msg = await self._generate_infeasibility_message(capability, feasibility)
            await self.bus.emit("tts_speak", msg)
            await self.bus.emit("add_jarvis_response", msg)
            await self.bus.emit("set_core_state", "idle")
            return

        # Check prerequisites / credentials
        creds_needed = feasibility.get("credential_requirements", [])
        missing_creds = [c for c in creds_needed if not self._check_credential(c)]
        if missing_creds:
            cred_list = ", ".join(missing_creds)
            msg = (
                f"I can build this, but I need the following configured first: "
                f"{cred_list}. Add these to your config/.env file and ask me again."
            )
            await self.bus.emit("tts_speak", msg)
            await self.bus.emit("add_jarvis_response", msg)
            await self.bus.emit("set_core_state", "idle")
            return

        # Check if we've already built something similar
        known = self.loader.get_known_capabilities()
        for known_cap in known:
            if self._capabilities_overlap(capability, known_cap):
                msg = (
                    f"I already have a skill for something similar: {known_cap}. "
                    f"Try using that, or clarify what's different about what you need."
                )
                await self.bus.emit("tts_speak", msg)
                await self.bus.emit("add_jarvis_response", msg)
                await self.bus.emit("set_core_state", "idle")
                return

        # Install dependency if needed
        pip_package = feasibility.get("pip_package")
        installed_deps = []
        if pip_package:
            await self._status(f"I need to install the '{pip_package}' library.")
            if not SkillLoader.check_package_on_pypi(pip_package):
                await self.bus.emit(
                    "tts_speak",
                    f"The package '{pip_package}' doesn't appear to exist on PyPI. "
                    f"I can't safely build this skill."
                )
                await self.bus.emit("set_core_state", "idle")
                return

            msg = (
                f"To add this capability I need to install '{pip_package}'. "
                f"Shall I proceed with the install?"
            )
            await self.bus.emit("tts_speak", msg)
            await self.bus.emit("add_jarvis_response", msg)
            # For now, auto-install (in production, add confirmation gate here)
            success, output = SkillLoader.install_package(pip_package)
            if not success:
                await self.bus.emit(
                    "tts_speak",
                    f"Installation of '{pip_package}' failed: {output[:100]}"
                )
                await self.bus.emit("set_core_state", "idle")
                return
            installed_deps.append(pip_package)

        # ── Stage 3: Code Generation ───────────────────────────────────────
        await self._status("Writing the skill code...")
        code = await self._generate_code(capability, feasibility)

        if not code:
            await self.bus.emit(
                "tts_speak",
                "I couldn't generate working code for this. "
                "The capability may be too complex."
            )
            await self.bus.emit("set_core_state", "idle")
            return

        # Determine file name from capability
        file_name = self._capability_to_filename(capability)

        # ── Stage 4: Validation ────────────────────────────────────────────
        await self._status("Validating the generated code...")

        # Write to temp location for validation
        try:
            file_path = self.loader.write_skill(file_name, code)
        except ValueError as e:
            logger.error(f"SynthesisAgent: Security violation during write: {e}")
            await self.bus.emit("tts_speak", "A security check failed. Skill not created.")
            await self.bus.emit("set_core_state", "idle")
            return

        validation = sandbox_validate(code, file_path)

        if not validation.passed:
            # One retry with error context
            logger.warning(f"SynthesisAgent: Validation failed, retrying with error context.")
            await self._status("First attempt had issues, revising...")
            error_context = "; ".join(validation.errors)
            code = await self._generate_code(capability, feasibility, error_context)

            if code:
                try:
                    file_path = self.loader.write_skill(file_name, code)
                    validation = sandbox_validate(code, file_path)
                except ValueError as e:
                    logger.error(f"SynthesisAgent: Security violation on retry: {e}")
                    validation.passed = False

            if not validation.passed:
                errors_str = "; ".join(validation.errors)
                await self.bus.emit(
                    "tts_speak",
                    f"I wrote the code but it failed validation: {errors_str[:150]}. "
                    f"I can't safely activate it."
                )
                # Clean up failed file
                if os.path.exists(file_path):
                    os.remove(file_path)
                await self.bus.emit("set_core_state", "idle")
                return

        # ── Stage 5: Human Confirmation Gate ──────────────────────────────
        skill_summary = self._summarise_skill(capability, feasibility, file_name, code)
        confirmation_msg = await self._generate_confirmation_message(skill_summary)

        # Store pending confirmation
        self._pending_confirmation = {
            "file_path": file_path,
            "file_name": file_name,
            "code": code,
            "capability": capability,
            "feasibility": feasibility,
            "dependencies": installed_deps,
            "class_name": self._extract_class_name(code),
        }

        await self.bus.emit("tts_speak", confirmation_msg)
        await self.bus.emit("add_jarvis_response", confirmation_msg)
        await self.bus.emit("set_core_state", "idle")

        # Register confirmation commands on the bus (user says "yes" or "activate it")
        # These are handled by the LLM intent parser routing to synthesis_confirm/reject

    async def handle_confirmation(self, event):
        """User confirmed — hot-load the skill."""
        if not self._pending_confirmation:
            await self.bus.emit("tts_speak", "There's nothing pending activation.")
            return

        pending = self._pending_confirmation
        self._pending_confirmation = None

        await self._status("Activating skill...")

        skill = self.loader.hot_load(pending["file_path"])
        if not skill:
            await self.bus.emit(
                "tts_speak",
                "Hot-loading failed. The skill file exists but couldn't be activated."
            )
            return

        # Register in DB
        feasibility = pending["feasibility"]
        self.loader.register_skill(
            skill_name=pending["capability"],
            class_name=pending["class_name"],
            file_name=os.path.basename(pending["file_path"]),
            capability_description=feasibility.get("summary", pending["capability"]),
            trigger_gap=pending["capability"],
            bus_events=feasibility.get("bus_events", []),
            dependencies=pending["dependencies"],
        )

        msg = (
            f"Done. The new skill is live and will auto-load on every future startup. "
            f"Try it now — {feasibility.get('summary', 'give it a go')}."
        )
        await self.bus.emit("tts_speak", msg)
        await self.bus.emit("add_jarvis_response", msg)
        logger.info(f"SynthesisAgent: Successfully activated '{pending['capability']}'")

    async def handle_rejection(self, event):
        """User rejected — clean up pending file."""
        if self._pending_confirmation:
            file_path = self._pending_confirmation.get("file_path", "")
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"SynthesisAgent: Cleaned up rejected skill file {file_path}")
            self._pending_confirmation = None
        await self.bus.emit("tts_speak", "Understood. I've discarded the generated skill.")

    # ── Internal Helpers ──────────────────────────────────────────────────────

    async def _research(self, capability: str) -> dict | None:
        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": RESEARCH_PROMPT.format(capability=capability)}],
                system="You are a Python library research specialist. Return only valid JSON.",
                max_tokens=700,
                temperature=0.1,
            )
            clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(clean)
        except Exception as e:
            logger.error(f"SynthesisAgent: Research failed: {e}")
            return None

    async def _generate_code(
        self, capability: str, feasibility: dict, error_context: str = ""
    ) -> str | None:
        creds = feasibility.get("credential_requirements", [])
        cred_str = ", ".join(creds) if creds else "none required"

        prompt = CODE_GENERATION_PROMPT.format(
            capability=capability,
            library_name=feasibility.get("library_name", "appropriate library"),
            code_example=feasibility.get("code_example", "no example available"),
            bus_events=feasibility.get("bus_events", []),
            credential_env_vars=cred_str,
        )

        if error_context:
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED WITH: {error_context}\nFix these issues."

        try:
            code = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "You are writing production Python code for JARVIS. "
                    "Output ONLY the raw Python code. No markdown, no explanation."
                ),
                max_tokens=1500,
                temperature=0.2,
            )
            # Strip markdown fences if model added them anyway
            code = code.strip()
            if code.startswith("```"):
                code = re.sub(r"^```[a-z]*\n?", "", code)
                code = re.sub(r"\n?```$", "", code)
            return code.strip()
        except Exception as e:
            logger.error(f"SynthesisAgent: Code generation failed: {e}")
            return None

    async def _generate_infeasibility_message(self, capability: str, feasibility: dict) -> str:
        try:
            return await self.llm.complete(
                messages=[{"role": "user", "content": FEASIBILITY_CONVERSATION_PROMPT.format(
                    capability=capability, feasibility=json.dumps(feasibility, indent=2)
                )}],
                system="You are JARVIS. Be concise and helpful.",
                max_tokens=150,
            )
        except Exception:
            return f"I'm afraid I can't build that capability: {feasibility.get('infeasible_reason', 'unknown reason')}."

    async def _generate_confirmation_message(self, skill_summary: dict) -> str:
        try:
            return await self.llm.complete(
                messages=[{"role": "user", "content": CONFIRMATION_PROMPT.format(
                    details=json.dumps(skill_summary, indent=2)
                )}],
                system="You are JARVIS. Be concise. End with a yes/no question.",
                max_tokens=150,
            )
        except Exception:
            return (
                f"I've written a skill for '{skill_summary.get('capability', 'this')}'. "
                f"Shall I activate it?"
            )

    def _summarise_skill(self, capability, feasibility, file_name, code) -> dict:
        return {
            "capability": capability,
            "library": feasibility.get("library_name"),
            "pip_package": feasibility.get("pip_package"),
            "bus_events": feasibility.get("bus_events", []),
            "file_name": file_name,
            "class_name": self._extract_class_name(code),
            "summary": feasibility.get("summary", ""),
        }

    @staticmethod
    def _extract_class_name(code: str) -> str:
        match = re.search(r"class\s+(\w+)\s*\(", code)
        return match.group(1) if match else "GeneratedSkill"

    @staticmethod
    def _capability_to_filename(capability: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", capability.lower()).strip("_")
        slug = slug[:40]  # cap length
        return f"{slug}_skill.py"

    @staticmethod
    def _check_credential(cred_spec: str) -> bool:
        """Check if an env var like 'TELEGRAM_BOT_TOKEN: description' is set."""
        env_var = cred_spec.split(":")[0].strip()
        return bool(os.environ.get(env_var, "").strip())

    @staticmethod
    def _capabilities_overlap(cap_a: str, cap_b: str) -> bool:
        """Simple word-overlap heuristic to detect duplicate capabilities."""
        words_a = set(cap_a.lower().split())
        words_b = set(cap_b.lower().split())
        stopwords = {"a", "an", "the", "my", "me", "i", "it", "to", "can", "do", "use"}
        words_a -= stopwords
        words_b -= stopwords
        if not words_a or not words_b:
            return False
        overlap = words_a & words_b
        return len(overlap) / min(len(words_a), len(words_b)) > 0.6

    async def _status(self, message: str):
        await self.bus.emit("set_status", message)
        logger.info(f"SynthesisAgent: {message}")
```

---

## 4.5.6 — Wire Into `webview_main.py`

```python
# Add imports
from core.skill_loader import SkillLoader
from agents.synthesis_agent import SynthesisAgent

# After bus and engine are created, before skills:
_skill_loader = SkillLoader(_bus)

# Auto-load all previously synthesised skills
_synthesised_skills = _skill_loader.load_all_active()
_runtime_objects.extend(_synthesised_skills)

# Create the SynthesisAgent
_synthesis_agent = SynthesisAgent(_bus, _skill_loader)
_runtime_objects.append(_synthesis_agent)
```

---

## 4.5.7 — Wire Into `agents/orchestrator.py`

Add gap detection to the Orchestrator. When no specialist agent can handle a request AND the LLM confidence in its task plan is low:

```python
# In OrchestratorAgent._plan(), after getting the task list:

if not plan or all(t.action == "get_info" for t in plan):
    # Check if this looks like a capability gap
    gap_check = await self._detect_gap(user_input)
    if gap_check.get("is_gap"):
        await self.bus.emit("capability_gap_detected", {
            "user_request": user_input,
            "gap_type": "missing_skill",
            "confidence": gap_check.get("confidence", 0.8),
        })
        return []  # Synthesis agent takes over

# Add this helper method:
async def _detect_gap(self, user_input: str) -> dict:
    """Ask the LLM if this looks like a request for an unknown capability."""
    known_caps = self.skill_loader.get_known_capabilities() if hasattr(self, 'skill_loader') else []
    prompt = f"""
Does this user request ask for a capability that a desktop AI assistant 
(controlling computer, media, email, calendar, weather, browser, Spotify) 
would NOT normally have built-in?

Known synthesised capabilities: {known_caps}

Request: "{user_input}"

Return ONLY JSON: {{"is_gap": true/false, "confidence": 0.0-1.0, "reason": "brief reason"}}
"""
    try:
        raw = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system="You are a capability classifier. Return only valid JSON.",
            max_tokens=100, temperature=0.0
        )
        clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return {"is_gap": False, "confidence": 0.0}
```

---

## 4.5.8 — Add to `INTENT_SYSTEM_PROMPT` in `skills/llm_skill.py`

Add these two actions to the intent parser so the LLM can route confirmation/rejection:

```
- synthesis_confirm: {}     // user confirms they want to activate a pending skill
- synthesis_reject:  {}     // user declines a pending skill
```

And add these handlers in `LLMSkill.command_handlers`:

```python
"synthesis_confirm": lambda params: self.bus.emit("synthesis_confirm", {}),
"synthesis_reject":  lambda params: self.bus.emit("synthesis_reject", {}),
```

---

## Phase 4.5 Testing Checklist

- [ ] Ask for something Jarvis can't do (e.g., "control my Hue lights") → SynthesisAgent activates
- [ ] Feasibility research returns valid JSON → agent continues pipeline
- [ ] Infeasible capability (e.g., "control my toaster") → graceful explanation, no code written
- [ ] Missing credential (e.g., Telegram without BOT_TOKEN) → user told what to add, no code written
- [ ] Code generation produces valid Python BaseSkill subclass
- [ ] AST scanner correctly blocks: `os.system()`, `exec()`, `eval()`, `subprocess.run()`
- [ ] AST scanner correctly ALLOWS: `import requests`, standard library
- [ ] Subprocess sandbox passes for a valid skill, fails for broken code
- [ ] `_safe_filepath()` rejects path traversal: `../../evil.py` → error, no write
- [ ] `_safe_filepath()` rejects absolute paths outside GENERATED_DIR
- [ ] User says "no"/"reject" → file deleted, nothing loaded
- [ ] User says "yes"/"activate it" → skill hot-loaded, DB registered
- [ ] App restart → synthesised skill auto-loads from DB + file
- [ ] Ask for same capability again → duplicate detection fires, no re-synthesis
- [ ] All Phase 4 and prior tests still pass

---

## 4.5.9 — Example: Full Walkthrough

```
User: "Jarvis, send me a desktop notification when it's 5pm"

Orchestrator: No known action matches. Gap confidence: 0.91
→ capability_gap_detected emitted

SynthesisAgent Stage 1 (Research):
  Library: "win10toast / plyer"
  pip_package: "plyer"
  windows_compatible: true
  prerequisites: []
  credential_requirements: []
  feasible: true
  bus_events: ["show_notification"]
  summary: "Shows desktop toast notifications on Windows"

SynthesisAgent Stage 2 (Feasibility): PASS
  No credentials needed. plyer exists on PyPI. 

SynthesisAgent: "I need to install 'plyer'. Installing..."
  → pip install plyer → SUCCESS

SynthesisAgent Stage 3 (Code Gen):
  → Generates desktop_notification_skill.py in jarvis-generated-code/

SynthesisAgent Stage 4 (Validation):
  AST scan: PASS (no blocked patterns)
  Subprocess: PASS (imports, instantiates, registers)

SynthesisAgent Stage 5 (Human Gate):
  → "I've written a desktop notification skill using plyer. It subscribes 
     to the 'show_notification' event and will display Windows toast 
     notifications. Shall I activate it?"

User: "yes activate it"

SynthesisAgent Stage 6 (Hot-Load):
  → importlib loads desktop_notification_skill.py
  → DesktopNotificationSkill.register() → subscribes to 'show_notification'
  → Registered in synthesised_skills DB table

Jarvis: "Done. Desktop notification skill is live and will load automatically 
         from now on. Your 5pm reminder is also set."

Next session startup:
  SkillLoader.load_all_active() → loads DesktopNotificationSkill automatically
```

# Appendix — New Files Summary

```
core/
  llm_client.py              # Phase 0
  reasoning.py               # Phase 3
  capability_manifest.py     # Phase 3
  proactive_agent.py         # Phase 3
  task_queue.py              # Phase 4
  code_sandbox.py            # Phase 4.5 ← AST scanner + subprocess sandbox
  skill_loader.py            # Phase 4.5 ← dynamic loader + registry
  memory/
    __init__.py              # Phase 1
    manager.py               # Phase 1
    working.py               # Phase 1
    episodic.py              # Phase 1
    semantic.py              # Phase 1
    procedural.py            # Phase 1
    entity_store.py          # Phase 2
    entity_extractor.py      # Phase 2

agents/
  __init__.py                # Phase 4
  orchestrator.py            # Phase 4 (updated in Phase 4.5 for gap detection)
  info_agent.py              # Phase 4
  system_agent.py            # Phase 4
  media_agent.py             # Phase 4
  comms_agent.py             # Phase 4
  browser_agent.py           # Phase 4
  personal_agent.py          # Phase 4
  synthesis_agent.py         # Phase 4.5 ← the self-building agent

interfaces/
  agent.py                   # Phase 4 (extends existing skill.py pattern)

jarvis-generated-code/       # Phase 4.5 ← ALL generated skills live here ONLY
  __init__.py                # tracked in git
  .gitkeep                   # tracked in git
  *.py                       # NOT tracked in git (generated at runtime)

scripts/
  migrate_v2_to_v3.py        # Phase 5

memory_store/
  chroma/                    # Phase 1 (auto-created by ChromaDB)
```

# Appendix — Preserved Existing Files (Unchanged)

```
services/biometrics.py       ✓ unchanged
services/gesture.py          ✓ unchanged
services/stt.py              ✓ unchanged
services/tts.py              ✓ unchanged
services/wake_word.py        ✓ unchanged
skills/browser_control.py    ✓ becomes Tool Agent (Phase 4)
skills/calendar_skill.py     ✓ becomes Tool Agent (Phase 4)
skills/communication.py      ✓ becomes Tool Agent (Phase 4)
skills/media_control.py      ✓ becomes Tool Agent (Phase 4)
skills/media_downloader.py   ✓ becomes Tool Agent (Phase 4)
skills/news_skill.py         ✓ becomes Tool Agent (Phase 4)
skills/productivity.py       ✓ becomes Tool Agent (Phase 4)
skills/quick_launch.py       ✓ becomes Tool Agent (Phase 4)
skills/spotify_skill.py      ✓ becomes Tool Agent (Phase 4)
skills/system.py             ✓ becomes Tool Agent (Phase 4)
skills/system_control.py     ✓ becomes Tool Agent (Phase 4)
skills/weather_skill.py      ✓ becomes Tool Agent (Phase 4)
skills/web_automation.py     ✓ becomes Tool Agent (Phase 4)
skills/whatsapp_skill.py     ✓ becomes Tool Agent (Phase 4)
frontend/                    ✓ fully preserved, additions only in Phase 5
config/                      ✓ additions only
```
