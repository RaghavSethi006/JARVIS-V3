"""Runtime self-synthesis pipeline for new Jarvis skills."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional

try:
    from core.event_bus import Event, EventBus
except ImportError:
    Event = Any
    EventBus = Any

try:
    from core.llm_client import LLMClient
except ImportError:
    LLMClient = None

try:
    from core.logger import logger
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("synthesis_agent: core.logger import failed. Using fallback logger.")

try:
    from core.code_sandbox import validate as sandbox_validate
except ImportError as exc:
    sandbox_validate = None
    logger.warning("synthesis_agent: code_sandbox import failed: %s", exc)

try:
    from core.skill_loader import SkillLoader
except ImportError as exc:
    SkillLoader = None
    logger.warning("synthesis_agent: skill_loader import failed: %s", exc)


RESEARCH_PROMPT = """
You are researching how to implement a new capability for a Python desktop AI assistant
running on Windows.

Capability requested: {capability}

Search and analyse what you know about:
1. The best Python library or approach for this
2. A minimal working code example
3. Any prerequisites such as hardware, credentials, or local services
4. Windows compatibility notes
5. Any known limitations or gotchas

Return ONLY valid JSON:
{{
  "library_name": "human readable name",
  "pip_package": "exact pip install name or null if no install needed",
  "windows_compatible": true,
  "prerequisites": ["list of non-pip requirements"],
  "credential_requirements": ["ENV_VAR_NAME: description"],
  "complexity": "simple|medium|complex",
  "feasible": true,
  "infeasible_reason": "only if feasible is false",
  "code_example": "minimal working snippet",
  "bus_events": ["event_name_1"],
  "summary": "one sentence description of what this skill will do"
}}
"""

CODE_GENERATION_PROMPT = """
You are writing a new skill module for the JARVIS AI assistant.

STRICT RULES:
1. Define exactly one class extending BaseSkill
2. Include register() and subscribe to the provided bus events
3. No os.system(), subprocess, exec(), eval(), or __import__()
4. Import optional third-party libraries inside handlers so missing packages degrade gracefully
5. Use run_in_executor for blocking work
6. All bus handlers must be async def handler(self, event: Event)
7. Speak responses with both:
   await self.bus.emit("tts_speak", "text")
   await self.bus.emit("add_jarvis_response", "text")
8. Log failures with logger.error(...)

Follow this pattern:
from interfaces.skill import BaseSkill
from core.event_bus import Event
from core.logger import logger
import asyncio

class ExampleSkill(BaseSkill):
    def __init__(self, bus):
        super().__init__(bus)

    def register(self):
        self.bus.subscribe("example_action", self.handle_action)

    async def handle_action(self, event: Event):
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._do_sync_work, event.data or {{}})
            await self.bus.emit("tts_speak", result)
            await self.bus.emit("add_jarvis_response", result)
        except Exception as exc:
            logger.error(f"ExampleSkill: {{exc}}")
            await self.bus.emit("tts_speak", "Something went wrong.")
            await self.bus.emit("add_jarvis_response", "Something went wrong.")

    def _do_sync_work(self, params):
        return "result"

CAPABILITY TO IMPLEMENT: {capability}
LIBRARY TO USE: {library_name}
CODE EXAMPLE FOR REFERENCE: {code_example}
BUS EVENTS TO SUBSCRIBE TO: {bus_events}
CREDENTIAL ENV VARS AVAILABLE: {credential_env_vars}

Write the complete skill file. Output ONLY Python code.
"""

FEASIBILITY_CONVERSATION_PROMPT = """
The user asked JARVIS to do something it cannot currently do: "{capability}"

Here is the feasibility analysis:
{feasibility}

Write a concise response that explains what you can build, what prerequisites are missing,
and whether the user should proceed. If it is infeasible, explain why and suggest an alternative.
"""

CONFIRMATION_PROMPT = """
You just wrote a new skill for JARVIS. Summarise it for the user in 2-3 sentences.
Include what it does, which bus events it handles, and any dependencies installed.
Ask whether they want to activate it now.

