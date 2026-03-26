"""Dynamic loading and registry management for synthesised Jarvis skills."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

try:
    from core.database import _conn, _lock
except ImportError:
    _conn = None
    _lock = None

try:
    from core.logger import logger
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("skill_loader: core.logger import failed. Using fallback logger.")

try:
    from interfaces.skill import BaseSkill
except ImportError:
    BaseSkill = object
    logger.warning("skill_loader: BaseSkill import failed. Dynamic loading degraded.")


GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "jarvis-generated-code",
)


def _contains_path_navigation(filename: str) -> bool:
    """Return True when the supplied filename attempts path traversal."""
    if not filename:
        return False
    normalized = filename.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    return (
        "/" in normalized
        or "\\" in filename
        or any(part == ".." for part in parts)
    )


def _safe_filename(name: str) -> str:
    """Sanitize an external skill name into a safe generated filename."""
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    if not safe:
        safe = "generated_skill"
    if not safe.endswith("_skill"):
        safe = f"{safe}_skill"
    return f"{safe}.py"


def _safe_filepath(filename: str) -> str:
    """Resolve a generated skill path and reject traversal outside GENERATED_DIR."""
    if _contains_path_navigation(filename):
        raise ValueError(
            "SkillLoader: SECURITY VIOLATION - path navigation is not allowed for generated skills."
        )
    base_name = os.path.basename(filename or "")
    safe_name = _safe_filename(base_name.replace(".py", ""))
    candidate = os.path.realpath(os.path.join(GENERATED_DIR, safe_name))
    allowed_root = os.path.realpath(GENERATED_DIR)
    allowed_prefix = allowed_root + os.sep
    if not (candidate == allowed_root or candidate.startswith(allowed_prefix)):
        raise ValueError(
            f"SkillLoader: SECURITY VIOLATION - attempted path outside jarvis-generated-code/: {candidate}"
        )
    return candidate


class SkillLoader:
    """Write, hot-load, and persist generated skills."""

    def __init__(self, bus: Any) -> None:
        self.bus = bus
        os.makedirs(GENERATED_DIR, exist_ok=True)
        self._ensure_marker_files()

    def _ensure_marker_files(self) -> None:
        """Ensure the generated-skill package markers exist."""
        init_path = os.path.join(GENERATED_DIR, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w", encoding="utf-8") as handle:
                handle.write('"""Tracked package marker for runtime-generated Jarvis skills."""\n')

        gitkeep_path = os.path.join(GENERATED_DIR, ".gitkeep")
        if not os.path.exists(gitkeep_path):
            with open(gitkeep_path, "w", encoding="utf-8") as handle:
                handle.write("")

    def write_skill(self, filename: str, code: str) -> str:
        """Write generated code to the hardcoded generated-skill directory."""
        file_path = _safe_filepath(filename)
        with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(code)
        logger.info("SkillLoader: wrote generated skill to %s", file_path)
        return file_path

    def delete_skill_file(self, filename_or_path: str) -> None:
        """Delete a generated skill file if it exists."""
        file_path = (
            filename_or_path
            if os.path.isabs(filename_or_path)
            else _safe_filepath(filename_or_path)
        )
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info("SkillLoader: removed generated skill file %s", file_path)

    def hot_load(self, file_path: str, register: bool = True) -> BaseSkill | None:
        """Import, instantiate, and optionally register a generated skill."""
        if BaseSkill is object:
            logger.error("SkillLoader: BaseSkill unavailable; cannot hot-load generated skill.")
            return None

        try:
            resolved_path = file_path if os.path.isabs(file_path) else _safe_filepath(file_path)
            module_stub = os.path.splitext(os.path.basename(resolved_path))[0]
            module_name = f"jarvis_generated_{module_stub}_{int(datetime.now().timestamp())}"
            spec = importlib.util.spec_from_file_location(module_name, resolved_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not create import spec for generated skill")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            skill_classes = [
                obj
                for _, obj in inspect.getmembers(module, inspect.isclass)
                if issubclass(obj, BaseSkill) and obj is not BaseSkill
            ]
            if not skill_classes:
                raise RuntimeError("No BaseSkill subclass found in generated skill")

            skill = skill_classes[0](self.bus)
            setattr(skill, "_jarvis_registered", False)
            if register:
                skill.register()
                setattr(skill, "_jarvis_registered", True)
                self._update_last_loaded(os.path.basename(resolved_path))
            logger.info(
                "SkillLoader: hot-loaded %s from %s",
                skill_classes[0].__name__,
                os.path.basename(resolved_path),
            )
            return skill
        except Exception as exc:
            logger.error("SkillLoader: hot-load failed for %s: %s", file_path, exc)
            return None

    def load_all_active(self) -> list[BaseSkill]:
        """Load all DB-registered active generated skills on startup."""
        loaded: list[BaseSkill] = []
        for row in self._get_active_skills():
            try:
                file_path = _safe_filepath(row["file_name"])
            except ValueError as exc:
                logger.error("SkillLoader: invalid generated file path for %s: %s", row["file_name"], exc)
                self.disable_skill(row["skill_name"])
                continue

            if not os.path.exists(file_path):
                logger.warning(
                    "SkillLoader: missing generated skill file %s; disabling registry entry.",
                    file_path,
                )
                self.disable_skill(row["skill_name"])
                continue

            skill = self.hot_load(file_path, register=False)
            if skill is not None:
                loaded.append(skill)

        logger.info("SkillLoader: loaded %s active generated skill(s).", len(loaded))
        return loaded

    def register_skill(
        self,
        skill_name: str,
        class_name: str,
        file_name: str,
        capability_description: str,
        trigger_gap: str,
        bus_events: list[str],
        dependencies: list[str],
        validation_passed: bool = True,
        notes: str = "",
    ) -> int:
        """Persist a generated skill in the SQLite registry."""
        if _conn is None or _lock is None:
            raise RuntimeError("SkillLoader: database layer unavailable.")

        with _lock, _conn() as conn:
            conn.execute(
                """
                INSERT INTO synthesised_skills (
                    skill_name, class_name, file_name, capability_description,
                    trigger_gap, bus_events, dependencies, validation_passed,
                    last_loaded, active, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    class_name = excluded.class_name,
                    file_name = excluded.file_name,
                    capability_description = excluded.capability_description,
                    trigger_gap = excluded.trigger_gap,
                    bus_events = excluded.bus_events,
                    dependencies = excluded.dependencies,
                    validation_passed = excluded.validation_passed,
                    last_loaded = excluded.last_loaded,
                    active = 1,
                    notes = excluded.notes,
                    version = synthesised_skills.version + 1
                """,
                (
                    skill_name,
                    class_name,
                    os.path.basename(file_name),
                    capability_description,
                    trigger_gap,
                    json.dumps(bus_events),
                    json.dumps(dependencies),
                    1 if validation_passed else 0,
                    datetime.now().isoformat(),
                    notes,
                ),
            )
            row = conn.execute(
                "SELECT id FROM synthesised_skills WHERE skill_name = ?",
                (skill_name,),
            ).fetchone()
            return int(row["id"]) if row else 0

    def get_all_skills(self) -> list[dict]:
        """Return all registered generated skills."""
        if _conn is None or _lock is None:
            return []
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synthesised_skills ORDER BY synthesis_date DESC"
            ).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                item["bus_events"] = json.loads(item.get("bus_events") or "[]")
                item["dependencies"] = json.loads(item.get("dependencies") or "[]")
                result.append(item)
            return result

    def get_known_capabilities(self) -> list[str]:
        """Return plain-English descriptions of active generated capabilities."""
        if _conn is None or _lock is None:
            return []
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT capability_description FROM synthesised_skills WHERE active = 1"
            ).fetchall()
            return [str(row["capability_description"]) for row in rows]

    def disable_skill(self, skill_name: str) -> None:
        """Mark a generated skill inactive in the registry."""
        if _conn is None or _lock is None:
            return
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE synthesised_skills SET active = 0 WHERE skill_name = ?",
                (skill_name,),
            )
        logger.info("SkillLoader: disabled generated skill %s", skill_name)

    def _get_active_skills(self) -> list[dict]:
        if _conn is None or _lock is None:
            return []
        with _lock, _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synthesised_skills WHERE active = 1"
            ).fetchall()
            return [dict(row) for row in rows]

    def _update_last_loaded(self, file_name: str) -> None:
        if _conn is None or _lock is None:
            return
        with _lock, _conn() as conn:
            conn.execute(
                "UPDATE synthesised_skills SET last_loaded = ? WHERE file_name = ?",
                (datetime.now().isoformat(), os.path.basename(file_name)),
            )

    @staticmethod
    def check_package_on_pypi(package_name: str) -> bool:
        """Best-effort check that a package exists on PyPI."""
        try:
            import requests

            response = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    @staticmethod
    def install_package(package_name: str) -> tuple[bool, str]:
        """Install a dependency into the current Python environment."""
        safe_name = re.sub(r"[^a-zA-Z0-9_.<>=\\-\\[\\],]", "", package_name or "")
        if not safe_name:
            return False, "Package name was empty after sanitization."
        if safe_name != package_name:
            return False, f"Package name '{package_name}' contains unsafe characters."

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", safe_name],
                capture_output=True,
                text=True,
                timeout=120,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"pip install timed out for '{safe_name}'"
        except Exception as exc:
            return False, str(exc)

        if result.returncode == 0:
            logger.info("SkillLoader: installed package %s", safe_name)
            output = (result.stdout or "").strip() or f"Installed {safe_name} successfully."
            return True, output[-300:]

        error_output = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        logger.error("SkillLoader: pip install failed for %s: %s", safe_name, error_output[:300])
        return False, error_output[:300]
