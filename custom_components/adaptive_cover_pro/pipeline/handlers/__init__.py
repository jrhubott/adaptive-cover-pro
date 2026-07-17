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
    CONF_CLIMATE_PRIORITY,
    CONF_CLOUD_SUPPRESSION_PRIORITY,
    CONF_ENABLE_SUN_TRACKING,
    CONF_GLARE_ZONE_PRIORITY,
    CONF_MANUAL_OVERRIDE_PRIORITY,
    CONF_MOTION_TIMEOUT_PRIORITY,
    CONF_SOLAR_PRIORITY,
    CONF_WEATHER_PRIORITY,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    _RANGE_HANDLER_PRIORITY,
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
from .group_lock import GroupLockHandler
from .group_scene import GroupSceneHandler
from .manual_override import ManualOverrideHandler
from .motion_timeout import MotionTimeoutHandler
from .solar import SolarHandler
from .weather import WeatherOverrideHandler

# A factory turns the live options into zero or more handler instances. Most
# handlers are always-on singletons; a couple are config-driven (custom-position
# slots, sun-tracking toggle).
HandlerFactory = Callable[[Mapping[str, Any]], list[OverrideHandler]]

# The seven built-in handlers whose priority the user may override, mapped to the
# option key carrying that override. ``default`` is excluded — it is the chain
# floor (priority 0) and stays fixed. Custom-position slots own their priority via
# CUSTOM_POSITION_SLOTS and are handled separately.
HANDLER_PRIORITY_CONF: dict[str, str] = {
    WeatherOverrideHandler.name: CONF_WEATHER_PRIORITY,
    ManualOverrideHandler.name: CONF_MANUAL_OVERRIDE_PRIORITY,
    MotionTimeoutHandler.name: CONF_MOTION_TIMEOUT_PRIORITY,
    CloudSuppressionHandler.name: CONF_CLOUD_SUPPRESSION_PRIORITY,
    ClimateHandler.name: CONF_CLIMATE_PRIORITY,
    GlareZoneHandler.name: CONF_GLARE_ZONE_PRIORITY,
    SolarHandler.name: CONF_SOLAR_PRIORITY,
}

# Each handler's class-default priority — the single source of truth lives on the
# class, so the fallback is read from there rather than re-typed here.
HANDLER_PRIORITY_DEFAULTS: dict[str, int] = {
    cls.name: cls.priority
    for cls in (
        WeatherOverrideHandler,
        ManualOverrideHandler,
        MotionTimeoutHandler,
        CloudSuppressionHandler,
        ClimateHandler,
        GlareZoneHandler,
        SolarHandler,
    )
}

_PRIORITY_MIN, _PRIORITY_MAX = _RANGE_HANDLER_PRIORITY


def resolve_handler_priority(options: Mapping[str, Any], name: str) -> int:
    """Effective priority for a built-in handler.

    Returns the user-configured override (clamped to the valid range) when the
    option is set, otherwise the handler's class-default priority. Both the
    runtime build path and the config-flow priority-ladder visualization call
    this so the order shown always matches the order evaluated.
    """
    raw = options.get(HANDLER_PRIORITY_CONF[name])
    if raw is None:
        return HANDLER_PRIORITY_DEFAULTS[name]
    return max(_PRIORITY_MIN, min(_PRIORITY_MAX, int(raw)))


def _single(cls: type[OverrideHandler]) -> HandlerFactory:
    """Return a factory that always emits exactly one ``cls()``."""
    return lambda _options: [cls()]


def _custom_position_handlers(options: Mapping[str, Any]) -> list[OverrideHandler]:
    """Build one ``CustomPositionHandler`` per configured + enabled slot.

    A slot contributes a handler only when it has a trigger (sensors and/or
    template) and an axis claim — a position or one of the axis constraints —
    and is enabled. Each carries an independent priority so the registry sorts
    it into the correct evaluation order alongside the rest of the chain.
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
            # A constraint-only slot (e.g. trigger → minimum tilt) has no
            # position claim. Pass None rather than a 0 sentinel: the ``use_my``
            # path bypasses the non-FIXED deferral, so a 0 here would fully
            # close the cover when the My value is also unavailable (audit
            # finding 3). The handler defers instead when it has nothing to send.
            raw_position = options.get(slot_keys["position"])
            handlers.append(
                CustomPositionHandler(
                    slot=slot,
                    position=int(raw_position) if raw_position is not None else None,
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
    # Group handlers LAST (issue #790 Phase 2): the registry's priority sort
    # is stable, so on a 100-tie a member's own custom-position safety slot
    # (built above) wins over the group lock — physical safety local to the
    # cover trumps a zone command. Always built; they defer when no intent.
    _single(GroupSceneHandler),
    _single(GroupLockHandler),
)


def build_handlers(options: Mapping[str, Any]) -> list[OverrideHandler]:
    """Build every configured pipeline handler from ``options``.

    Iterates the registry and flattens each factory's output, then applies any
    per-handler priority overrides. The result is handed to ``PipelineRegistry``,
    which sorts by priority.
    """
    handlers: list[OverrideHandler] = []
    for factory in HANDLER_FACTORIES:
        handlers.extend(factory(options))
    # Apply configured priority overrides. Setting the instance attribute shadows
    # the class default, exactly as CustomPositionHandler does in its __init__.
    # Handlers absent from the map (default, custom slots) keep their own priority.
    for handler in handlers:
        if handler.name in HANDLER_PRIORITY_CONF:
            handler.priority = resolve_handler_priority(options, handler.name)
    return handlers


__all__ = [
    "HANDLER_FACTORIES",
    "HANDLER_PRIORITY_CONF",
    "HANDLER_PRIORITY_DEFAULTS",
    "ClimateHandler",
    "CloudSuppressionHandler",
    "CustomPositionHandler",
    "DefaultHandler",
    "GlareZoneHandler",
    "GroupLockHandler",
    "GroupSceneHandler",
    "HandlerFactory",
    "ManualOverrideHandler",
    "MotionTimeoutHandler",
    "SolarHandler",
    "WeatherOverrideHandler",
    "build_handlers",
    "resolve_handler_priority",
]
