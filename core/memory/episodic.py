"""
Episodic Memory — compressed per-session summaries with vector embeddings.
Retrieval is by semantic relevance, not recency.
"""

import json
import os
from typing import Optional
from core.logger import logger

# Graceful import for heavy dependencies
try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    chromadb = None
    _CHROMA_AVAILABLE = False
    logger.warning("EpisodicMemory: chromadb not installed. Episodic memory disabled.")

try:
    from sentence_transformers import SentenceTransformer
    _EMBEDDER_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    _EMBEDDER_AVAILABLE = False
    logger.warning("EpisodicMemory: sentence-transformers not installed. Episodic memory disabled.")

from core.database import _conn, _lock, _ensure_schema


CHROMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory_store", "chroma"
)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class EpisodicMemory:
    """Stores per-session summaries with vector embeddings for semantic retrieval."""

    def __init__(self) -> None:
        self._chroma = None
        self._collection = None
        self._embedder = None
        self._ready = False

    def initialize(self) -> None:
        """Call on startup in a background thread. Loads ChromaDB and embedding model."""
        if not _CHROMA_AVAILABLE or not _EMBEDDER_AVAILABLE:
            logger.warning("EpisodicMemory: Dependencies missing — skipping init.")
            return

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
                     session_start: str, session_end: str, mood: str = "neutral") -> None:
        """Store episode in SQLite + ChromaDB."""
        if not self._ready:
            return

        # SQLite
        try:
            with _lock, _conn() as conn:
                _ensure_schema(conn)
                conn.execute(
                    """INSERT OR REPLACE INTO episodes
                       (id, session_start, session_end, summary, topics, mood, embedding_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (episode_id, session_start, session_end, summary,
                     json.dumps(topics), mood, episode_id)
                )
        except Exception as e:
            logger.error(f"EpisodicMemory: SQLite save failed: {e}")

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
            count = self._collection.count()
            if count == 0:
                return []
            query_embedding = self._embedder.encode(query).tolist()
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, count),
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
