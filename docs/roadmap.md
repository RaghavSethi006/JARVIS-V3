# JARVIS v3.0 — Development Roadmap
> Runtime: Groq API (llama-3.3-70b-versatile) · Architected with Claude claude-opus-4-6 (build-time only)

---

## Vision

Transform Jarvis from a smart script-runner into a genuinely intelligent, memory-rich, multi-agent personal AI that knows you, reasons about your life, and acts on your behalf — the way J.A.R.V.I.S. does in the films.

---

## Guiding Principles

- **Zero regression** — every existing feature (biometrics, gesture, TTS/STT, all skills) must work throughout all phases
- **Groq API as the runtime brain** — `llama-3.3-70b-versatile` for fast, cheap inference on all standard tasks; local llama-cpp as offline fallback
- **Claude claude-opus-4-6 is build-time only** — used via Claude.ai to design and write the app, never called at runtime
- **Optional Anthropic API for synthesis only** — Phase 4.5 code generation benefits from Opus quality; routed selectively, never for normal conversation
- **Async-first** — all new components respect the existing asyncio event-bus architecture
- **Graceful degradation** — if a new subsystem fails, Jarvis falls back to current behaviour, never goes silent
- **Incremental delivery** — each phase is independently deployable and testable

---

## Phases at a Glance

| Phase | Name | Duration (est.) | Core Deliverable |
|-------|------|----------------|-----------------|
| 0 | LLMClient Hardening | 1–2 days | Formalise existing Groq integration into robust LLMClient wrapper with retries, fallback, and smart routing |
| 1 | Memory Core | 1–2 weeks | 4-tier memory system live and injecting context |
| 2 | Entity Memory | 1–2 weeks | Living knowledge graph of people, projects, topics |
| 3 | AGI Personality | 1 week | Reasoning layer, proactive behaviour, real character |
| 4 | Multi-Agent Architecture | 2–3 weeks | Orchestrator + Specialist Agents, parallel task execution |
| 5 | Integration & Polish | 1 week | All systems unified, UI updates, full test pass |

---

## Phase 0 — LLMClient Hardening

**Status: Partially done** — Groq is already running (`llama-3.3-70b-versatile` confirmed in logs). Phase 0 formalises and hardens what exists.

**Goal:** Wrap the existing Groq integration into a production-grade `LLMClient` singleton with retry/backoff, a clean fallback chain, and an optional high-quality routing lane for the tasks where model quality really matters (code synthesis, session summarisation). Nothing in the codebase imports `groq` or `llama_cpp` directly — everything goes through `LLMClient`.

**Runtime model priority order:**
1. **Groq API** — `llama-3.3-70b-versatile` — primary for all standard tasks (fast, cheap)
2. **Local llama-cpp** — `Llama-3.2-3B-Instruct-Q4_K_M.gguf` — offline fallback when Groq unreachable
3. **Anthropic API** — `claude-opus-4-6-20250514` — optional, only for Phase 4.5 code synthesis

**Key changes:**
- Harden `core/llm_client.py` — retry with backoff (3 attempts, 2s/4s), timeout, rate limit detection
- Add `complete_smart(task_type=)` method — routes to Anthropic if `task_type="synthesis"`, Groq otherwise
- Formalise `GROQ_API_KEY` and `GROQ_MODEL` in `config/.env` and `config/settings.yaml`
- Add optional `ANTHROPIC_API_KEY` to `.env` (only needed when Phase 4.5 is built)
- Fix the `window.X is not a function` JS spam — add `typeof` guard to `_evaluate_js_call()`

**Completion criteria:** Groq drops connection → auto retry → falls back to local model with a warning. Zero `is not a function` errors in logs. All existing commands work unchanged.

---

## Phase 1 — Memory Core

**Goal:** Replace the 20-message rolling buffer with a full 4-tier memory architecture. Jarvis begins to remember you across sessions.

**Tiers built:**
1. **Working Memory** — structured in-session context (annotated exchanges, current task/goal, active entities)
2. **Episodic Memory** — per-session summaries stored with vector embeddings; retrieved by semantic relevance, not recency
3. **Semantic Memory** — continuously-updated user profile of facts, preferences, and behavioural patterns
4. **Procedural Memory** — named multi-step macros/workflows learned from usage

**Key changes:**
- Add `chromadb` (local vector DB) to `requirements.txt`
- Add `sentence-transformers` (embedding model: `all-MiniLM-L6-v2`) to `requirements.txt`
- Create `core/memory/` package: `manager.py`, `working.py`, `episodic.py`, `semantic.py`, `procedural.py`
- Extend `database.py` schema: new tables for episodes, user facts, procedures
- `SessionSummarizer` runs on window close, compresses session to episode via Claude API
- Context builder injects relevant memory into every LLM prompt

**Completion criteria:** After a conversation about a topic, a fresh session can reference what was discussed. User preferences are recalled unprompted.

