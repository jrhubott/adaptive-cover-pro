"""Tests for the shared priority-chain helper (build_priority_chain).

The helper is the single source of truth for the override-pipeline decision
chain: it imports each handler's declared ``priority`` class attribute (never a
hardcoded integer) and returns the entries ordered highest-priority-first. Both
the config-flow summary and the custom-slot priority visual consume it.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    CloudSuppressionHandler,
    DefaultHandler,
    GlareZoneHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.priority_chain import (
    PriorityChainEntry,
    build_priority_chain,
)


def _kwargs(**overrides):
    base = {
        "has_weather": False,
        "has_motion": False,
        "has_cloud": False,
        "has_climate": False,
        "sun_tracking_enabled": True,
        "has_glare": False,
        "supports_glare": False,
        "custom_slots": [],
    }
    base.update(overrides)
    return base


def test_anchor_priorities_come_from_handler_classes():
    """Each fixed anchor's priority equals its handler's class attribute."""
    chain = build_priority_chain(**_kwargs())
    by_label = {e.label: e.priority for e in chain}
    assert by_label["Weather"] == WeatherOverrideHandler.priority
    assert by_label["Manual"] == ManualOverrideHandler.priority
    assert by_label["Motion"] == MotionTimeoutHandler.priority
    assert by_label["Cloud"] == CloudSuppressionHandler.priority
    assert by_label["Climate"] == ClimateHandler.priority
    assert by_label["Solar"] == SolarHandler.priority
    assert by_label["Default"] == DefaultHandler.priority


def test_entries_sorted_highest_priority_first():
    chain = build_priority_chain(**_kwargs())
    priorities = [e.priority for e in chain]
    assert priorities == sorted(priorities, reverse=True)


def test_glare_absent_when_policy_does_not_support_it():
    chain = build_priority_chain(**_kwargs(supports_glare=False, has_glare=False))
    assert all(e.label != "Glare" for e in chain)


def test_glare_present_at_handler_priority_when_supported():
    chain = build_priority_chain(**_kwargs(supports_glare=True, has_glare=True))
    glare = next(e for e in chain if e.label == "Glare")
    assert glare.priority == GlareZoneHandler.priority
    assert glare.active is True


def test_active_flags_propagate():
    chain = build_priority_chain(**_kwargs(has_weather=True, has_cloud=True))
    active = {e.label: e.active for e in chain}
    assert active["Weather"] is True
    assert active["Cloud"] is True
    assert active["Motion"] is False
    # Manual and Default are always active.
    assert active["Manual"] is True
    assert active["Default"] is True


def test_custom_slot_interleaved_at_its_priority():
    # custom slot tuple matches the summary's shape:
    # (slot, trigger, position, priority, use_my, tilt, tilt_only)
    slots = [(1, "sensor.x", 50, 85, False, None, False)]
    chain = build_priority_chain(**_kwargs(custom_slots=slots))
    custom = next(e for e in chain if e.slot == 1)
    assert custom.priority == 85
    assert custom.active is True
    # It sorts between Manual (80) and Weather (90).
    labels = [e.label for e in chain]
    assert labels.index("Weather") < labels.index(custom.label) < labels.index("Manual")


def test_custom_slot_ties_break_after_fixed_handler():
    # A custom slot at exactly Motion's priority must render AFTER Motion
    # (stable order: fixed anchors are inserted before custom slots).
    slots = [(2, "sensor.y", 40, MotionTimeoutHandler.priority, False, None, False)]
    chain = build_priority_chain(**_kwargs(custom_slots=slots))
    labels = [e.label for e in chain]
    motion_idx = labels.index("Motion")
    custom_idx = next(i for i, e in enumerate(chain) if e.slot == 2)
    assert motion_idx < custom_idx


def test_custom_slot_uses_configured_name_when_provided():
    """A named slot's label is the configured name, not Custom#N (issue #910)."""
    slots = [(2, "sensor.x", 0, 75, False, None, False)]
    chain = build_priority_chain(
        **_kwargs(custom_slots=slots, slot_names={2: "Canicule"})
    )
    custom = next(e for e in chain if e.slot == 2)
    assert custom.label == "Canicule(75)"


def test_custom_slot_falls_back_to_default_label_when_name_absent():
    """A slot missing from slot_names still renders Custom#N (fallback)."""
    slots = [(2, "sensor.x", 0, 75, False, None, False)]
    chain = build_priority_chain(**_kwargs(custom_slots=slots, slot_names={}))
    custom = next(e for e in chain if e.slot == 2)
    assert custom.label == "Custom#2(75)"


def test_custom_slot_empty_or_missing_slot_names_mapping_falls_back():
    """slot_names=None (the default) must not change today's label behavior."""
    slots = [(2, "sensor.x", 0, 75, False, None, False)]
    chain = build_priority_chain(**_kwargs(custom_slots=slots))
    custom = next(e for e in chain if e.slot == 2)
    assert custom.label == "Custom#2(75)"


def test_entry_is_priority_chain_entry():
    chain = build_priority_chain(**_kwargs())
    assert all(isinstance(e, PriorityChainEntry) for e in chain)


# ---------------------------------------------------------------------------
# Phase D — the custom-slot priority-scale visual (#613).
# ---------------------------------------------------------------------------


def _blind_policy():
    from custom_components.adaptive_cover_pro.const import CoverType
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    return get_policy(CoverType.BLIND)


def test_priority_scale_lists_fixed_anchors():
    from custom_components.adaptive_cover_pro.config_flow import _render_priority_scale

    scale = _render_priority_scale({}, _blind_policy())
    for anchor in (
        "Weather",
        "Manual",
        "Motion",
        "Cloud",
        "Climate",
        "Solar",
        "Default",
    ):
        assert anchor in scale, f"{anchor} anchor missing from priority scale"
    # Anchor priorities are shown.
    assert "90" in scale and "80" in scale and "75" in scale


def test_priority_scale_marks_configured_custom_slot():
    from custom_components.adaptive_cover_pro.config_flow import _render_priority_scale
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS

    slot1 = CUSTOM_POSITION_SLOTS[1]
    config = {
        slot1["sensors"]: ["binary_sensor.x"],
        slot1["position"]: 40,
        slot1["priority"]: 85,
        slot1["enabled"]: True,
    }
    scale = _render_priority_scale(config, _blind_policy())
    # The slot is shown at its priority with the "here" marker.
    assert "85" in scale
    assert "◀" in scale
    assert "Custom" in scale


def test_priority_scale_unconfigured_slots_absent():
    from custom_components.adaptive_cover_pro.config_flow import _render_priority_scale

    scale = _render_priority_scale({}, _blind_policy())
    assert "◀" not in scale  # no custom slot configured → no marker


def test_priority_scale_shows_custom_name_when_configured():
    """A named slot's ladder entry shows its name, not Custom#N (issue #910)."""
    from custom_components.adaptive_cover_pro.config_flow import _render_priority_scale
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS

    slot1 = CUSTOM_POSITION_SLOTS[1]
    config = {
        slot1["sensors"]: ["binary_sensor.x"],
        slot1["position"]: 40,
        slot1["priority"]: 85,
        slot1["name"]: "Canicule",
    }
    scale = _render_priority_scale(config, _blind_policy())
    assert "Canicule(85)" in scale
    assert "Custom#1" not in scale
