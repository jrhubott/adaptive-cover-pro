"""Base class for override handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..const import ReasonCode
from ..reason_i18n import Reason
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

    def contribute(self, snapshot: PipelineSnapshot) -> dict[str, Any]:  # noqa: ARG002
        """Expose optional data regardless of whether evaluate() returned a result.

        Override to publish fields (e.g. ``climate_data``) that should appear
        on the final PipelineResult even when this handler does not win the
        position.  Returned keys must match PipelineResult attribute names.
        The registry fills only None fields on the winner — winner values always
        take precedence.  Default returns {} (opt-in).
        """
        return {}

    def describe_skip(self, snapshot: PipelineSnapshot) -> str | Reason:  # noqa: ARG002
        """Reason when this handler does not match.

        May return either a legacy English ``str`` (subclass overrides not yet
        migrated) or a stable :class:`Reason` payload. The registry normalizes
        both — see ``PipelineRegistry.evaluate`` — so handlers migrate to codes
        one batch at a time (issue #882).
        """
        return Reason(ReasonCode.SKIP_NOT_ACTIVE)