---

## Phase 2 — Entity Memory

**Goal:** Every person, project, topic, or place mentioned in conversation becomes a living structured object that grows richer over time.

**Entity types:** `person`, `project`, `topic`, `place`, `organization`, `concept`

**Key changes:**
- Create `core/memory/entity_store.py` — CRUD, fuzzy resolution, relationship graph
- Create `core/memory/entity_extractor.py` — async post-exchange pipeline using Claude API
- Extend DB schema: `entities`, `entity_facts`, `entity_relationships`, `entity_threads` tables
- Entity embeddings stored in ChromaDB for semantic lookup
- `EntityResolver` handles alias matching and disambiguation
- Context builder enriched: entity profiles injected alongside episodic memory
- Contradiction detection: flags when new facts conflict with stored ones

**Completion criteria:** Mention a person by name twice across different sessions and Jarvis recalls prior facts about them. Mention a project and Jarvis has its current status, tech stack, and open threads.

---

## Phase 3 — AGI Personality Layer

**Goal:** Make interactions feel genuinely intelligent — Jarvis reasons before acting, anticipates needs, has a consistent evolving character, and takes ownership of tasks.

**Key changes:**
- Create `core/reasoning.py` — chain-of-thought pre-response layer for complex inputs (internal scratchpad, never spoken)
- Create `core/proactive_agent.py` — background timer checking calendar, reminders, email, learned patterns; surfaces gentle notifications
- Overhaul system prompt in `skills/llm_skill.py` — full character definition (tone, address style, uncertainty handling, signature phrases)
- Add `CapabilityManifest` — structured description of all skills injected into system context so Jarvis knows what it can/can't do
- Task ownership: Jarvis narrates multi-step progress in real time
- Emotional state tracking in Working Memory: adjusts verbosity based on detected user mood/pace

**Completion criteria:** Complex requests trigger visible reasoning. Jarvis surfaces an unprompted calendar reminder. Responses reference past conversations naturally. Failure cases produce helpful alternatives, not silence.

---

## Phase 4 — Multi-Agent Architecture

**Goal:** Decompose the monolithic `LLMSkill` into a clean 3-tier agent hierarchy capable of parallel execution, specialised reasoning, and intelligent delegation.

**Agent tiers:**

**Tier 1 — Orchestrator**
Receives all user input. Decomposes into a dependency-ordered task plan. Delegates to specialists. Synthesises results into a final response. Never executes directly.

**Tier 2 — Specialist Agents**
- `InfoAgent` — knowledge, weather, news, calendar reads, email reads
- `SystemAgent` — app control, volume, brightness, screenshots, file ops
- `MediaAgent` — Spotify, YouTube, local media, downloads
- `CommsAgent` — email send, WhatsApp, calendar create, contacts
- `BrowserAgent` — goal-directed multi-step Selenium automation
- `PersonalAgent` — memory management, proactive behaviour, user modelling

**Tier 3 — Tool Agents**
Existing skills refactored as pure function-callers. Return structured `{status, data}` results. TTS is handled upstream by Specialist Agents, not by tools directly.

**Key changes:**
- Create `agents/` package: `orchestrator.py`, `info_agent.py`, `system_agent.py`, `media_agent.py`, `comms_agent.py`, `browser_agent.py`, `personal_agent.py`
- Create `core/task_queue.py` — parallel task execution with dependency resolution and timeout handling
- Refactor all `skills/` to pure tool functions (remove direct TTS calls, return structured results)
- Update `webview_main.py` to instantiate the agent hierarchy instead of flat skill list
- `BaseAgent` abstract class in `interfaces/agent.py`

**Completion criteria:** A 3-part request ("play something, check my meetings, set a reminder") executes in parallel and synthesises a single coherent spoken response.

---

## Phase 4.5 — Self-Synthesis Agent (Jarvis Builds Its Own Skills)

**Goal:** When Jarvis is asked to do something it can't, a dedicated SynthesisAgent activates — researches the capability online, writes a new skill, validates it in isolation, and hot-loads it into the running system permanently. All generated code lives exclusively in `jarvis-generated-code/`.

**The flow:**
```
User asks for unknown capability
    → Orchestrator: gap detected
    → SynthesisAgent activates
    → Stage 1: Web research (3-5 targeted searches)
    → Stage 2: Feasibility check (library exists? Windows OK? credentials needed?)
    → Stage 3: Code generation (Claude writes BaseSkill-compliant skill)
    → Stage 4: Sandbox validation (subprocess isolation, AST security scan)
    → Stage 5: Human confirmation gate ("Here's what I wrote — activate it?")
    → Stage 6: Hot-load into running Jarvis via importlib
    → Skill persisted → auto-loads on every future startup
```

**The `jarvis-generated-code/` folder rule:**
All generated skills are written **exclusively** to `jarvis-generated-code/`. This is enforced at three independent layers: the LLM prompt, the AST scanner, and the file writer. Any attempt to write outside this folder is a hard abort — no exceptions, no overrides.

