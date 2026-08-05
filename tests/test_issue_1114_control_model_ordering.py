"""Coordinator ordering guard for the day/night control-model cache (#1114).

``DayNightShadePolicy`` caches its control model per instance because the
downstream dispatch hooks — ``resolve_entity_target``, ``after_position_command``,
and the A3 ``tilt_capability_contradiction`` predicate — receive no ``options``.
The cache is filled by the generic ``CoverTypePolicy.sync_runtime_options`` hook,
which the coordinator drives from ``_update_options``.

What that buys is ordering: ``sync_runtime_options`` runs before
``_evaluate_health_checks``, so a single-carriage Model B/C instance is already
known to be single-carriage the first time A3 asks. ``post_pipeline_resolve``
also fills the cache, but it runs *after* the health checks inside the same
``_calculate_cover_state`` call, so it cannot rescue cycle 1.

The stub-driven A3 tests in ``tests/test_coordinator_healthchecks.py`` assign
``policy._control_model`` by hand and therefore stay green no matter where the
coordinator resolves it. Nothing pinned the coordinator side until this file: move
the seam after ``_calculate_cover_state``, or add an earlier caller that passes
stale options, and a Model B/C user on position-only hardware gets a
``cover_tilt_unsupported`` Repair on startup for hardware that is exactly what
Models B/C exist to support.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_ENTITIES,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    DAY_NIGHT_MODEL_SPLIT_RANGE,
    ISSUE_COVER_TILT_UNSUPPORTED,
    CoverType,
)
from custom_components.adaptive_cover_pro.managers.repair import RepairManager
from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

# open + close + set_position + stop, with no set_tilt_position bit — the
# position-only hardware #1114 reports as locked out of the Model B/C picker.
_POSITION_ONLY_FEATURES = 15


@pytest.mark.parametrize(
    "model", [DAY_NIGHT_MODEL_SPLIT_RANGE, DAY_NIGHT_MODEL_DUAL_ENTITY]
)
async def test_cycle_one_control_model_beats_the_a3_health_check(hass, model):
    """A real Model B/C entry on a position-only cover must not flag A3 on cycle 1.

    Drives a real config entry through setup (which runs the coordinator's first
    update cycle) and captures every A3 predicate verdict. The spy is installed on
    ``RepairManager`` *before* setup because that first cycle happens while the
    entry is still being created, so there is no coordinator instance to patch yet.

    Asserting the recorded predicate rather than ``ir.async_create_issue`` is
    deliberate: the real ``RepairManager`` debounces for 15 minutes, so the raise
    a wrong verdict would eventually produce is invisible inside one cycle. The
    predicate is the decision; the debounce only delays acting on it.
    """
    # Its own entity id, so ``setup_integration``'s tilt-capable
    # ``cover.test_blind`` fixture state cannot overwrite these capabilities.
    cover_id = "cover.position_only"
    hass.states.async_set(
        cover_id,
        "open",
        {"current_position": 100, "supported_features": _POSITION_ONLY_FEATURES},
    )

    records: list[tuple[str, bool]] = []
    real_update = RepairManager.update_predicate

    def _spy(self, issue_key, unhealthy, **kwargs):
        records.append((issue_key, unhealthy))
        return real_update(self, issue_key, unhealthy, **kwargs)

    entry_id = f"a3_order_{model}"
    with patch.object(RepairManager, "update_predicate", _spy):
        await setup_integration(
            hass,
            name="Day Night",
            cover_type=CoverType.DAY_NIGHT_SHADE,
            options={
                **VERTICAL_OPTIONS,
                CONF_ENTITIES: [cover_id],
                CONF_DAY_NIGHT_CONTROL_MODEL: model,
            },
            entry_id=entry_id,
        )

    key = f"{ISSUE_COVER_TILT_UNSUPPORTED}_{entry_id}_{cover_id}"
    verdicts = [unhealthy for issue_key, unhealthy in records if issue_key == key]
    assert verdicts, f"A3 never evaluated for {cover_id}: {records}"
    assert verdicts[0] is False, (
        f"cycle-1 A3 false contradiction for {model} — the control model was not "
        "resolved before _evaluate_health_checks ran"
    )
