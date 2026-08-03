"""The Calibration menu — travel time measured, typed in, or cleared.

Two things here are more than UI plumbing.

The **Calibration parent** exists because "calibration" already meant the
position interpolation curve. Grouping that page with travel time under one
parent is what lets each keep a name that says which it is.

The **save-time merge** closes a real data-loss window. A measurement run is
started from this menu and keeps going after the user navigates on; it writes
its results straight to the config entry. The flow, meanwhile, is holding a
snapshot of ``options`` taken when it opened, and saving writes that snapshot
back wholesale — so without the merge, finishing the dialog silently erases
everything the run measured.
"""

from __future__ import annotations

import re

import pytest
from homeassistant.data_entry_flow import InvalidData
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_INTERP,
    CONF_SENSOR_TYPE,
    CONF_TRAVEL_TIME_CALIBRATION,
    DOMAIN,
    TRAVEL_CALIBRATION_SOURCE_MANUAL,
    TRAVEL_CALIBRATION_SOURCE_MEASURED,
    TRAVEL_TIME_MAX_SECONDS,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration

_COVER = "cover.living_room"
_OTHER = "cover.kitchen"

_MEASURED = {
    "full_travel_seconds": 24.0,
    "open_seconds": 26.0,
    "close_seconds": 22.0,
    "start_delay_seconds": 1.5,
    "calibrated_at": "2026-08-02T12:00:00+00:00",
    "source": TRAVEL_CALIBRATION_SOURCE_MEASURED,
}


async def _setup_entry(
    hass: HomeAssistant,
    *,
    entry_id: str,
    entities: list[str] | None = None,
    extra_options: dict | None = None,
    assumed: bool = False,
) -> MockConfigEntry:
    covers = entities or [_COVER]
    for eid in covers:
        attrs: dict = {"current_position": 40, "supported_features": 15}
        if assumed:
            attrs["assumed_state"] = True
        hass.states.async_set(eid, "open", attrs)

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = covers
    if extra_options:
        opts.update(extra_options)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Travel", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Travel",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _step(hass: HomeAssistant, flow_id: str, step: str):
    return await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": step}
    )


