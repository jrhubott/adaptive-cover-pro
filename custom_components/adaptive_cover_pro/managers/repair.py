"""Config-coherence Repair manager (issue #975).

Sibling to :class:`~.sensor_health.SensorHealthManager`: same debounced
raise/clear lifecycle (shared via
:class:`~.common.debounced_repair._DebouncedRepairBase`), but driven by a
caller-computed boolean rather than a live entity poll. Config predicates —
"is the min/max position envelope inverted?", "is the start time after the end
time?" — have no entity to watch, so the coordinator evaluates the predicate
each cycle and pushes the verdict via :meth:`update_predicate`.

The manager stores the latest verdict per issue key and re-reads it at debounce
expiry, so a multi-step options edit that transiently looks incoherent settles
before nagging, and a genuine fix mid-debounce suppresses the Repair entirely.

Side-effect ownership: a manager (per-instance predicate/timer state, Repair
lifecycle). It computes nothing itself — the coordinator owns the predicate
logic and reads config generically (no cover-type branching).
"""

from __future__ import annotations

from dataclasses import dataclass

from .common.debounced_repair import _DebouncedRepairBase


@dataclass(slots=True, frozen=True)
class _Predicate:
    """One config predicate's latest verdict and the Repair metadata to raise."""

    unhealthy: bool
    translation_key: str
    placeholders: dict[str, str] | None


class RepairManager(_DebouncedRepairBase):
    """Raise/clear informational Repairs from caller-computed config predicates."""

    def __init__(self, *args, **kwargs) -> None:
        """Bind the shared lifecycle and initialise the predicate registry."""
        super().__init__(*args, **kwargs)
        self._predicates: dict[str, _Predicate] = {}

    # -- registration -------------------------------------------------------

    def update_predicate(
        self,
        issue_key: str,
        unhealthy: bool,
        *,
        translation_key: str,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Store this cycle's verdict for ``issue_key``.

        ``unhealthy`` True means the config is incoherent — raise (after
        debounce); False means coherent — clear any pending timer or Repair.
        """
        self._predicates[issue_key] = _Predicate(
            unhealthy=unhealthy,
            translation_key=translation_key,
            placeholders=placeholders,
        )

    # -- per-cycle evaluation ----------------------------------------------

    def evaluate(self) -> None:
        """Re-drive the lifecycle for every stored predicate once per cycle."""
        for issue_key, predicate in list(self._predicates.items()):
            if not predicate.unhealthy:
                self._recover(issue_key)
            else:
                self._schedule(
                    issue_key,
                    predicate.translation_key,
                    predicate.placeholders or {},
                    still_unhealthy=lambda k=issue_key: self._predicates[k].unhealthy,
                )