**Key changes:**
- Create `jarvis-generated-code/` — isolated home for all LLM-written skills
- Create `agents/synthesis_agent.py` — the full 6-stage pipeline
- Create `core/code_sandbox.py` — subprocess isolation + AST security analysis
- Create `core/skill_loader.py` — dynamic importlib hot-loading + skill registry
- Extend DB schema: `synthesised_skills` table (name, path, dependencies, date, active flag)
- Update `webview_main.py` — auto-load all active synthesised skills on startup
- Update `agents/orchestrator.py` — route gaps to SynthesisAgent

**Safety model (three independent layers):**
1. LLM prompt instructs: write only to `jarvis-generated-code/`, no `os.system`, no `exec/eval`
2. AST scanner blocks: any file write outside `jarvis-generated-code/`, `os.system`, `subprocess`, `exec`, `eval`, `__import__`, raw sockets
3. Subprocess sandbox: generated code runs in isolation with 10s timeout before any hot-load

**Completion criteria:** Ask for a capability Jarvis doesn't have (e.g., Telegram messaging, Hue lights, clipboard manager). Jarvis researches, writes, validates, confirms with you, and activates it. Second request uses the new skill directly with zero synthesis overhead.

---

## Phase 5 — Integration & Polish

**Goal:** All phases unified, edge cases handled, UI updated to reflect new capabilities, full regression test pass.

**Key changes:**
- Frontend: new memory/entity panel in Dashboard showing active entities and recent facts
- Frontend: agent status indicators showing which specialist is active
- `scripts/test_all.py` — comprehensive test suite covering all phases
- Performance profiling: entity extraction and memory retrieval must not add perceptible latency to responses
- `scripts/migrate_db.py` — migration script for users upgrading from v2
- Update `README.md` with full architecture documentation
- Logging enrichment: all agent decisions and memory operations logged at DEBUG level

**Completion criteria:** Full demo session — 10+ turn conversation with proactive interruption, entity recall, parallel task execution, and zero regressions on existing features.

---

## Dependency Map

```
Phase 0 (Claude API)
    └── Phase 1 (Memory Core)
            └── Phase 2 (Entity Memory)
                    └── Phase 3 (AGI Personality)
                            └── Phase 4 (Multi-Agent)
                                    └── Phase 4.5 (Self-Synthesis)
                                            └── Phase 5 (Polish)
```

Each phase depends on the one above it. Phases 1–2 can partially overlap. Phase 3 can begin its system prompt work in parallel with Phase 2. Phase 4.5 strictly requires Phase 4's Orchestrator to exist.

---

## Technology Stack Additions

| Addition | Purpose | Phase |
|----------|---------|-------|
| `groq` SDK (already installed) | Groq API — `llama-3.3-70b-versatile`, primary runtime LLM | 0 |
| `chromadb` | Local vector database for embeddings | 1 |
| `sentence-transformers` | Local embedding model (`all-MiniLM-L6-v2`) | 1 |
| `rapidfuzz` | Fuzzy string matching for entity resolution | 2 |
| `spacy` (small model) | Fast NER pre-pass before LLM extraction | 2 |
| `anthropic` SDK (optional) | Claude claude-opus-4-6 for code synthesis only — Phase 4.5 | 4.5 |
| `requests` (already installed) | PyPI package lookup during feasibility check | 4.5 |
| `chromadb` | Local vector database for embeddings | 1 |
| `sentence-transformers` | Local embedding model (`all-MiniLM-L6-v2`) | 1 |
| `rapidfuzz` | Fuzzy string matching for entity resolution | 2 |
| `spacy` (small model) | Fast NER pre-pass before LLM extraction | 2 |
| `requests` (already installed) | PyPI package lookup during feasibility check | 4.5 |

All existing dependencies preserved. No breaking removals.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Claude API latency > local model | Streaming responses; show thinking indicator; async pipeline |
| ChromaDB cold start slow | Pre-warm on app launch in background thread |
| Entity extractor over-extracts noise | Confidence threshold; user can dismiss/correct stored facts |
| Agent parallelism race conditions | TaskQueue with explicit dependency locking |
| Memory context exceeds token window | Hierarchical summarisation; tiered context truncation |
| Breaking existing skills during agent refactor | Feature flags; skills remain directly callable throughout transition |
| Generated code writing outside allowed folder | Three independent enforcement layers (prompt + AST + file writer) |
| Generated code with malicious or dangerous patterns | AST scanner blocks subprocess, exec, eval, raw sockets before any execution |
| Bad pip install breaking environment | Dry-run check against PyPI before install; rollback on failure |
| Hot-loaded skill crashing the event loop | Subprocess sandbox must pass before hot-load; bus subscription is isolated |
| LLM writes unbuildable code | One retry with error context; if still fails, reports to user with explanation |