async def _open_calibration(hass: HomeAssistant, entry: MockConfigEntry):
    """Init the options flow and step into the Calibration menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    return await _step(hass, flow_id, "calibration"), flow_id


async def _open_travel_time(hass: HomeAssistant, flow_id: str):
    """Step into Travel Time from wherever we are (via Calibration)."""
    await _step(hass, flow_id, "calibration")
    return await _step(hass, flow_id, "travel_time")


async def _finish(hass: HomeAssistant, flow_id: str) -> None:
    """Walk back out of the submenus and save.

    Each menu only offers its own children plus a Back row, so Done is not
    reachable from inside Travel Time — the walk is the same one a user makes.
    """
    await _step(hass, flow_id, "calibration")
    await _step(hass, flow_id, "init")
    await _step(hass, flow_id, "done")
    await hass.async_block_till_done()


# ------------------------------------------------------------------ #
# Menu composition
# ------------------------------------------------------------------ #


async def test_calibration_replaces_interp_on_the_top_level_menu(
    hass: HomeAssistant,
) -> None:
    entry = await _setup_entry(hass, entry_id="tt_menu")
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert "calibration" in result["menu_options"]
    assert "interp" not in result["menu_options"]


async def test_calibration_menu_always_offers_travel_time(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass, entry_id="tt_menu_child")
    result, _ = await _open_calibration(hass, entry)

    assert result["type"] == "menu"
    assert "travel_time" in result["menu_options"]


async def test_position_calibration_stays_conditional_on_its_own_option(
    hass: HomeAssistant,
) -> None:
    """Moving ``interp`` under a parent must not make it unconditionally visible."""
    off = await _setup_entry(hass, entry_id="tt_interp_off")
    result, _ = await _open_calibration(hass, off)
    assert "interp" not in result["menu_options"]

    on = await _setup_entry(
        hass, entry_id="tt_interp_on", extra_options={CONF_INTERP: True}
    )
    result, _ = await _open_calibration(hass, on)
    assert "interp" in result["menu_options"]


async def test_cancel_is_offered_only_while_a_run_is_in_flight(
    hass: HomeAssistant,
) -> None:
    """Start and cancel are mutually exclusive — never both, never neither."""
    entry = await _setup_entry(hass, entry_id="tt_cancel_entry")
    _, flow_id = await _open_calibration(hass, entry)
    idle = await _step(hass, flow_id, "travel_time")
    assert "travel_time_run" in idle["menu_options"]
    assert "travel_time_cancel" not in idle["menu_options"]

    entry.runtime_data.travel_calibration._state = "running"
    running = await _open_travel_time(hass, flow_id)
    assert "travel_time_cancel" in running["menu_options"]
    assert "travel_time_run" not in running["menu_options"]


# ------------------------------------------------------------------ #
# Manual entry
# ------------------------------------------------------------------ #


async def _submit_manual(hass: HomeAssistant, flow_id: str, values: dict):
    """From the Calibration menu: into Travel Time, into the form, submit."""
    await _step(hass, flow_id, "travel_time")
    await _step(hass, flow_id, "travel_time_manual")
    return await hass.config_entries.options.async_configure(flow_id, values)


async def test_manual_entry_stores_a_manual_row(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass, entry_id="tt_manual")
    _, flow_id = await _open_calibration(hass, entry)
    await _submit_manual(hass, flow_id, {_COVER: 23.5})
    await _finish(hass, flow_id)

    row = entry.options[CONF_TRAVEL_TIME_CALIBRATION][_COVER]
    assert row["full_travel_seconds"] == 23.5
    assert row["source"] == TRAVEL_CALIBRATION_SOURCE_MANUAL
    # A hand-entered figure has no per-leg detail to offer.
    assert row["open_seconds"] is None
    assert row["close_seconds"] is None


async def test_zero_clears_an_existing_entry(hass: HomeAssistant) -> None:
    """0 is the clear gesture — the reason the field is a BOX, not a slider."""
    entry = await _setup_entry(
        hass,
        entry_id="tt_manual_clear",
        extra_options={CONF_TRAVEL_TIME_CALIBRATION: {_COVER: dict(_MEASURED)}},
    )
    _, flow_id = await _open_calibration(hass, entry)
    await _submit_manual(hass, flow_id, {_COVER: 0})
    await _finish(hass, flow_id)

    assert CONF_TRAVEL_TIME_CALIBRATION not in entry.options


async def test_out_of_range_value_is_rejected(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass, entry_id="tt_manual_range")
    _, flow_id = await _open_calibration(hass, entry)

    with pytest.raises(InvalidData, match=re.escape(f"data['{_COVER}']")):
        await _submit_manual(hass, flow_id, {_COVER: TRAVEL_TIME_MAX_SECONDS + 50})


async def test_reset_clears_the_whole_table(hass: HomeAssistant) -> None:
    entry = await _setup_entry(
        hass,
        entry_id="tt_reset",
        extra_options={CONF_TRAVEL_TIME_CALIBRATION: {_COVER: dict(_MEASURED)}},
    )
    _, flow_id = await _open_calibration(hass, entry)
    await _step(hass, flow_id, "travel_time")
    await _step(hass, flow_id, "travel_time_reset")
    await _finish(hass, flow_id)

    assert CONF_TRAVEL_TIME_CALIBRATION not in entry.options


# ------------------------------------------------------------------ #
# The save-time merge
# ------------------------------------------------------------------ #


def _write_behind_the_flow(hass: HomeAssistant, entry, table: dict) -> None:
    """Simulate a calibration run finishing while the options dialog is open."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_TRAVEL_TIME_CALIBRATION: table}
    )


async def test_a_measurement_taken_during_the_dialog_survives_saving(
    hass: HomeAssistant,
) -> None:
    """The data-loss window this merge exists to close.

    Without it, the flow's opening snapshot — which has no travel-time table at
    all — is written back over the finished run's results.
    """
    entry = await _setup_entry(hass, entry_id="tt_race")
    result = await hass.config_entries.options.async_init(entry.entry_id)

    _write_behind_the_flow(hass, entry, {_COVER: dict(_MEASURED)})

    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_TRAVEL_TIME_CALIBRATION][_COVER] == _MEASURED


async def test_a_manual_edit_and_a_concurrent_measurement_both_survive(
    hass: HomeAssistant,
) -> None:
    """Per-entity, not all-or-nothing.

    The user types a time for one cover while a run measures another. Resolving
    this either way round would throw away real work.
    """
    entry = await _setup_entry(
        hass, entry_id="tt_race_split", entities=[_COVER, _OTHER]
    )
    _, flow_id = await _open_calibration(hass, entry)
    await _submit_manual(hass, flow_id, {_COVER: 18.0, _OTHER: 0})

    _write_behind_the_flow(hass, entry, {_OTHER: dict(_MEASURED)})

    await _finish(hass, flow_id)

    table = entry.options[CONF_TRAVEL_TIME_CALIBRATION]
    assert table[_COVER]["full_travel_seconds"] == 18.0
    assert table[_COVER]["source"] == TRAVEL_CALIBRATION_SOURCE_MANUAL
    assert table[_OTHER] == _MEASURED


