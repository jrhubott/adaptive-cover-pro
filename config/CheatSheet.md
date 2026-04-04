# Adaptive Cover Pro — Configuration Cheat Sheet

**Quick reference for all settings — save this as a PDF!**

---

## Core Settings

| Setting | Value | Description |
|---------|-------|-------------|
| Cover Entity | `cover.living_room_blinds` | Target cover |
| Window Azimuth | 180° | South-facing window |
| Default Position | 50% | Sun visible |
| Sunset Position | 0% | Sun below horizon |
| Time Window Start | 8:00 AM | Adaptive start |
| Time Window End | 6:00 PM | Adaptive end |

---

## Motion Control

| Setting | Value | Description |
|---------|-------|-------------|
| Motion Sensors | `binary_sensor.hallway` | Occupancy detector |
| Timeout | 300s (5 min) | No-motion wait |
| Status | motion_detected/no_motion | Real-time state |

---

## Weather Override

| Setting | Value | Description |
|---------|-------|-------------|
| Wind Sensor | `sensor.wind_speed` | Speed detection |
| Wind Threshold | 30 mph | Speed limit |
| Rain Sensor | `binary_sensor.rain` | Rain detection |
| Rain Position | 0% | Close when raining |
| Tolerance | ±45° | Direction window |
| Timeout | 300s | Clear delay |

---

## Climate Mode

| Setting | Value | Description |
|---------|-------|-------------|
| Temp Entity | `sensor.outside_temp` | Outside temp |
| Summer Tilt | 0° | Cool strategy |
| Winter Tilt | 80° | Heat strategy |
| Temp Range | 70-80°F | Trigger range |

---

## Glare Zones

| Setting | Value | Description |
|---------|-------|-------------|
| FOV Left | -30° | Left view angle |
| FOV Right | +30° | Right view angle |
| Blind Spot | 45° | Elevation trigger |
| Enable | Yes | Glare active |

---

## Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| motion_detected | Motion active | Normal |
| no_motion | Timeout active | Normal |
| waiting_for_data | Sensors offline | Check sensors |
| timeout_pending | Motion timer running | Wait |
| active | Control running | Normal |
| outside_time_window | Outside hours | Wait |
| sun_not_visible | Sun below horizon | Normal |
| manual_override | Manual moved | Reset or wait |

---

## Reset Buttons

| Button | What It Does |
|--------|--------------|
| Manual Override Reset | Clears user override |
| Force Override Clear | Clears force state |

---

## Quick Configs

### Solar Only
```
Mode: Basic
Window Azimuth: 180°
Default: 50%
Sunset: 0%
Time: 8-18
```

### Climate Cooling
```
Mode: Climate
Strategy: Summer
Temp: 75°F
Tilt: 0°
Window: 180°
```

### Motion Aware
```
Mode: Basic
Motion: binary_sensor.hallway
Timeout: 5 min
Force: None
```

### Security Mode
```
Force Override: alarm_sensor
Position: 0%
Bypass: Yes
```

---

## Diagnostic Entities

| Entity | Check |
|--------|-------|
| binary_sensor.motion_status | motion/no_motion |
| sensor.control_status | active/not_active |
| sensor.last_motion_time | motion timestamp |
| sensor.sun_azimuth | sun position |
| sensor.sun_elevation | sun height |
| sensor.galery_zone_active | glare status |

---

## Common Values

| Setting | Typical Range |
|---------|---------------|
| Azimuth | 0-360° |
| Window Width | 36-72" |
| Window Height | 48-84" |
| Window Depth | 2-8" |
| Default Position | 25-75% |
| Sunset Position | 0-50% |
| Timeout | 180-900s |
| Wind Threshold | 20-50 mph |
| Tolerance | 30-60° |
| Cloud Coverage | 50-90% |

---

## Quick Fixes

| Problem | Fix |
|---------|-----|
| Blinds not moving | Check time window, azimuth |
| Motion timeout short | Increase from 300s |
| Weather override too sensitive | Increase threshold |
| Manual override stuck | Use reset button |
| Sun position wrong | Recalculate azimuth |
| waiting_for_data status | Update to v2.13.8+ |

---

## Commands

| Command | Purpose |
|---------|---------|
| `set_azimuth` | Set window direction |
| `move_cover` | Move cover to position |
| `open_cover` | Open fully |
| `close_cover` | Close fully |
| `tilt_cover` | Adjust slat angle |

---

## Release Checklist

- [ ] v2.13.8-beta.2 motion fix
- [ ] v2.13.8-beta.1 reconciliation fix
- [ ] v2.13.7 motion timeout fix
- [ ] Manual override reset working
- [ ] Motion status initialization working
- [ ] Climate mode tested
- [ ] Glare zones tested

---

## Helpful Links

- Config Location: Settings → Devices & Services → Adaptive Cover Pro
- Status Sensors: Developer Tools → States → filter by sensor
- Logs: Settings → System → Logs → adaptive_cover_pro
- Support: GitHub Issues

---

*Last Updated: v2.13.8-beta.2*