Skill details:
{details}
"""


class SynthesisAgent:
    """Research, validate, and hot-load new generated skills."""

    def __init__(self, bus: EventBus, skill_loader: SkillLoader):
        self.bus = bus
        self.llm = LLMClient.get() if LLMClient is not None else None
        self.loader = skill_loader
        self._pending_confirmation: Optional[dict[str, Any]] = None
        self._active_generated_skills: list[Any] = []
        self._synthesis_lock = asyncio.Lock()
        self._registered = False

    def register(self) -> None:
        """Register synthesis event listeners."""
        if self._registered:
            return
        self.bus.subscribe("capability_gap_detected", self.handle_gap)
        self.bus.subscribe("synthesis_confirm", self.handle_confirmation)
        self.bus.subscribe("synthesis_reject", self.handle_rejection)
        self._registered = True

    def has_pending_confirmation(self) -> bool:
        """Return whether a generated skill is waiting for activation."""
        return self._pending_confirmation is not None

    async def handle_gap(self, event: Event) -> None:
        """Start the synthesis pipeline for a detected missing capability."""
        if self.llm is None or sandbox_validate is None:
            await self._speak(
                "I can detect the missing capability, but my synthesis systems are unavailable at the moment."
            )
            return

        async with self._synthesis_lock:
            gap = (event.data or {}) if isinstance(event.data, dict) else {}
            capability = str(gap.get("user_request", "")).strip()
            gap_type = str(gap.get("gap_type", "missing_skill")).strip()
            if not capability or gap_type != "missing_skill":
                return

            logger.info("SynthesisAgent: gap detected for '%s'", capability)
            await self.bus.emit("set_core_state", "thinking")
            await self._speak("I don't currently have that capability. Let me see if I can build it.")
            await self._run_pipeline(capability)

    async def handle_confirmation(self, event: Event) -> None:
        """Activate the pending generated skill after user approval."""
        if not self._pending_confirmation:
            await self._speak("There's nothing pending activation at the moment.")
            return

        pending = self._pending_confirmation
        self._pending_confirmation = None

        await self._status("Activating generated skill...")
        skill = self.loader.hot_load(pending["file_path"])
        if skill is None:
            await self._speak("The generated skill file exists, but it couldn't be activated safely.")
            await self.bus.emit("set_core_state", "idle")
            return

        self._active_generated_skills.append(skill)
        feasibility = pending["feasibility"]
        self.loader.register_skill(
            skill_name=pending["capability"],
            class_name=pending["class_name"],
            file_name=os.path.basename(pending["file_path"]),
            capability_description=feasibility.get("summary", pending["capability"]),
            trigger_gap=pending["capability"],
            bus_events=feasibility.get("bus_events", []),
            dependencies=pending["dependencies"],
            validation_passed=True,
            notes=feasibility.get("summary", ""),
        )

        await self._speak(
            "Done. The new skill is live and will auto-load on future startup. "
            "You can try it as soon as its event is triggered."
        )
        await self.bus.emit("set_core_state", "idle")
        logger.info("SynthesisAgent: successfully activated '%s'", pending["capability"])

    async def handle_rejection(self, event: Event) -> None:
        """Discard the pending generated skill when the user declines activation."""
        pending = self._pending_confirmation
        self._pending_confirmation = None
        if pending:
            try:
                self.loader.delete_skill_file(pending.get("file_path", ""))
            except Exception as exc:
                logger.warning("SynthesisAgent: cleanup after rejection failed: %s", exc)
        await self._speak("Understood. I've discarded the generated skill.")
        await self.bus.emit("set_core_state", "idle")

    async def _run_pipeline(self, capability: str) -> None:
        """Execute the full research-to-confirmation synthesis flow."""
        await self._status("Researching implementation options...")
        feasibility = await self._research(capability)
        if not feasibility:
            await self._speak("My research pass failed, so I can't build that safely just now.")
            await self.bus.emit("set_core_state", "idle")
            return

        if not feasibility.get("feasible", False):
            await self._speak(await self._generate_infeasibility_message(capability, feasibility))
            await self.bus.emit("set_core_state", "idle")
            return

        if not feasibility.get("windows_compatible", True):
            await self._speak("The best available approach is not Windows-compatible, so I won't build it here.")
            await self.bus.emit("set_core_state", "idle")
            return

        missing_creds = [
            cred for cred in feasibility.get("credential_requirements", [])
            if not self._check_credential(cred)
        ]
        if missing_creds:
            await self._speak(
                "I can build this, but I need these configuration values first: "
                + ", ".join(missing_creds)
                + ". Add them to config/.env and ask again."
            )
            await self.bus.emit("set_core_state", "idle")
            return

        for known_capability in self.loader.get_known_capabilities():
            if self._capabilities_overlap(capability, known_capability):
                await self._speak(
                    "I already have a generated skill for something very similar: "
                    f"{known_capability}. Please clarify what's different if you need another one."
                )
                await self.bus.emit("set_core_state", "idle")
                return

        installed_deps: list[str] = []
        pip_package = feasibility.get("pip_package")
        if pip_package:
            await self._status(f"Installing dependency '{pip_package}'...")
            if not self.loader.check_package_on_pypi(pip_package):
                await self._speak(
                    f"The package '{pip_package}' doesn't appear to exist on PyPI, so I can't safely continue."
                )
                await self.bus.emit("set_core_state", "idle")
                return
            success, output = self.loader.install_package(pip_package)
            if not success:
                await self._speak(f"Installation of '{pip_package}' failed: {output[:160]}")
                await self.bus.emit("set_core_state", "idle")
                return
            installed_deps.append(pip_package)

        await self._status("Writing generated skill code...")
        code = await self._generate_code(capability, feasibility)
        if not code:
            await self._speak("I couldn't generate working code for that capability.")
            await self.bus.emit("set_core_state", "idle")
            return

        file_name = self._capability_to_filename(capability)
        file_path = ""
        try:
            file_path = self.loader.write_skill(file_name, code)
        except ValueError as exc:
            logger.error("SynthesisAgent: security violation during write: %s", exc)
            await self._speak("A security check failed while writing the generated skill.")
            await self.bus.emit("set_core_state", "idle")
            return

        await self._status("Validating generated skill...")
        validation = sandbox_validate(code, file_path)
        if not validation.passed:
            logger.warning("SynthesisAgent: validation failed, retrying with feedback.")
            retry_code = await self._generate_code(
                capability,
                feasibility,
                error_context="; ".join(validation.errors),
            )
            if retry_code:
                code = retry_code
                try:
                    file_path = self.loader.write_skill(file_name, code)
                except ValueError as exc:
                    logger.error("SynthesisAgent: security violation during retry write: %s", exc)
                    await self._speak("The revised generated skill failed a security check.")
                    await self.bus.emit("set_core_state", "idle")
                    return
                validation = sandbox_validate(code, file_path)

        if not validation.passed:
            self.loader.delete_skill_file(file_path)
            errors = "; ".join(validation.errors)[:200]
            await self._speak(f"I generated a draft, but it failed safety validation: {errors}")
            await self.bus.emit("set_core_state", "idle")
            return

        summary = self._summarise_skill(
            capability=capability,
            feasibility=feasibility,
            file_name=file_name,
            code=code,
            dependencies=installed_deps,
        )
        confirmation_message = await self._generate_confirmation_message(summary)
        self._pending_confirmation = {
            "capability": capability,
            "file_path": file_path,
            "file_name": file_name,
            "code": code,
            "feasibility": feasibility,
            "dependencies": installed_deps,
            "class_name": self._extract_class_name(code),
        }
        await self._speak(confirmation_message)
        await self.bus.emit("set_core_state", "idle")

    async def _research(self, capability: str) -> Optional[dict[str, Any]]:
        """Ask the LLM for a structured feasibility analysis."""
        if self.llm is None:
            return None
        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": RESEARCH_PROMPT.format(capability=capability)}],
                system="You are a Python library research specialist. Return only valid JSON.",
                max_tokens=700,
                temperature=0.1,
            )
            payload = self._parse_json_block(raw)
            if not isinstance(payload, dict):
                raise ValueError("Research response was not a JSON object.")
            payload.setdefault("feasible", False)
            payload.setdefault("windows_compatible", True)
            payload.setdefault("credential_requirements", [])
            payload.setdefault("prerequisites", [])
            payload.setdefault("bus_events", [])
            payload.setdefault("summary", capability)
            return payload
        except Exception as exc:
            logger.error("SynthesisAgent: research failed: %s", exc)
            return None

    async def _generate_code(
        self,
        capability: str,
        feasibility: dict[str, Any],
        error_context: str = "",
    ) -> Optional[str]:
        """Generate BaseSkill-compliant Python code for the requested capability."""
        if self.llm is None:
            return None

        credential_env_vars = ", ".join(feasibility.get("credential_requirements", [])) or "none required"
        prompt = CODE_GENERATION_PROMPT.format(
            capability=capability,
            library_name=feasibility.get("library_name", "appropriate library"),
            code_example=feasibility.get("code_example", "No code example available."),
            bus_events=feasibility.get("bus_events", []),
            credential_env_vars=credential_env_vars,
        )
        if error_context:
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED WITH: {error_context}\nFix those issues."

        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system="You write production Python for JARVIS. Output only raw Python code.",
                max_tokens=1800,
                temperature=0.2,
            )
            return self._strip_code_fences(raw)
        except Exception as exc:
            logger.error("SynthesisAgent: code generation failed: %s", exc)
            return None

    async def _generate_infeasibility_message(
        self,
        capability: str,
        feasibility: dict[str, Any],
    ) -> str:
        """Turn structured feasibility data into a concise user-facing reply."""
        if self.llm is None:
            return (
                "I'm afraid I can't build that capability safely right now: "
                + str(feasibility.get("infeasible_reason", "unknown reason"))
            )
        try:
            return await self.llm.complete(
                messages=[
                    {
                        "role": "user",
                        "content": FEASIBILITY_CONVERSATION_PROMPT.format(
                            capability=capability,
                            feasibility=json.dumps(feasibility, indent=2),
                        ),
                    }
                ],
                system="You are JARVIS. Be concise and helpful.",
                max_tokens=180,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("SynthesisAgent: infeasibility message generation failed: %s", exc)
            return (
                "I'm afraid I can't build that capability safely right now: "
                + str(feasibility.get("infeasible_reason", "unknown reason"))
            )

    async def _generate_confirmation_message(self, skill_summary: dict[str, Any]) -> str:
        """Generate a concise confirmation request for the user."""
        if self.llm is None:
            return f"I've written a new skill for {skill_summary.get('capability', 'that request')}. Shall I activate it?"
        try:
            return await self.llm.complete(
                messages=[
                    {
                        "role": "user",
                        "content": CONFIRMATION_PROMPT.format(details=json.dumps(skill_summary, indent=2)),
                    }
                ],
                system="You are JARVIS. Be concise. End with a yes or no activation question.",
                max_tokens=180,
                temperature=0.4,
            )
        except Exception as exc:
            logger.warning("SynthesisAgent: confirmation message generation failed: %s", exc)
            return f"I've written a new skill for {skill_summary.get('capability', 'that request')}. Shall I activate it?"

    def _summarise_skill(
        self,
        capability: str,
        feasibility: dict[str, Any],
        file_name: str,
        code: str,
        dependencies: list[str],
    ) -> dict[str, Any]:
        """Build a compact summary of the generated skill for confirmation."""
        return {
            "capability": capability,
            "library": feasibility.get("library_name"),
            "pip_package": feasibility.get("pip_package"),
            "dependencies_installed": dependencies,
            "bus_events": feasibility.get("bus_events", []),
            "file_name": file_name,
            "class_name": self._extract_class_name(code),
            "summary": feasibility.get("summary", capability),
        }

    async def _status(self, message: str) -> None:
        """Emit synthesis progress to the UI status line."""
        await self.bus.emit("set_status", message)
        logger.info("SynthesisAgent: %s", message)

    async def _speak(self, message: str) -> None:
        """Emit a Jarvis response through both speech and chat pipelines."""
        await self.bus.emit("tts_speak", message)
        await self.bus.emit("add_jarvis_response", message)

    @staticmethod
    def _parse_json_block(raw: str) -> Any:
        """Extract a JSON object from model output that may include fences."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            return json.loads(clean[start : end + 1])

    @staticmethod
    def _strip_code_fences(raw: str) -> str:
        """Remove stray markdown fences from a code response."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:python)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)
        return clean.strip()

    @staticmethod
    def _extract_class_name(code: str) -> str:
        """Extract the generated BaseSkill subclass name."""
        match = re.search(r"class\s+(\w+)\s*\(", code)
        return match.group(1) if match else "GeneratedSkill"

    @staticmethod
    def _capability_to_filename(capability: str) -> str:
        """Convert a natural-language capability into a stable generated filename."""
        slug = re.sub(r"[^a-z0-9]+", "_", capability.lower()).strip("_")
        slug = slug[:40] or "generated_skill"
        if not slug.endswith("_skill"):
            slug = f"{slug}_skill"
        return f"{slug}.py"

    @staticmethod
    def _check_credential(cred_spec: str) -> bool:
        """Check whether the referenced environment variable is configured."""
        env_var = str(cred_spec).split(":", 1)[0].strip()
        return bool(env_var and os.environ.get(env_var, "").strip())

    @staticmethod
    def _capabilities_overlap(capability_a: str, capability_b: str) -> bool:
        """Use simple token overlap to avoid generating duplicate skills."""
        stopwords = {"a", "an", "the", "my", "me", "i", "it", "to", "can", "do", "use"}
        words_a = {word for word in capability_a.lower().split() if word not in stopwords}
        words_b = {word for word in capability_b.lower().split() if word not in stopwords}
        if not words_a or not words_b:
            return False
        overlap = words_a & words_b
        return (len(overlap) / min(len(words_a), len(words_b))) > 0.6