async def test_saving_prunes_rows_for_covers_no_longer_controlled(
    hass: HomeAssistant,
) -> None:
    """Swapping a cover out must not leave its time behind forever."""
    entry = await _setup_entry(
        hass,
        entry_id="tt_prune",
        extra_options={
            CONF_TRAVEL_TIME_CALIBRATION: {
                _COVER: dict(_MEASURED),
                "cover.removed": dict(_MEASURED),
            }
        },
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    await hass.async_block_till_done()

    table = entry.options[CONF_TRAVEL_TIME_CALIBRATION]
    assert set(table) == {_COVER}


# ------------------------------------------------------------------ #
# Configuration summary
# ------------------------------------------------------------------ #


def test_summary_counts_calibrated_covers() -> None:
    config = {
        CONF_ENTITIES: [_COVER, _OTHER],
        CONF_TRAVEL_TIME_CALIBRATION: {_COVER: dict(_MEASURED)},
    }
    assert "Travel time: 1/2 covers" in _build_config_summary(config, CoverType.BLIND)


def test_summary_calls_out_hand_entered_times() -> None:
    manual = {**_MEASURED, "source": TRAVEL_CALIBRATION_SOURCE_MANUAL}
    config = {
        CONF_ENTITIES: [_COVER, _OTHER],
        CONF_TRAVEL_TIME_CALIBRATION: {_COVER: dict(_MEASURED), _OTHER: manual},
    }
    summary = _build_config_summary(config, CoverType.BLIND)
    assert "Travel time: 2/2 covers (1 entered by hand)" in summary


def test_summary_omits_the_line_when_nothing_is_calibrated() -> None:
    config = {CONF_ENTITIES: [_COVER]}
    assert "Travel time" not in _build_config_summary(config, CoverType.BLIND)


async def test_summary_warns_about_an_unmeasurable_cover_with_no_time(
    hass: HomeAssistant,
) -> None:
    """The footgun: this cover can never be measured and has nothing entered.

    Silently, it will simply never animate — exactly what the summary is for.
    """
    hass.states.async_set(
        _COVER, "open", {"supported_features": 11, "assumed_state": True}
    )
    summary = _build_config_summary(
        {CONF_ENTITIES: [_COVER]}, CoverType.BLIND, hass=hass
    )

    assert "report an assumed state" in summary
    assert _COVER in summary


async def test_no_warning_once_a_time_has_been_entered(hass: HomeAssistant) -> None:
    hass.states.async_set(
        _COVER, "open", {"supported_features": 11, "assumed_state": True}
    )
    manual = {**_MEASURED, "source": TRAVEL_CALIBRATION_SOURCE_MANUAL}
    summary = _build_config_summary(
        {
            CONF_ENTITIES: [_COVER],
            CONF_TRAVEL_TIME_CALIBRATION: {_COVER: manual},
        },
        CoverType.BLIND,
        hass=hass,
    )

    assert "report an assumed state" not in summary


async def test_visiting_the_manual_page_does_not_claim_untouched_covers(
    hass: HomeAssistant,
) -> None:
    """Submitting the form must only claim the fields the user actually changed.

    The form carries one field per cover and posts all of them back. Treating
    every field as an edit would mean opening this page to set one cover
    silently takes authority over the others, and the save-time merge would
    then overwrite a concurrent measurement of a cover nobody touched — turning
    a per-entity merge into an all-or-nothing one.
    """
    entry = await _setup_entry(hass, entry_id="tt_untouched", entities=[_COVER, _OTHER])
    _, flow_id = await _open_calibration(hass, entry)
    # Only _COVER is given a value; _OTHER keeps its pre-filled 0.
    await _submit_manual(hass, flow_id, {_COVER: 30.0, _OTHER: 0})

    flow = hass.config_entries.options._progress[flow_id]
    assert flow._travel_time_edited == {_COVER}


async def test_measure_now_starts_a_run_and_returns_to_the_menu(
    hass: HomeAssistant,
) -> None:
    """Fire-and-forget: the dialog must not block for the length of a run.

    Returning to the menu rather than aborting also preserves edits made
    earlier in the same session.
    """
    entry = await _setup_entry(hass, entry_id="tt_run")
    _, flow_id = await _open_calibration(hass, entry)
    await _step(hass, flow_id, "travel_time")

    calibrator = entry.runtime_data.travel_calibration
    started: list[bool] = []
    calibrator.async_run = lambda: started.append(True) or _noop()

    result = await _step(hass, flow_id, "travel_time_run")
    await hass.async_block_till_done()

    assert started == [True]
    assert result["step_id"] == "travel_time"


async def test_cancel_stops_the_run_and_returns_to_the_menu(
    hass: HomeAssistant,
) -> None:
    entry = await _setup_entry(hass, entry_id="tt_cancel_action")
    calibrator = entry.runtime_data.travel_calibration
    calibrator._state = "running"
    cancelled: list[bool] = []
    calibrator.async_cancel = lambda *, restore: cancelled.append(restore)

    _, flow_id = await _open_calibration(hass, entry)
    await _step(hass, flow_id, "travel_time")
    result = await _step(hass, flow_id, "travel_time_cancel")

    # restore=True: the menu's Cancel puts the covers back, unlike the panic
    # stop, which stands the run down without further movement.
    assert cancelled == [True]
    assert result["step_id"] == "travel_time"


async def _noop() -> None:
    return None


async def test_a_sub_minimum_time_is_rejected_not_clamped(
    hass: HomeAssistant,
) -> None:
    """The floor the constant advertises is actually enforced.

    Clamping would store a number the user never typed; accepting would let the
    manual path produce a ramp the measured path discards as implausible.
    """
    entry = await _setup_entry(hass, entry_id="tt_too_short")
    _, flow_id = await _open_calibration(hass, entry)
    result = await _submit_manual(hass, flow_id, {_COVER: 0.3})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "travel_time_too_short"}
    assert CONF_TRAVEL_TIME_CALIBRATION not in entry.options


