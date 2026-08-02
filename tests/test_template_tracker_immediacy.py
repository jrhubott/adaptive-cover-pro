"""Tracked-template immediacy and ``acp`` context wiring (issue #1159).

``_register_template_tracker`` exists to give a template-only override
sensor-grade immediacy — the cover reacts the instant the template flips, with
no companion binary sensor and no polling (#577/#563/#639/#632/#974). That
property is invisible to every other test in the suite: they assert the
manager-level fold, not the tracker's listener set. This file guards it
directly, for plain templates and for templates written against the ``acp``
namespace, plus the coordinator-side wiring that hands every render site the
same context.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.adaptive_cover_pro.const import (
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_END_TIME,
    CONF_MOTION_TEMPLATE,
    CONF_START_TIME,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from tests._helpers.acp_namespace import (
    acp_variables,
    make_acp_entry,
    seed_acp_row,
    seed_sun_infront,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


def _make_entry(hass: HomeAssistant, entry_id: str, options: dict) -> MockConfigEntry:
    entry = make_acp_entry(hass, entry_id, options={**VERTICAL_OPTIONS, **options})
    hass.states.async_set(
        "sun.sun", "above_horizon", {"azimuth": 180.0, "elevation": 45.0}
    )
    hass.states.async_set(
        "cover.test_blind", "open", {"current_position": 100, "supported_features": 143}
    )
    return entry


def _preseed_sun_infront(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Create the Sun Infront registry row ahead of setup.

    Mirrors the common real-world case (HA restart / options save): the entity
    registry is loaded early and already holds this entry's rows, so the
    namespace resolves at tracker-registration time. The unique_id matches what
    ``binary_sensor.py`` builds, so the real entity adopts this exact row.
    """
    return seed_sun_infront(hass, entry)


