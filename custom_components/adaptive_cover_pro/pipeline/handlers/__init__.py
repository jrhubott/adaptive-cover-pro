"""Built-in override handlers for the pipeline, plus the handler registry.

Adding a handler is a one-place change: write the handler module, import it
here, and add one entry to ``HANDLER_FACTORIES``. The coordinator builds the
pipeline via :func:`build_handlers` and never needs editing — priority remains
the handler's class attribute, so ``PipelineRegistry`` sorts the chain.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...const import (
    CONF_ENABLE_SUN_TRACKING,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from ...helpers import (
    custom_position_slot_configured,
)
from ..handler import OverrideHandler
from .climate import ClimateHandler
from .cloud_suppression import CloudSuppressionHandler
from .custom_position import CustomPositionHandler
from .default import DefaultHandler
from .glare_zone import GlareZoneHandler
from .manual_override import ManualOverrideHandler
from .motion_timeout import MotionTimeoutHandler
from .solar import SolarHandler
from .weather import WeatherOverrideHandler

# A factory turns the live options into zero or more handler instances. Most
# handlers are always-on singletons; a couple are config-driven (custom-position
# slots, sun-tracking toggle).
HandlerFactory = Callable[[Mapping[str, Any]], list[OverrideHandler]]


def _single(cls: type[OverrideHandler]) -> HandlerFactory:
    """Return a factory that always emits exactly one ``cls()``."""
    return lambda _options: [cls()]


def _custom_position_handlers(options: Mapping[str, Any]) -> list[OverrideHandler]:
    """Build one ``CustomPositionHandler`` per configured + enabled slot.

    A slot contributes a handler only when it has a trigger (sensors and/or
    template) and a position and is enabled. Each carries an independent
    priority so the registry sorts it into the correct evaluation order
    alongside the rest of the chain.
    """
    handlers: list[OverrideHandler] = []
    for slot, slot_keys in CUSTOM_POSITION_SLOTS.items():
        enabled = bool(
            options.get(slot_keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED)
        )
        if custom_position_slot_configured(options, slot_keys) and enabled:
            priority = int(
                options.get(slot_keys["priority"]) or DEFAULT_CUSTOM_POSITION_PRIORITY
            )
            raw_tilt = options.get(slot_keys["tilt"])
            tilt = int(raw_tilt) if raw_tilt is not None else None
            handlers.append(
                CustomPositionHandler(
                    slot=slot,
                    position=int(options.get(slot_keys["position"])),
                    priority=priority,
                    tilt=tilt,
                )
            )
    return handlers


def _solar_handler(options: Mapping[str, Any]) -> list[OverrideHandler]:
    """Emit the ``SolarHandler`` only when sun tracking is enabled."""
    if options.get(CONF_ENABLE_SUN_TRACKING, True):
        return [SolarHandler()]
    return []


# The registry. Order here is for readability only — PipelineRegistry sorts by
# each handler's declared priority. Add a new handler with one entry.
HANDLER_FACTORIES: tuple[HandlerFactory, ...] = (
    _single(WeatherOverrideHandler),
    _single(ManualOverrideHandler),
    _custom_position_handlers,
    _single(MotionTimeoutHandler),
    _single(CloudSuppressionHandler),
    _single(ClimateHandler),
    _single(GlareZoneHandler),
    _solar_handler,
    _single(DefaultHandler),
)


def build_handlers(options: Mapping[str, Any]) -> list[OverrideHandler]:
    """Build every configured pipeline handler from ``options``.

    Iterates the registry and flattens each factory's output. The result is
    handed to ``PipelineRegistry``, which sorts by priority.
    """
    handlers: list[OverrideHandler] = []
    for factory in HANDLER_FACTORIES:
        handlers.extend(factory(options))
    return handlers


__all__ = [
    "HANDLER_FACTORIES",
    "ClimateHandler",
    "CloudSuppressionHandler",
    "CustomPositionHandler",
    "DefaultHandler",
    "GlareZoneHandler",
    "HandlerFactory",
    "ManualOverrideHandler",
    "MotionTimeoutHandler",
    "SolarHandler",
    "WeatherOverrideHandler",
    "build_handlers",
]
