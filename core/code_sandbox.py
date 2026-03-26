"""Safety validation for LLM-generated Jarvis skill code."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field

try:
    from core.logger import logger
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("code_sandbox: core.logger import failed. Using fallback logger.")


GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "jarvis-generated-code",
)

BLOCKED_CALLS = {
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "builtins.exec",
    "builtins.eval",
    "builtins.compile",
    "__import__",
}
BLOCKED_NAMES = {"exec", "eval", "compile", "__import__"}
BLOCKED_IMPORTS = {"subprocess"}
SENSITIVE_IMPORTS = {"socket", "ctypes"}
WRITE_MODES = {"w", "a", "x", "+"}


@dataclass
class SandboxResult:
    """Result of AST and subprocess validation."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _ASTScanner(ast.NodeVisitor):
    """Walk generated code AST and record unsafe patterns."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._generated_base = os.path.realpath(GENERATED_DIR)

    def visit_Call(self, node: ast.Call) -> None:
        call_str = self._call_to_str(node)
        if call_str in BLOCKED_CALLS:
            self.errors.append(f"Line {node.lineno}: blocked call `{call_str}()`")
        if call_str == "open":
            self._check_open_call(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_NAMES:
            self.errors.append(f"Line {node.lineno}: blocked built-in `{node.id}`")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_level = alias.name.split(".", 1)[0]
            if top_level in BLOCKED_IMPORTS:
                self.errors.append(f"Line {node.lineno}: blocked import `{alias.name}`")
            elif top_level in SENSITIVE_IMPORTS:
                self.warnings.append(f"Line {node.lineno}: sensitive import `{alias.name}`")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = (node.module or "").split(".", 1)[0]
        if module_name in BLOCKED_IMPORTS:
            self.errors.append(f"Line {node.lineno}: blocked import-from `{node.module}`")
        elif module_name in SENSITIVE_IMPORTS:
            self.warnings.append(f"Line {node.lineno}: sensitive import-from `{node.module}`")
        self.generic_visit(node)

    def _call_to_str(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        if isinstance(node.func, ast.Name):
            return node.func.id
        return ""

    def _check_open_call(self, node: ast.Call) -> None:
        """Allow writes only when the destination is a constant path within GENERATED_DIR."""
        if not self._is_write_call(node):
            return
        if not node.args:
            self.errors.append(f"Line {node.lineno}: open() write without file path")
            return

        file_arg = node.args[0]
        if not isinstance(file_arg, ast.Constant) or not isinstance(file_arg.value, str):
            self.errors.append(
                f"Line {node.lineno}: dynamic file writes are not allowed in generated code"
            )
            return

        target = os.path.realpath(os.path.abspath(file_arg.value))
        allowed_prefix = self._generated_base + os.sep
        if not (target == self._generated_base or target.startswith(allowed_prefix)):
            self.errors.append(
                f"Line {node.lineno}: file write outside jarvis-generated-code/: {file_arg.value}"
            )

    def _is_write_call(self, node: ast.Call) -> bool:
        mode = "r"
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            if isinstance(node.args[1].value, str):
                mode = node.args[1].value
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    mode = keyword.value.value
        return any(flag in mode for flag in WRITE_MODES)


def ast_scan(code: str) -> SandboxResult:
    """Run AST validation without executing code."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return SandboxResult(passed=False, errors=[f"Syntax error: {exc}"])

    scanner = _ASTScanner()
    scanner.visit(tree)
    passed = not scanner.errors
    if passed:
        logger.info("CodeSandbox: AST scan passed.")
    else:
        logger.warning("CodeSandbox: AST scan failed: %s", scanner.errors)
    return SandboxResult(passed=passed, errors=scanner.errors, warnings=scanner.warnings)


_SANDBOX_HARNESS = textwrap.dedent(
    """
    import importlib.util
    import inspect
    import json
    import sys

    import socket as _socket

    def _blocked_socket(*args, **kwargs):
        raise RuntimeError("Network access blocked in sandbox")

    _socket.socket = _blocked_socket

    sys.path.insert(0, {project_root!r})

    try:
        from interfaces.skill import BaseSkill
        spec = importlib.util.spec_from_file_location("generated_skill", {file_path!r})
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not create import spec for generated skill")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        skill_classes = [
            obj for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, BaseSkill) and obj is not BaseSkill
        ]
        if not skill_classes:
            raise RuntimeError("No BaseSkill subclass found in generated code")

        class MockBus:
            def subscribe(self, *args, **kwargs):
                return None

            async def emit(self, *args, **kwargs):
                return None

        skill_class = skill_classes[0]
        instance = skill_class(MockBus())
        instance.register()
        print(json.dumps({{"passed": True, "class_name": skill_class.__name__, "error": None}}))
    except Exception as exc:
        print(json.dumps({{"passed": False, "class_name": None, "error": str(exc)}}))
        raise
    """
)


def subprocess_sandbox(file_path: str, timeout: int = 15) -> SandboxResult:
    """Run generated code in a subprocess with network disabled."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    harness = _SANDBOX_HARNESS.format(project_root=project_root, file_path=file_path)

    try:
        result = subprocess.run(
            [sys.executable, "-c", harness],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(passed=False, errors=[f"Sandbox timeout after {timeout}s"])
    except Exception as exc:
        return SandboxResult(passed=False, errors=[f"Sandbox execution failed: {exc}"])

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stderr:
        logger.debug("CodeSandbox stderr: %s", stderr[:500])
    if not stdout:
        return SandboxResult(passed=False, errors=["Sandbox produced no output"])

    last_line = stdout.splitlines()[-1]
    try:
        payload = json.loads(last_line)
    except json.JSONDecodeError:
        return SandboxResult(
            passed=False,
            errors=[f"Sandbox output was not valid JSON: {last_line[:200]}"],
        )

    passed = bool(payload.get("passed"))
    error_text = str(payload.get("error") or "").strip()
    errors = [error_text] if error_text else []
    if passed:
        logger.info(
            "CodeSandbox: subprocess validation passed for %s",
            payload.get("class_name"),
        )
    else:
        logger.warning("CodeSandbox: subprocess validation failed: %s", errors)
    return SandboxResult(passed=passed, errors=errors)


def validate(code: str, file_path: str) -> SandboxResult:
    """Run both AST and subprocess validation stages."""
    stage_one = ast_scan(code)
    if not stage_one.passed:
        return SandboxResult(
            passed=False,
            errors=["STAGE 1 (AST) FAILED - code not executed", *stage_one.errors],
            warnings=stage_one.warnings,
        )

    stage_two = subprocess_sandbox(file_path)
    return SandboxResult(
        passed=stage_two.passed,
        errors=(["STAGE 2 (Subprocess) FAILED"] + stage_two.errors) if not stage_two.passed else [],
        warnings=[*stage_one.warnings, *stage_two.warnings],
    )