async def test_the_status_block_reflects_a_run_that_finished_mid_dialog(
    hass: HomeAssistant,
) -> None:
    """The page that starts a run must show what the run produced.

    Rendering the flow's opening snapshot would report every freshly-measured
    cover as "not calibrated" on the very page that just measured it.
    """
    entry = await _setup_entry(hass, entry_id="tt_live_status")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]

    _write_behind_the_flow(hass, entry, {_COVER: dict(_MEASURED)})

    flow = hass.config_entries.options._progress[flow_id]
    assert "24.0" in flow._travel_time_status()
    assert "not calibrated" not in flow._travel_time_status()


async def test_a_run_writes_its_results_to_the_config_entry(
    hass: HomeAssistant,
) -> None:
    """The whole persist path: calibrator → apply_options_patch → entry options.

    Also pins that the write does NOT reload the entry — the key is registered
    as runtime-applicable precisely because a reload would tear down the run
    that produced the numbers.
    """
    from custom_components.adaptive_cover_pro import options_write_reloads

    entry = await _setup_entry(hass, entry_id="tt_persist")
    coordinator = entry.runtime_data
    table = {_COVER: dict(_MEASURED)}

    # A live coordinator has run at least one cycle, so it holds a baseline to
    # diff against. Without one the listener reloads unconditionally, which is
    # the right conservative default but not the state under test here.
    coordinator._cached_options = dict(entry.options)
    new_options = {**entry.options, CONF_TRAVEL_TIME_CALIBRATION: table}
    assert options_write_reloads(entry, new_options) is False

    await coordinator._async_persist_travel_calibration(table)
    await hass.async_block_till_done()

    assert entry.options[CONF_TRAVEL_TIME_CALIBRATION] == table
    assert entry.state is ConfigEntryState.LOADED


async def test_a_manual_entry_saved_mid_run_is_not_erased_when_the_run_ends(
    hass: HomeAssistant,
) -> None:
    """The mirror of the flow-side merge.

    A run holds the covers for minutes. If it wrote a start-of-run snapshot at
    the end, a time hand-entered in the meantime — the ONLY way an unmeasurable
    cover ever gets one — would be silently undone.
    """
    entry = await _setup_entry(
        hass, entry_id="tt_persist_merge", entities=[_COVER, _OTHER]
    )
    calibrator = entry.runtime_data.travel_calibration
    manual = {**_MEASURED, "source": TRAVEL_CALIBRATION_SOURCE_MANUAL}

    # The run began with an empty table…
    measured = {_COVER: dict(_MEASURED)}
    # …and the user saved a manual time for the other cover while it ran.
    _write_behind_the_flow(hass, entry, {_OTHER: manual})

    merged = calibrator._merged_table([_COVER, _OTHER], measured)

    assert merged[_COVER] == _MEASURED
    assert merged[_OTHER] == manual
