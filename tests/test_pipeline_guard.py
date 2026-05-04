"""Pipeline guard tests.

These tests are a safety net: changing a handler's priority, adding a new
handler with a conflicting priority, or removing a handler will cause these
tests to fail immediately.

When you change a handler's priority, also update:
  1. _EXPECTED_PRIORITIES below
  2. _build_config_summary() in config_flow.py (priority badge numbers)
  3. README.md pipeline chain prose and mermaid diagram
"""

from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    CloudSuppressionHandler,
    DefaultHandler,
    ForceOverrideHandler,
    GlareZoneHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
    WeatherOverrideHandler,
)

# Canonical priority map. Update here (and config_flow.py + README) when a
# priority changes. CustomPositionHandler is excluded — its priority is
# user-configurable (1–99) and cannot be locked to a single value.
_EXPECTED_PRIORITIES: dict[type, int] = {
    ForceOverrideHandler: 100,
    WeatherOverrideHandler: 90,
    ManualOverrideHandler: 80,
    MotionTimeoutHandler: 75,
    CloudSuppressionHandler: 60,
    ClimateHandler: 50,
    GlareZoneHandler: 45,
    SolarHandler: 40,
    DefaultHandler: 0,
}


class TestPipelineGuard:
    """Structural integrity tests for pipeline handler priorities."""

    def test_no_duplicate_priorities(self) -> None:
        """No two fixed handlers may share a priority value.

        Duplicate priorities make evaluation order ambiguous. Fails when a new
        handler is added with a value already claimed by an existing one.
        """
        priorities = list(_EXPECTED_PRIORITIES.values())
        assert len(priorities) == len(set(priorities)), (
            f"Duplicate priorities in _EXPECTED_PRIORITIES: {sorted(priorities)}\n"
            "Each fixed handler must have a unique priority value."
        )

    def test_handler_priorities_match_expected(self) -> None:
        """Every handler's class attribute must match the canonical map.

        Fails when a handler's priority is changed without updating this file.
        When you change a priority, update _EXPECTED_PRIORITIES here AND update
        _build_config_summary() in config_flow.py and the README mermaid diagram.
        """
        for cls, expected in _EXPECTED_PRIORITIES.items():
            assert cls.priority == expected, (
                f"{cls.__name__}.priority is {cls.priority!r}, expected {expected}.\n"
                "Update _EXPECTED_PRIORITIES in this file, config_flow.py badges, "
                "and README.md if this is intentional."
            )

    def test_all_fixed_handlers_in_expected_priorities(self) -> None:
        """Adding a new fixed handler without registering it here fails immediately.

        When you add a new handler with a fixed priority, add it to
        _EXPECTED_PRIORITIES. If a handler is removed, also remove it.
        """
        from custom_components.adaptive_cover_pro.pipeline.handlers import __all__
        from custom_components.adaptive_cover_pro.pipeline import handlers as _mod

        # All exported handler classes with a fixed integer `priority` attribute
        fixed_classes = {
            getattr(_mod, name)
            for name in __all__
            if hasattr(getattr(_mod, name, None), "priority")
            and isinstance(getattr(getattr(_mod, name), "priority", None), int)
        }
        # Exclude CustomPositionHandler — configurable priority, not lockable
        from custom_components.adaptive_cover_pro.pipeline.handlers import (
            CustomPositionHandler,
        )

        fixed_classes.discard(CustomPositionHandler)

        missing = fixed_classes - set(_EXPECTED_PRIORITIES.keys())
        extra = set(_EXPECTED_PRIORITIES.keys()) - fixed_classes
        assert not missing and not extra, (
            f"Handler mismatch in _EXPECTED_PRIORITIES:\n"
            f"  Exported but not registered here: {[c.__name__ for c in missing]}\n"
            f"  Registered here but not exported: {[c.__name__ for c in extra]}"
        )
