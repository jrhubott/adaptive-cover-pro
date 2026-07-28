"""Shared debounce/raise/clear lifecycle for informational Repairs.

Both ``SensorHealthManager`` (entity-availability watches) and
``RepairManager`` (config-coherence predicates) need the same machinery: a
per-key debounce timer so transient conditions don't nag, a re-check at expiry
so a condition that recovered mid-debounce never raises, and raise/clear of an
informational Home Assistant Repair via the issue registry.

This base owns that machinery in one place (no-duplication rule). The one
entity-specific line in the original ``SensorHealthManager._raise`` — the
"still unhealthy at expiry?" re-check — is parameterized as a per-key
``still_unhealthy`` callable the subclass supplies at ``_schedule`` time, so
the availability watcher can poll a live entity while the predicate manager can
read a stored boolean.

Side-effect ownership: this is manager infrastructure — it holds per-instance
timer/active state and orchestrates the Repair lifecycle. Subclasses decide
*what* is unhealthy; the base decides *how* the Repair is debounced and raised.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from ...const import DEFAULT_SENSOR_HEALTH_DEBOUNCE_SECONDS
from .timeout_controller import TimeoutController

if TYPE_CHECKING:
    from logging import Logger

    from homeassistant.core import HomeAssistant


class _DebouncedRepairBase:
    """Debounce, re-check, and raise/clear informational Repairs per issue key."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: Logger,
        *,
        domain: str,
        debounce_seconds: float = DEFAULT_SENSOR_HEALTH_DEBOUNCE_SECONDS,
    ) -> None:
        """Bind the manager to hass, the integration domain, and a debounce."""
        self._hass = hass
        self._logger = logger
        self._domain = domain
        self._debounce = debounce_seconds
        self._timers: dict[str, TimeoutController] = {}
        self._active: set[str] = set()
        # Keys whose registry state this manager lifetime has already reconciled.
        # The primary fix path (options flow) reloads the config entry, so a
        # Repair raised in a prior lifetime is invisible to this instance's
        # ``_active`` set. The first healthy clear for a key therefore attempts a
        # delete unconditionally (idempotent) to sweep any orphan, then records
        # the key here so subsequent healthy cycles skip the redundant no-op.
        self._reconciled: set[str] = set()

    # -- lifecycle ----------------------------------------------------------

    def _schedule(
        self,
        issue_key: str,
        translation_key: str,
        placeholders: dict[str, str],
        *,
        still_unhealthy: Callable[[], bool],
    ) -> None:
        """Start (once) the debounce timer that will raise the Repair.

        ``still_unhealthy`` is re-evaluated at expiry so a condition that
        recovered during the debounce window never raises.
        """
        if issue_key in self._active:
            return  # already raised — nothing to debounce
        timer = self._timers.get(issue_key)
        if timer is not None and timer.is_running:
            return  # debounce already in flight
        timer = TimeoutController(
            self._logger, label=f"debounced repair {issue_key}", hass=self._hass
        )
        self._timers[issue_key] = timer
        timer.start(
            self._debounce,
            lambda: self._raise(
                issue_key, translation_key, placeholders, still_unhealthy
            ),
        )

    async def _raise(
        self,
        issue_key: str,
        translation_key: str,
        placeholders: dict[str, str],
        still_unhealthy: Callable[[], bool],
    ) -> None:
        """Raise the Repair — but only if still unhealthy at expiry."""
        if not still_unhealthy():
            return
        ir.async_create_issue(
            self._hass,
            self._domain,
            issue_key,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )
        self._active.add(issue_key)
        self._reconciled.add(issue_key)

    def _recover(self, issue_key: str) -> None:
        """Cancel any pending debounce and clear an active Repair."""
        self._cancel_timer(issue_key)
        self._delete_issue(issue_key)

    def _cancel_timer(self, issue_key: str) -> None:
        timer = self._timers.pop(issue_key, None)
        if timer is not None:
            timer.cancel()

    def _delete_issue(self, issue_key: str) -> None:
        # Clear when we know it is active, OR on the first healthy pass of this
        # lifetime to sweep a stale orphan left by a prior lifetime (config-entry
        # reload dropped the ``_active`` set). ``async_delete_issue`` is a no-op
        # when the issue is absent, so the extra first-pass call is harmless.
        if issue_key in self._active or issue_key not in self._reconciled:
            ir.async_delete_issue(self._hass, self._domain, issue_key)
            self._active.discard(issue_key)
            self._reconciled.add(issue_key)

    def shutdown(self) -> None:
        """Cancel all in-flight debounce timers (on reload / unload)."""
        for issue_key in list(self._timers):
            self._cancel_timer(issue_key)
