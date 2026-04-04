# Adaptive Cover Pro — Troubleshooting Guide

**Solution to common problems, error states, and configuration issues.**

---

## Status Sensor Reference

The **Motion Status** sensor tells you what's happening:

| Status | Meaning | Fix |
|--------|---------|-----|
| `motion_detected` | Motion detected — solar control active | Normal operation |
| `no_motion` | Timeout active — default position used | Normal, after 5 min no motion |
| `waiting_for_data` | Sensors unavailable — data pending | Check sensor connectivity |
| `timeout_pending` | Motion timeout running | Wait for it to expire |

The **Control Status** sensor shows adaptive state:

| Status | Meaning | Fix |
|--------|---------|-----|
| `active` | Control running normally | Nothing needed |
| `outside_time_window` | Outside configured hours | Wait or adjust times |
| `position_delta_too_small` | Position difference negligible | Normal, no action needed |
| `time_delta_too_small` | Time not enough for update | Normal wait |
| `manual_override` | User moved blinds manually | Use reset or wait timeout |
| `automatic_control_off` | Control disabled | Enable in config |
| `sun_not_visible` | Sun below horizon | Normal night state |
| `force_override_active` | Force sensor active | Check force sensors |
| `weather_override_active` | Weather override active | Weather conditions cleared |
| `motion_timeout` | Motion timeout active | Motion resumed or timeout expired |

---

## Problem: Blinds Not Tracking Sun

### Symptoms
- Blinds stay at one position
- No movement despite sun changing position
- Status shows "Active" but never changes

### Causes & Fixes

**1. Time Window Mismatch**
- **Symptom:** "outside_time_window" status
- **Fix:** Adjust Start/End times to current day hours

**2. Azimuth Wrong**
- **Symptom:** Blinds move opposite to expected
- **Fix:** Add 180° to azimuth (opposite direction)
- **Test:** Use `sensor.sun_elevation` to verify

**3. Position Threshold Too Tight**
- **Symptom:** No movement despite sun change
- **Fix:** Check Open/Close Threshold in config
- **Suggestion:** Lower threshold to 25-50%

---

## Problem: Motion Control Not Working

### Symptoms
- Blinds don't respond to motion
- Motion timeout never fires
- "waiting_for_data" status

### Causes & Fixes

**1. Motion Sensor Off/Unavailable**
- **Symptom:** "waiting_for_data" status
- **Fix:** Check sensor is available in Home Assistant
- **Test:** Open Developer Tools → States → filter by your sensor

**2. Timeout Too Short**
- **Symptom:** Blinds close immediately after motion
- **Fix:** Increase timeout from 300s to 600-900s
- **Note:** Short timeout makes blinds aggressive

**3. Motion Sensor Not Reading**
- **Symptom:** Always shows "no_motion"
- **Fix:** Check sensor state in UI
- **Test:** `states.binary_sensor.motion_sensor`

---

## Problem: "Manual Override" Stuck

### Symptoms
- Blinds won't auto-reset
- Manual override persists longer than expected
- Reset button doesn't work

### Causes & Fixes

**1. Manual Override Duration**
- **Symptom:** Always in override state
- **Fix:** Set duration to 0 to disable override
- **Or:** Wait for configured time

**2. Intermediate Position Check**
- **Symptom:** Override extends while moving
- **Fix:** Enable "Ignore Intermediate" in config
- **Note:** Only counts final position

**3. Manual Reset Button**
- **Symptom:** Reset button doesn't clear override
- **Fix:** Trigger motion sensor (tap, wave) to "wake" it
- **Or:** Toggle force override on/off

---

## Problem: Weather Override Too Active

### Symptoms
- Blinds close randomly due to weather
- Wind/rain triggers too sensitive
- Override persists after conditions clear

### Causes & Fixes

**1. Wind Threshold Too Low**
- **Symptom:** Closes on light breeze
- **Fix:** Increase threshold from 30 mph to 40-50 mph
- **Note:** Use max sustained wind, not gusts

**2. Wind Direction Tolerance Too Wide**
- **Symptom:** Closes when wind off-angle
- **Fix:** Reduce tolerance from ±45° to ±30°
- **Note:** Only trigger when wind directly faces window

**3. Clear-Delay Timeout**
- **Symptom:** Stuck after weather clears
- **Fix:** Adjust timeout from 300s to 180-600s
- **Note:** Longer = more stable, Shorter = more responsive

---

## Problem: Solar Position Wrong

### Symptoms
- Blinds open at wrong time
- Too early/late for sunrise/sunset
- Default position not applied

### Causes & Fixes

**1. Azimuth Wrong**
- **Symptom:** Blinds face wrong direction
- **Fix:** Recalculate azimuth with compass app
- **Test:** Check azimuth matches your window orientation

**2. Window Depth Missing**
- **Symptom:** Sun calculations off
- **Fix:** Set Window Depth to actual value
- **Note:** Deeper recess = sun hits later

**3. Glare Zone Issues**
- **Symptom:** Always closed when sun visible
- **Fix:** Disable Glare Zones temporarily
- **Or:** Adjust FOV/Limits

---

## Problem: "waiting_for_data" Status

### Symptoms
- Motion Status sensor shows "waiting_for_data"
- Blinds default to known state
- No solar or motion control active

