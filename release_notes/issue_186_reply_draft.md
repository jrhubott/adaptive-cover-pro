Hi @PhilDirty — thank you for opening this and for articulating the safety concerns so clearly.  You are right: **Automatic Control is not a master switch**, and that needed both better documentation and a proper hard-off control.

Here is where I've landed (implemented, tests written, pending beta release).

---

## Short answers to your three questions

### 1. How is ACP designed to avoid harmful situations?

**Force Override and Weather Override bypass Automatic Control by design.**  When you configure a binary sensor (wind, rain, window-open, alarm) as a Force Override sensor, it *must* move covers regardless of whether sun-tracking is paused — that is the whole point of the feature.  Turning Automatic Control OFF does not silence those handlers; it silences only solar tracking, climate, cloud suppression, glare zones, and custom positions.

**Position Verification** (reconciliation) has a bounded retry cap (`max_retries`, default 3).  If a cover is physically blocked and doesn't reach its target, the integration retries up to that cap and then backs off with a warning logged.  It does not hammer the motor indefinitely.  The retry count is visible in the diagnostics sensor so you can see when it gave up.  Worst-case timeline: the integration tries at t+0, t+60s, t+120s, and t+180s, then gives up.

### 2. How do I turn ACP off for good?

I've added a new switch: **Integration Enabled** (default ON).

When OFF:
- Any ACP-in-flight cover moves are stopped immediately — `cover.stop_cover` is sent to every cover ACP was currently moving (capability-checked so covers without STOP support are not touched).
- Every subsequent outbound cover command is blocked — solar, climate, custom position, manual-override expiry, startup, end-of-window default, **and** Force Override and Weather Override.
- Motion and weather deferred timers are cancelled so nothing fires after you disable it.
- Reconciliation state is cleared so no stale target is replayed when you re-enable.
- Diagnostic sensors keep updating — the integration is still running in HA; it just refuses to move covers.

When you turn it back ON:
- No forced snap — covers stay exactly where they are.
- The next natural update cycle resumes positioning normally.

### 3. Is there an emergency stop?

**Yes, and there are two ways to trigger it.**

**Per-instance:** The `Integration Enabled` switch for each ACP instance is the per-room emergency stop. Wire it into any automation, scene, voice assistant command, or physical button dashboard card.

**Global:** Three new services control all ACP instances at once (or a targeted subset via Home Assistant's `target:` block):

| Service | What it does |
|---|---|
| `adaptive_cover_pro.integration_disable` | Stops in-flight ACP moves, cancels timers, clears state, disables. |
| `adaptive_cover_pro.integration_enable` | Re-enables. Covers stay where they are; natural resume. |
| `adaptive_cover_pro.emergency_stop` | **Panic button.** Stops *every* configured cover (not just in-flight ones), then disables. With no target, hits every ACP instance in your installation. |

Example — global off from Developer Tools → Services:

```yaml
service: adaptive_cover_pro.emergency_stop
# no target needed: all ACP instances, all covers
```

---

## What Automatic Control actually does (clarification)

| When Automatic Control is OFF… | Stopped | Still runs |
|---|---|---|
| Solar / climate / cloud / glare / custom-position / default handlers | ✅ | |
| Manual-override detection and expiry reposition | ✅ | |
| Reconciliation of non-safety targets | ✅ | |
| Force Override (priority 100) | | ✅ (safety) |
| Weather Override (priority 90) | | ✅ (safety, configurable) |
| Reconciliation of safety targets (force/weather) | | ✅ |
| End-of-time-window return-to-default | | ✅ |

Think of **Automatic Control** as "solar tracking on/off" and **Integration Enabled** as "ACP on/off".

---

## Your specific use cases

**Obstacle (human, furniture) blocks the cover** — Position Verification retries at most `max_retries` times (default 3), then logs a warning and backs off.  No infinite loop, no motor hammering.  Worst case: t+0 → t+60s → t+120s → t+180s → give up.

**Walk-through / don't get locked out** — keep Custom Position at priority **77** (the default, below Manual Override at 80).  If someone physically pushes a cover open, the manual override handler wins at priority 80 and holds the cover in place for the manual-override duration (default ~2 hours).  The cover only re-closes when the override timer expires.  This is the pattern I described in #144.

**Kids / "off while I'm out"** — use `adaptive_cover_pro.emergency_stop` from a script, voice command, or automation.  Nothing ACP can do will move a cover until you call `adaptive_cover_pro.integration_enable`.  You can wire it to your alarm panel — when alarm armed = emergency_stop; when alarm disarmed = integration_enable.

---

## Tests added (so these promises cannot regress silently)

**Kill switch / Integration Enabled:**
- `test_apply_position_blocked_when_integration_disabled`
- `test_force_override_blocked_when_integration_disabled`
- `test_weather_override_blocked_when_integration_disabled`
- `test_reconcile_skips_all_targets_when_integration_disabled` (safety AND non-safety)
- `test_re_enable_does_not_force_reposition`
- `test_safety_targets_cleared_on_disable_prevents_replay`
- `test_enable_resumes_normal_sends`
- `test_position_verification_does_not_retry_beyond_max` (obstacle use case)
- `test_delta_prevents_hammering_when_cover_reports_same_position`
- `test_manual_override_wins_over_auto_control` (walk-through use case)
- `test_integration_disabled_preserves_manual_position`
- `test_automatic_control_off_allows_force_override` (documents the bypass)
- `test_automatic_control_off_blocks_solar`
- `test_kill_switch_off_blocks_force_override_unlike_auto_control` (key behavioral difference)
- `test_stop_in_flight_sends_stop_to_waiting_entities`
- `test_stop_in_flight_skips_covers_without_has_stop`
- `test_stop_in_flight_skips_entities_not_waiting`
- `test_stop_in_flight_clears_wait_for_target`

**Global services:**
- `test_resolve_no_target_returns_all_coordinators`
- `test_resolve_entity_id_maps_to_owning_coordinator`
- `test_resolve_unmanaged_entity_skipped`
- `test_resolve_device_id_maps_to_coordinator`
- `test_resolve_entity_id_within_device_coordinator_not_narrowed`
- `test_integration_enable_no_target_enables_all`
- `test_integration_enable_sends_no_commands`
- `test_integration_disable_calls_stop_in_flight`
- `test_integration_disable_sets_enabled_false`
- `test_integration_disable_cancels_timers_and_clears_state`
- `test_emergency_stop_calls_stop_all`
- `test_emergency_stop_also_disables_integration`
- `test_emergency_stop_with_entity_filter_narrows_stop`
- `test_emergency_stop_no_target_hits_all_instances`
- `test_unload_services_removes_all_three`

---

I'll cut a beta as soon as this lands.  Will ping you here for testing — really appreciate the detailed feedback that prompted all of this.
