# Adaptive Cover Pro — Quick Start Guide

**For users who want functional automation in under 30 minutes.**

## TL;DR

1. **Add Integration** → Configure with your cover + window direction
2. **Save** → Blinds start tracking the sun
3. **Tweak** → Adjust default positions as needed

---

## Step 1: Configure Your Cover

**Settings → Devices & Services → Adaptive Cover Pro → Configure**

| Field | What to Set | Why |
|-------|-------------|-----|
| **Cover Entity** | Select your blind/shutter | Main control target |
| **Window Azimuth** | Compass direction (0=N, 90=E, 180=S, 270=W) | Tells where sun hits window |
| **Default Position** | 50% (half-open) | Sun position when visible |
| **Sunset Position** | 0% (closed) | When sun below horizon |
| **Time Window Start** | 8:00 AM | Start time of day |
| **Time Window End** | 6:00 PM | End time of day |

**Optional but useful:**
- **Window Width/Height**: Physical dimensions in inches
- **Window Depth**: Frame recess in inches

---

## Step 2: Enable Weather (Optional)

### Simple Weather Protection

| Field | What to Set |
|-------|-------------|
| **Rain Sensor** | `binary_sensor.outdoor_rain_sensor` |
| **Wind Speed Sensor** | `sensor.wind_speed` |
| **Wind Threshold** | 30 (mph) |
| **Rain Override Position** | 0% (close when raining) |

---

## Step 3: Enable Motion (Optional)

### Simple Occupancy Control

| Field | What to Set |
|-------|-------------|
| **Motion Sensor** | `binary_sensor.hallway_motion` |
| **Timeout** | 300 seconds (5 min) |

---

## Step 4: Save & Test

Click **Save**. Your blinds should now:
- Track the sun automatically
- Close at sunset
- Open when motion detected
- Close when no motion for 5 minutes

---

## Common Issues

### Blinds Not Moving
- Check **Control Status** sensor: "Outside Adaptive Time Window"
- Verify window azimuth matches actual window direction
- Ensure time window includes current time

### "Manual Override" Status
- You manually moved blinds recently
- Wait for override timeout (default: 30 minutes)
- Or click **Reset** button

### Motion Sensor Wrong
- Motion timeout too short? Increase timeout
- No motion for default position? Timeout is working
- False positives? Check sensor reliability

---

## Next Steps

- Read **Configuration.md** for advanced features
- Try **Climate Mode** for temperature-based control
- Enable **Glare Zones** for precision sun blocking
- Configure **Multiple Windows** for different orientations

---

## Need Help?

- **GitHub Issues:** https://github.com/jrhubott/adaptive-cover-pro/issues
- **Version:** v2.13.8-beta.2
- **Tested on:** Home Assistant 2024.5+