### Causes & Fixes

**1. Reload Problem** *(Fixed in v2.13.8-beta.2)*
- **Symptom:** After config reload, motion lost
- **Fix:** Update to v2.13.8-beta.2+
- **Note:** Old versions reset motion tracking

**2. Sensor Connectivity**
- **Symptom:** All motion sensors offline
- **Fix:** Check integration for sensor entity
- **Test:** `states.binary_sensor.motion`

**3. Motion Manager Not Initialized**
- **Symptom:** Timeout never clears
- **Fix:** Restart integration
- **Note:** Usually clears on next motion event

---

## Problem: Climate Mode Not Working

### Symptoms
- Blinds ignore temperature
- Climate mode shows inactive
- Summer/winter strategy not applying

### Causes & Fixes

**1. Temperature Entity Missing**
- **Symptom:** No temperature data
- **Fix:** Add outdoor temp sensor
- **Test:** States → filter by temp sensor

**2. Threshold Range Too Tight**
- **Symptom:** Never triggers
- **Fix:** Widen range (e.g., 70-80°F)
- **Note:** Wider range = more active

**3. Climate Mode Disabled**
- **Symptom:** Mode stuck at Basic
- **Fix:** Select Climate mode in config
- **Note:** Requires temp entity configured

---

## Problem: Glare Zones Not Triggering

### Symptoms
- Glare zones disabled
- Sun in blind spot not blocked
- FOV angles wrong

### Causes & Fixes

**1. Glare Zones Disabled**
- **Symptom:** No glare detection
- **Fix:** Enable Glare Zones in config
- **Note:** Must have FOV angles set

**2. FOV Angles Too Wide/Narrow**
- **Symptom:** Always/no glare detection
- **Fix:** Adjust FOV Left/Right values
- **Note:** Typical: -30° to +30°

**3. Blind Spot Mismatch**
- **Symptom:** Wrong angles trigger
- **Fix:** Adjust Blind Spot angles
- **Note:** Match your actual glare angles

---

## Diagnostic Sensors

Use these for troubleshooting:

| Entity ID | What It Shows |
|-----------|---------------|
| `binary_sensor.motion_status` | Motion control state |
| `sensor.control_status` | Adaptive control status |
| `sensor.motion_timeout_active` | Timeout flag (on/off) |
| `sensor.last_motion_time` | Last motion detection time |
| `sensor.sun_azimuth` | Current sun position |
| `sensor.sun_elevation` | Current sun height |
| `sensor.sun_visible` | Sun visibility status |
| `sensor.glare_zone_active` | Glare detection status |
| `sensor.weather_override_active` | Weather override flag |
| `sensor.force_override_active` | Force override flag |
| `sensor.manual_override_active` | Manual override flag |

---

## Reset Procedures

### Clear Manual Override
1. Click **Manual Override Reset** button
2. Or trigger motion sensor
3. Or disable/enable override config

### Clear Weather Override
1. Wait for conditions to clear
2. Or wait timeout (300s default)
3. Or toggle weather config off/on

### Clear Motion Timeout
1. Motion detected → clears timeout
2. Or wait timeout expiration
3. Or reload integration

### System Restart
1. **Settings → System → Restart Integration**
2. Or **Developer Tools → Reload Integration**
3. Or restart Home Assistant

---

## Edge Cases

### No Motion Sensors Configured
- **Behavior:** Always assumes presence
- **Result:** Sun control always runs
- **Fix:** Add motion sensors for occupancy mode

### Empty Motion Sensor List
- **Behavior:** Same as above
- **Result:** No occupancy detection
- **Fix:** Add at least one motion sensor

### Invalid Window Dimensions
- **Symptom:** Glare zones off
- **Fix:** Set valid Width/Height/Depth
- **Note:** All three required for glare

### Time Window Outside Valid Range
- **Symptom:** "outside_time_window" always
- **Fix:** Use times 0:00-23:59
- **Note:** Must be valid timestamps

---

## Logs & Debugging

### Enable Debug Logging

**Configuration.yaml:**
```yaml
logger:
  logs:
    custom_components.adaptive_cover_pro: debug
```

### Common Error Messages

| Message | Meaning | Fix |
|---------|---------|-----|
| "No configuration entity" | Cover not selected | Select cover in config |
| "Invalid azimuth value" | Azimuth out of range | Check 0-360° |
| "Timeout expired" | Motion wait too long | Increase timeout value |
| "Position delta too small" | No position change needed | Normal state |
| "Climate mode not configured" | Temp entity missing | Add temp entity |

---

## Performance Tips

- **Test with one window first** before multi-window setup
- **Start with wide timeouts** before shortening
- **Monitor logs** during first 24 hours
- **Adjust seasonally** (winter vs summer sun)
- **Use motion sensor** for presence detection
- **Disable force override** during testing
- **Enable glare zones** last (complex)

---

## Version-Specific Notes

### v2.13.8-beta.2+
- Motion status no longer shows "waiting_for_data" after reload
- Proper initialization of motion manager on startup

### v2.13.7+
- Motion timeout pending state fix
- Better detection of stale timeouts

### v2.13.6+
- Reset button time_window fix
- Climate-aware override reset

---

*Last Updated: v2.13.8-beta.2*
