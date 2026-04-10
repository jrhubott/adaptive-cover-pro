Hi @PhilDirty — thank you for opening this and for articulating the safety concerns so clearly.  You are right: **Automatic Control is not a master switch**, and that needed both better documentation and a proper hard-off control.

Here is where I've landed (implemented, tests written, pending beta release).

---

## Short answers to your three questions

### 1. How is ACP designed to avoid harmful situations?

**Force Override and Weather Override bypass Automatic Control by design.**  When you configure a binary sensor (wind, rain, window-open, alarm) as a Force Override sensor, it *must* move covers regardless of whether sun-tracking is paused — that is the whole point of the feature.  Turning Automatic Control OFF does not silence those handlers; it silences only solar tracking, climate, cloud suppression, glare zones, and custom positions.

**Position Verification** (reconciliation) has a bounded retry cap (`max_retries`, default 3).  If a cover is physically blocked and doesn't reach its target, the integration retries up to that cap and then backs off with a warning logged.  It does not hammer the motor indefinitely.  The retry count is visible in the diagnostics sensor so you can see when it gave up and tune `max_retries` lower if you want even fewer retries.

### 2. How do I turn ACP off for good?

I've added a new switch: **Integration Enabled** (default ON).

When OFF:
- Every outbound cover command is blocked — solar, climate, custom position, manual-override expiry, startup, end-of-window default, **and** Force Override and Weather Override.
- Motion and weather deferred timers are cancelled so nothing fires after you disable it.
- Reconciliation state is cleared so no stale target is replayed when you re-enable.
- Diagnostic sensors keep updating — the integration is still running in HA; it just refuses to move covers.

When you turn it back ON:
- No forced snap — covers stay exactly where they are.
- The next natural update cycle resumes positioning normally.

### 3. Is there an emergency stop?

**Yes: the Integration Enabled switch is the emergency stop.**  It is a single HA entity — wire it into any automation, scene, voice assistant command, or physical button dashboard card.

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

**Obstacle (human, furniture) blocks the cover** — Position Verification retries at most `max_retries` times (default 3), then logs a warning and backs off.  No infinite loop, no motor hammering.

**Walk-through / don't get locked out** — keep Custom Position at priority **77** (the default, below Manual Override at 80).  If someone physically pushes a cover open, the manual override handler wins at priority 80 and holds the cover in place for the manual-override duration (default ~2 hours).  The cover only re-closes when the override timer expires.  This is the pattern I described in #144.

**Kids / "off while I'm out"** — flip Integration Enabled OFF.  Nothing ACP can do will move a cover until you flip it back ON.  You can put this on a dashboard button, a voice command, or wire it to your alarm panel.

---

## Tests added (so these promises cannot regress silently)

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

---

I'll cut a beta as soon as this lands.  Will ping you here for testing — really appreciate the detailed feedback that prompted this.
