"""Base class for override handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import PipelineResult, PipelineSnapshot


class OverrideHandler(ABC):
    """Abstract base class for pipeline handlers.

    Subclasses must set ``name`` and ``priority`` as class attributes
    and implement ``evaluate()``.

    Priority is evaluated highest-first; the first handler that returns
    a non-None result wins.
    """

    name: str
    priority: int

    @abstractmethod
    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return PipelineResult to claim position, or None to pass."""

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason string when this handler does not match."""
        return "not active"
