"""Generic sensor-health Repair manager (issue #786).

Watches a registry of ``{issue_key -> entity_id}`` and raises an informational
Home Assistant Repair when a watched sensor stays unavailable (or missing from
the state machine) past a debounce window — then clears the Repair once it
recovers. The debounce is generous so integration restarts and device re-adds
do not nag the user before a genuinely dead sensor is flagged.

The manager is deliberately entity-agnostic: wiring a new sensor is a single
``update_watch`` call with its own ``issue_key`` and ``translation_key`` — no
per-sensor branching here (no-duplication rule). It now wires the effective
indoor temperature entity, the controlled covers, and ``sun.sun``.

The debounce/raise/clear/shutdown machinery lives on the shared
:class:`~.common.debounced_repair._DebouncedRepairBase` so the config-predicate
sibling (:class:`~.repair.RepairManager`) reuses the exact same lifecycle
rather than copying it.

Side-effect ownership: this is a manager (it holds per-instance state and
orchestrates the Repair lifecycle). The resolution *read* stays in the
``state/`` boundary (``AreaSensorResolver``); the manager only reacts to the
already-resolved effective entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..const import DEFAULT_SENSOR_HEALTH_DEBOUNCE_SECONDS
from .common.debounced_repair import _DebouncedRepairBase

if TYPE_CHECKING:
    from logging import Logger

    from homeassistant.core import HomeAssistant

# HA state strings that count as "no real value" for a watched entity.
_UNHEALTHY_STATES = ("unavailable", "unknown", None)


@dataclass(frozen=True, slots=True)
class _Watch:
    """One watched entity and the Repair metadata to raise for it."""

    entity_id: str
    translation_key: str
    placeholders: dict[str, str] = field(default_factory=dict)


class SensorHealthManager(_DebouncedRepairBase):
    """Raise/clear informational Repairs for unhealthy watched sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: Logger,
        *,
        domain: str,
        debounce_seconds: float = DEFAULT_SENSOR_HEALTH_DEBOUNCE_SECONDS,
    ) -> None:
        """Bind the manager to hass and the integration domain."""
        super().__init__(hass, logger, domain=domain, debounce_seconds=debounce_seconds)
        self._watched: dict[str, _Watch] = {}

    # -- registration -------------------------------------------------------

    def update_watch(
        self,
        issue_key: str,
        entity_id: str | None,
        *,
        translation_key: str,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Register / replace / clear the entity watched under ``issue_key``.

        ``entity_id`` of ``None`` unwatches the key and clears any pending timer
        or active Repair — the effective sensor became unset.
        """
        if not entity_id:
            self._unwatch(issue_key)
            return
        self._watched[issue_key] = _Watch(
            entity_id=entity_id,
            translation_key=translation_key,
            placeholders=dict(placeholders or {}),
        )

    # -- per-cycle evaluation ----------------------------------------------

    def evaluate(self) -> None:
        """Re-check every watched entity's health once per update cycle."""
        for issue_key, watch in list(self._watched.items()):
            if self._is_healthy(watch.entity_id):
                self._recover(issue_key)
            else:
                self._schedule(
                    issue_key,
                    watch.translation_key,
                    watch.placeholders,
                    still_unhealthy=lambda w=watch: not self._is_healthy(w.entity_id),
                )

    def _is_healthy(self, entity_id: str) -> bool:
        """Return True iff the watched entity has a real (non-null) state."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return False
        return state.state not in _UNHEALTHY_STATES

    def _unwatch(self, issue_key: str) -> None:
        """Stop watching a key and clear its timer + Repair."""
        self._watched.pop(issue_key, None)
        self._cancel_timer(issue_key)
        self._delete_issue(issue_key)
