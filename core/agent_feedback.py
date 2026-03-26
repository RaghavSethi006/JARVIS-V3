"""Utilities for capturing tool feedback during multi-agent execution."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ToolFeedbackCapture:
    """Stores intercepted tool feedback emitted during agent task execution."""

    messages: list[str] = field(default_factory=list)

    def record(self, message: object) -> None:
        """Record a non-empty feedback message."""
        text = str(message or "").strip()
        if text:
            self.messages.append(text)


_TOOL_FEEDBACK_CAPTURE: ContextVar[ToolFeedbackCapture | None] = ContextVar(
    "tool_feedback_capture",
    default=None,
)
_TOOL_FEEDBACK_SUPPRESSED: ContextVar[bool] = ContextVar(
    "tool_feedback_suppressed",
    default=False,
)


@contextmanager
def capture_tool_feedback() -> Iterator[ToolFeedbackCapture]:
    """Capture tool feedback while suppressing direct UI/TTS emissions."""
    capture = ToolFeedbackCapture()
    capture_token = _TOOL_FEEDBACK_CAPTURE.set(capture)
    suppress_token = _TOOL_FEEDBACK_SUPPRESSED.set(True)
    try:
        yield capture
    finally:
        _TOOL_FEEDBACK_SUPPRESSED.reset(suppress_token)
        _TOOL_FEEDBACK_CAPTURE.reset(capture_token)


def is_tool_feedback_suppressed() -> bool:
    """Return whether tool feedback should be suppressed for the current context."""
    return _TOOL_FEEDBACK_SUPPRESSED.get()


def record_tool_feedback(message: object) -> None:
    """Append a message to the current feedback capture, if one exists."""
    capture = _TOOL_FEEDBACK_CAPTURE.get()
    if capture is not None:
        capture.record(message)