async def test_plain_template_tracker_fires_on_dependency_change(
    hass: HomeAssistant,
) -> None:
    """A template over an ordinary entity still reaches the coordinator callback.

    The invariant guard for the whole #1159 change: passing a render context to
    ``TrackTemplate`` must not cost an ordinary template its immediacy.
    """
    entry = _make_entry(
        hass,
        "tt_plain_01",
        {CONF_MOTION_TEMPLATE: "{{ is_state('input_boolean.guests', 'on') }}"},
    )
    hass.states.async_set("input_boolean.guests", "off")

    with (
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        fired.reset_mock()
        hass.states.async_set("input_boolean.guests", "on")
        await hass.async_block_till_done()

        assert fired.called, (
            "A plain motion template must still fire its tracked-result callback "
            "when its dependency flips (immediacy contract, #577)."
        )


async def test_acp_namespace_template_tracker_fires_on_own_entity_change(
    hass: HomeAssistant,
) -> None:
    """A namespace self-reference records a real state listener (#1159).

    ``acp.sun_infront`` resolves to an entity_id *string*, so ``is_state()``
    still records the dependency in ``RenderInfo`` — which is the only reason
    the tracker fires at all.
    """
    entry = _make_entry(
        hass,
        "tt_acp_01",
        {CONF_MOTION_TEMPLATE: "{{ is_state(acp.sun_infront, 'on') }}"},
    )
    entity_id = _preseed_sun_infront(hass, entry)
    hass.states.async_set(entity_id, "off")

    with (
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        fired.reset_mock()
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()

        assert fired.called, (
            "A template using the acp namespace must be tracked through the "
            "resolved entity_id, so flipping that entity fires the callback."
        )


async def test_track_template_never_carries_a_rate_limit(hass: HomeAssistant) -> None:
    """``TrackTemplate.rate_limit`` stays unset — it cannot guard a self-reference.

    HA's ``_rate_limit_for_event`` (``homeassistant/helpers/event.py``) opens with
    *"Specifically referenced entities are excluded from the rate limit"* and
    returns ``None`` whenever the entity that changed is in
    ``RenderInfo.entities``. ``is_state(acp.sun_infront, …)`` puts exactly that
    entity_id there, so a ``rate_limit`` on these trackers would be inert — it
    would read as protection while providing none. The real guard is the refresh
    coalescer; this test exists so nobody re-adds the decoration.
    """
    acp_template = "{{ is_state(acp.sun_infront, 'on') }}"
    plain_template = "{{ states('sensor.lux') | int > 100 }}"
    entry = _make_entry(
        hass,
        "tt_rate_01",
        {
            CONF_MOTION_TEMPLATE: acp_template,
            CONF_DAYTIME_GATE_TEMPLATE: plain_template,
        },
    )
    _preseed_sun_infront(hass, entry)

    captured: dict[str, float | None] = {}

    from homeassistant.helpers import event as ha_event

    original = ha_event.async_track_template_result

    def _capture(hass_, track_templates, action, **kwargs):
        for tt in track_templates:
            captured[tt.template.template] = tt.rate_limit
        return original(hass_, track_templates, action, **kwargs)

    with (
        patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture,
        ),
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert captured[acp_template] is None, (
        "HA bypasses TrackTemplate.rate_limit for a specifically-referenced "
        "entity, which is exactly what acp.<key> resolves to — do not set it."
    )
    assert captured[plain_template] is None, (
        "A template that does not use the acp namespace must keep its unthrottled "
        "immediacy — no rate limit."
    )


async def test_only_namespace_templates_have_their_refreshes_coalesced(
    hass: HomeAssistant, freezer
) -> None:
    """A namespace template's action is throttled; a plain one's is not.

    The leading edge is immediate for both — that is the whole immediacy
    contract. The difference is what happens to the *second* and *third* flip
    inside one cooldown: the plain template still runs its action once per flip,
    while the namespace template collapses them into a single trailing run once
    the window closes. Nothing is dropped; the trailing run is asserted.
    """
    acp_template = "{{ is_state(acp.sun_infront, 'on') }}"
    plain_template = "{{ is_state('input_boolean.guests', 'on') }}"
    entry = _make_entry(
        hass,
        "tt_coalesce_01",
        {
            CONF_MOTION_TEMPLATE: acp_template,
            CONF_DAYTIME_GATE_TEMPLATE: plain_template,
        },
    )
    sun_infront = _preseed_sun_infront(hass, entry)
    hass.states.async_set(sun_infront, "off")
    hass.states.async_set("input_boolean.guests", "off")

    with (
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as acp_fired,
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_daytime_gate_template_change",
            new_callable=AsyncMock,
        ) as plain_fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        acp_fired.reset_mock()
        plain_fired.reset_mock()

        # Three rendered-result flips each, all inside one cooldown window.
        for state in ("on", "off", "on"):
            hass.states.async_set(sun_infront, state)
            hass.states.async_set("input_boolean.guests", state)
            await hass.async_block_till_done()

        assert plain_fired.call_count == 3, (
            "A plain template keeps one action per flip — the immediacy contract "
            "shipped across #577/#563/#639/#632/#974 is untouched."
        )
        assert acp_fired.call_count == 1, (
            "A namespace template runs its leading flip immediately and coalesces "
            "the rest of the window into one deferred run."
        )

        freezer.tick(1.2)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert acp_fired.call_count == 2, (
            "The coalesced flips must still produce a trailing run when the "
            "window closes — coalescing drops nothing."
        )


async def test_unload_cancels_the_coalescer_pending_trailing_run(
    hass: HomeAssistant, freezer
) -> None:
    """Unloading the entry must kill the coalescer's deferred run.

    ``_coalesce_namespace_refreshes`` schedules a trailing timer that outlives
    the tracker: HA's :class:`~homeassistant.helpers.debounce.Debouncer` owns its
    own ``loop.call_later`` handle, so removing the template tracker on unload
    does nothing to it. Without ``entry.async_on_unload(debouncer.async_shutdown)``
    that timer still fires after teardown and replays the deferred flip into a
    coordinator that has already been unloaded.

    The suite cannot catch that leak by accident: ``conftest`` returns
    ``expected_lingering_timers = True`` for every ``@pytest.mark.integration``
    test, which is every test that reaches ``_register_template_tracker``. So the
    guard has to be explicit — drive the debouncer into a pending state, unload,
    then let the window close and assert nothing ran.
    """
    entry = _make_entry(
        hass,
        "tt_unload_01",
        {CONF_MOTION_TEMPLATE: "{{ is_state(acp.sun_infront, 'on') }}"},
    )
    sun_infront = _preseed_sun_infront(hass, entry)
    hass.states.async_set(sun_infront, "off")

    debouncers: list[Debouncer] = []

    class _RecordingDebouncer(Debouncer):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            debouncers.append(self)

    with (
        patch("custom_components.adaptive_cover_pro.Debouncer", _RecordingDebouncer),
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        fired.reset_mock()

        # Two flips inside one cooldown: the first runs on the leading edge, the
        # second is deferred — which is the state that leaks.
        for state in ("on", "off"):
            hass.states.async_set(sun_infront, state)
            await hass.async_block_till_done()

        assert len(debouncers) == 1, (
            "Exactly one namespace template is configured, so exactly one "
            f"coalescing Debouncer should exist. Got {len(debouncers)}."
        )
        debouncer = debouncers[0]
        assert fired.call_count == 1
        assert debouncer._timer_task is not None, (
            "Precondition: the second flip must leave a live trailing timer, "
            "otherwise this test proves nothing about cancelling one."
        )
        assert (
            debouncer._execute_at_end_of_timer is True
        ), "Precondition: a run must actually be queued for the trailing edge."

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert debouncer._timer_task is None, (
            "Unload must cancel the coalescer's scheduled trailing timer — the "
            "entry.async_on_unload(debouncer.async_shutdown) wiring."
        )

        freezer.tick(1.2)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert fired.call_count == 1, (
            "The deferred flip must not be replayed after unload: that runs a "
            "refresh against a torn-down coordinator."
        )


#: The canonical reachable self-reference loop (issue #1159 review). The gate
#: template reads ``control_status``; a True verdict opens the time window,
#: which makes ``control_status`` stop reporting ``outside_time_window``, which
#: renders the template False, which closes the window again. A true 2-cycle
#: with no fixed point.
_LOOPING_GATE_TEMPLATE = "{{ is_state(acp.control_status, 'outside_time_window') }}"

#: Refreshes past this many mean the loop is running away. The counting stub
#: stops calling through at the cap instead of letting an unguarded regression
#: hang the suite.
_REFRESH_CAP = 60


async def test_self_referencing_gate_template_refresh_is_bounded(
    hass: HomeAssistant, freezer
) -> None:
    """The real control_status feedback loop settles at one refresh per window.

    Everything here is production wiring: the real daytime-gate template, the
    real tracker, the real ``sensor.…_control_status``, the real coordinator
    refresh. Only two things are instrumented — a counter around
    ``async_refresh`` (which stops calling through at ``_REFRESH_CAP`` so a
    regression fails fast instead of hanging), and one asynchronously-dispatched
    consumer of the control-status sensor.

    That consumer matters. A real install always has one (the recorder, a
    template sensor, an automation), and it is what keeps every flip visible to
    the tracker. With only synchronous callbacks on the bus the loop happens to
    die out after a handful of iterations and the test would pass vacuously;
    with one, the *unguarded* loop is unbounded — measured at >9000 refreshes
    before the harness timed out, versus the five below.

    A real start/end window is configured, with hours of slack on both sides:
    the ``hass`` fixture puts HA in US/Pacific, so the frozen 17:00 UTC instant
    is 10:00 local, two hours past the 08:00 start and ten short of the 20:00
    end. The slack is the point — an exact boundary tie would leave the window
    open only by ``>=``, and a fixture that drifts closed collapses the run to
    ``per_window == [0, 0, 0, 0, 0]``, which reads as a coalescer regression
    rather than a broken fixture.

    Configuring a real window at all is only safe since #1161 made both sides
    of the comparison HA-framed — ``TimeWindowManager`` reads HA's configured
    zone rather than the host clock, and the boundary's implied date comes
    from the same place — so the window's openness no longer depends on the
    machine's local timezone.
    """
    freezer.move_to(dt.datetime(2026, 6, 15, 17, 0, 0, tzinfo=dt.UTC))
    entry = _make_entry(
        hass,
        "tt_loop_01",
        {
            CONF_START_TIME: "08:00:00",
            CONF_END_TIME: "20:00:00",
            CONF_DAYTIME_GATE_TEMPLATE: _LOOPING_GATE_TEMPLATE,
        },
    )
    # Seed the row the gate template references, then ask the *production*
    # resolver what entity_id that is. The consumer has to be subscribed before
    # setup — it is what keeps each flip in its own pass — so the id cannot be
    # read back after the platform registers, and hardcoding it would let an
    # entity-naming change quietly unsubscribe the consumer. The loop would then
    # die out and this test would fail as ``per_window == [0, 0, 0, 0, 0]``,
    # reading as a coalescer regression rather than a broken fixture.
    control_status = seed_acp_row(
        hass, entry, "sensor", "control_status", "dining_room_shade_control_status"
    )
    assert control_status == acp_variables(hass, entry)["acp_entity"]("control_status")

    def _async_consumer(_event) -> None:
        """Stand in for a real install's async consumer of the sensor.

        Deliberately *not* ``@callback``-decorated — HA dispatches an
        undecorated listener off the event loop, and that extra hop is what
        keeps each flip in its own pass instead of letting the bus collapse
        pairs of them.
        """

    unsub = async_track_state_change_event(hass, [control_status], _async_consumer)

    refreshes: list[None] = []
    real_refresh = AdaptiveDataUpdateCoordinator.async_refresh

    async def _counting_refresh(self) -> None:
        refreshes.append(None)
        if len(refreshes) > _REFRESH_CAP:
            return
        await real_refresh(self)

    try:
        with patch.object(
            AdaptiveDataUpdateCoordinator, "async_refresh", _counting_refresh
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            assert hass.states.get(control_status) is not None, (
                "Precondition: the real control_status sensor must publish under "
                f"{control_status!r}, the entity_id the stand-in consumer is "
                "subscribed to. Without that the loop never reaches the consumer "
                "and every assertion below passes or fails for fixture reasons."
            )

            settled = len(refreshes)
            assert settled <= 8, (
                f"Setup drove {settled} refreshes — the self-referencing gate "
                "template is spinning instead of being coalesced."
            )

            per_window: list[int] = []
            for _ in range(5):
                before = len(refreshes)
                freezer.tick(1.2)
                async_fire_time_changed(hass)
                await hass.async_block_till_done()
                per_window.append(len(refreshes) - before)

            assert per_window == [1, 1, 1, 1, 1], (
                "Each cooldown window must yield exactly one refresh: >1 means the "
                "loop is not bounded, 0 means the deferred flip was dropped instead "
                f"of deferred. Got {per_window}."
            )

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        unsub()


async def test_namespace_context_is_passed_to_every_tracker(
    hass: HomeAssistant,
) -> None:
    """Every registration gets the same context, namespace-using or not.

    One context for the tracker's render and the manager's cycle render is what
    keeps the two from ever disagreeing.
    """
    plain_template = "{{ states('sensor.lux') | int > 100 }}"
    entry = _make_entry(hass, "tt_ctx_01", {CONF_DAYTIME_GATE_TEMPLATE: plain_template})

    captured: dict[str, object] = {}

    from homeassistant.helpers import event as ha_event

    original = ha_event.async_track_template_result

    def _capture(hass_, track_templates, action, **kwargs):
        for tt in track_templates:
            captured[tt.template.template] = tt.variables
        return original(hass_, track_templates, action, **kwargs)

    with (
        patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture,
        ),
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert set(captured[plain_template]) == {"acp", "acp_entity"}, (
        "Tracked templates get the entity forms only — never acp_state, whose "
        "value reads are invisible to RenderInfo."
    )


async def test_coordinator_threads_one_context_to_every_render_site(
    hass: HomeAssistant,
) -> None:
    """Every collaborator that renders an option template shares the coordinator's context.

    The no-duplication guard for #1159: one factory, one context per flavour, no
    render site building its own. ``acp_state`` reaches only the untracked
    numeric resolver.
    """
    entry = _make_entry(hass, "tt_wiring_01", {})

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    entity_ctx = coordinator._template_variables
    assert set(entity_ctx) == {"acp", "acp_entity"}
    assert set(coordinator._template_variables_with_state) == {
        "acp",
        "acp_entity",
        "acp_state",
    }

    for owner, attr in (
        (coordinator._motion_mgr, "_template_variables"),
        (coordinator._weather_mgr, "_template_variables"),
        (coordinator._time_mgr, "_template_variables"),
        (coordinator._climate_provider, "_template_variables"),
        (coordinator._snapshot_builder, "_template_variables"),
    ):
        assert getattr(owner, attr) is entity_ctx, (
            f"{type(owner).__name__} must render against the coordinator's one "
            "entity-form context, not its own."
        )

    assert (
        coordinator._template_resolver._variables
        is coordinator._template_variables_with_state
    ), "The numeric resolver is the one render site that also gets acp_state."
