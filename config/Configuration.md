# Adaptive Cover Pro — Configuration Manual

## Overview

**Adaptive Cover Pro** automates your window coverings based on solar position, weather, occupancy, and time-of-day. Unlike basic timers, it *learns* your window geometry and sun position to make intelligent decisions about when to open, close, or tilt your blinds.

**Who is this for?**
- Users with solar tracking or automated blinds who want more than basic schedule-based automation
- Smart home enthusiasts who configured the basic Home Assistant `cover.solar_blinds` but want more flexibility
- Users with weather-sensitive spaces (greenhouses, sunrooms, offices) where temperature control matters

**What it does:**
- Tracks the sun's position relative to your windows
- Adjusts blinds to block glare while maximizing natural light
- Responds to weather (rain, high winds, cloud cover)
- Respects occupancy (keeps blinds open when you're home, close when away)
- Provides manual override with automatic self-reset
- Works with basic solar, climate-controlled, or hybrid control strategies

---

## Quick Start

### 1. Basic Setup

**Configuration → Integrations → Adaptive Cover Pro**

After adding the integration, navigate to **Settings → Devices & Services → Adaptive Cover Pro → Configure**.

You must have at least one **Cover Entity** configured. You can add one by selecting an existing cover (blinds, shades, roller shutters) from the dropdown.

### 2. Core Settings

#### Cover Selection
- **Cover Entity:** Select the cover(s) you want adaptive control for
- **Window ID (Optional):** If you have multiple directional windows, assign IDs to track them separately

#### Solar Settings
| Item | What it does | Typical Value |
|------|--------------|---------------|
| **Time Window Start** | Earliest time adaptive control begins | 8:00 AM |
| **Time Window End** | Latest time control runs | 6:00 PM |

If you use **Time Window Entities** (see below), you can set "None" here.

#### Window Geometry
These settings tell the integration how your window is positioned:

| Item | Description | Typical Value |
|------|-------------|---------------|
| **Azimuth** | Direction window faces (0°=North, 90°=East, 180°=South, 270°=West) | 180° for South-facing |
| **Window Width** | Physical width in inches (used for glare calculations) | 48" |
| **Window Height** | Physical height in inches | 60" |
| **Window Depth** | Depth of window recess or frame | 4" |

**Tip:** Use a compass app to find azimuth. Measure window dimensions with a tape measure.

#### Adaptive Behavior
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Default Position** | Position when sun is visible and no override active | 50% (half-open) |
| **Sunset Position** | Position when sun disappears below horizon | 0% (fully closed) |
| **Sunrise Position** | Position when sun rises above horizon | 100% (fully open) |
| **Sunrise Offset** | How early/blinded before sunrise | 0° |
| **Sunset Offset** | How late/blinded after sunset | 0° |

### 3. Save & Test

Click **Save**. The integration will:
1. Validate your window geometry
2. Show a confirmation message
3. Begin controlling your cover

**Status Indicator:**
- **Active** = Solar control running
- **Outside Adaptive Time Window** = Outside configured hours
- **Sun Not Visible** = Sun below horizon
- **Manual Override** = You've manually moved the blinds
- **Force Override Active** = Weather or force condition active

---

## Time Window Control

### Basic Time Window
Set fixed times when adaptive control runs:
- **Start Time:** 8:00 AM
- **End Time:** 6:00 PM

### Time Window Entities (Advanced)
Instead of fixed times, use entities that trigger the window:
- **Start Entity:** Binary sensor (on/off) that begins the adaptive window
- **End Entity:** Binary sensor that ends the adaptive window

**Use case:** Use a "home" sensor or occupancy detector to start adaptive control when people arrive, and stop when everyone leaves.

---

## Solar Control Modes

### Basic Mode (Recommended for most users)
- Uses sun position to decide open/close
- Default position when sun visible
- Sunset position when sun not visible

### Climate Mode (Advanced)
Add temperature considerations:
- **Temperature Entity:** Link to a temperature sensor (e.g., `sensor.outside_temp`)
- **Temperature Range:** Define cool/warmer thresholds
- **Strategy:** Choose summer (cool) or winter (insulation) approach

**Summer Strategy (Cooling):**
- Blinds *tilt closed* to block heat
- Open position: 0° (blinds closed)
- Good for hot climates or south-facing rooms

**Winter Strategy (Heating):**
- Blinds *open* to admit solar heat
- Close position: 100° (blinds open)
- Good for cold climates or north-facing rooms

---

## Weather Override

Your covers automatically adjust when weather conditions change.

### Wind Protection
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Wind Direction Sensor** | Entity measuring wind direction (degrees) | `sensor.wind_direction` |
| **Wind Speed Sensor** | Entity measuring wind speed | `sensor.wind_speed` |
| **Wind Threshold** | Speed at which blinds close | 30 mph |
| **Tolerance** | Degrees of window facing considered | ±45° |

When wind hits your window direction above threshold, blinds retract to protect the interior.

### Rain Protection
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Rain Sensor** | Entity detecting rain | `binary_sensor.rain_sensor` |
| **Rain Threshold** | Rain intensity trigger | 1.0 mm/hr |
| **Override Position** | Blinds position when raining | 0% (closed) |

### Cloud Coverage (Optional)
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Cloud Sensor** | Entity showing cloud coverage % | `sensor.cloud_coverage` |
| **Cloud Threshold** | % when treated as overcast | 75% |

---

## Motion & Occupancy Control

The integration can pause solar control when you're away from the window.

### Motion Control
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Motion Sensors** | List of motion sensors | `binary_sensor.hallway_motion` |
| **Timeout** | Seconds to wait before resuming control | 5 min (300s) |

**How it works:**
1. Motion detected → Blinds follow solar schedule
2. No motion for timeout → Blinds go to default position
3. Timeout expires → Blinds close/settle

### Force Override Sensors
Manual override that *never resets* until you clear it:
- **Force Override Sensors:** List of sensors that force blinds closed
- **Override Position:** Always close to this position when active
- **Bypass Auto Control:** When active, bypass all other rules

**Use case:** Security mode — force blinds closed when alarm triggered.

---

## Glare Zones (Advanced)

Define directional blind positions that cause glare:
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Enable Glare Zones** | Turn zone detection on/off | Yes |
| **FOV Left** | Left view angle from window | -30° |
| **FOV Right** | Right view angle from window | +30° |
| **Blind Spot Left** | Sun angle that causes glare left | -45° |
| **Blind Spot Right** | Sun angle that causes glare right | +45° |
| **Blind Spot Elevation** | Sun elevation causing glare | 45° |

When sun falls in a "blind spot," blinds adjust to block it.

---

## Manual Override

### Manual Override Duration
Set how long manual override lasts:
- **Duration:** Seconds before auto-reset (0 = disabled)
- **Threshold:** Minimum position change to count as manual (0-100%)

**How it works:**
1. You manually move the blinds → override activates
2. After timeout configured, system resumes auto-control
3. If "Ignore Intermediate" enabled, only final position matters

### Manual Override Reset Button
- **Manual Override Reset:** Button in UI that immediately resets override
- Resets the override and resumes adaptive control

---

## Advanced Settings

### Interpolation Settings
Control how the integration interprets your data:
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Interpolate Start** | Start entity for interpolation | `none` |
| **Interpolate End** | End entity for interpolation | `none` |
| **Interpolation List** | List of values between start/end | [0, 25, 50, 75, 100] |
| **Interpolate** | Enable or disable interpolation | No |

### Threshold Settings
Control open/close behavior:
| Item | Description | Typical Value |
|------|-------------|---------------|
| **Open/Close Threshold** | Position percent for open/close | 50% |

---

## Supported Entities

### Required
- **Cover Entity(s):** Your automated blinds/shutters
- **Sun Sensor:** `sun.sun` (built-in Home Assistant)

### Optional but Recommended
- **Weather Entity:** `sensor.weather_entity` (for conditions)
- **Wind/Rain Sensors:** Weather data sources
- **Motion Sensors:** Occupancy detection
- **Temperature Entities:** Climate control

### Supported Cover Types
- **Blinds:** Venetian, vertical, roller
- **Awning:** Slatted or retractable
- **Tilt-Only:** Slat angle control

---

## Common Configurations

### Configuration A — Solar Tracking
- **Mode:** Basic
- **Window Azimuth:** South (180°)
- **Default Position:** 50%
- **Sunset Position:** 0%
- **Time Window:** 6:00 AM — 8:00 PM
- **No Override:** None

### Configuration B — Climate Control
- **Mode:** Climate
- **Strategy:** Summer (cooling)
- **Default Position:** 0% (closed)
- **Temperature Entity:** `sensor.outside_temp`
- **Temp Range:** 72°F — 78°F
- **Time Window:** 7:00 AM — 7:00 PM

### Configuration C — Motion-Aware
- **Mode:** Basic
- **Motion Sensors:** `binary_sensor.hallway`
- **Timeout:** 5 minutes
- **Force Override:** Empty list
- **Manual Override:** 30-minute duration

---

## Troubleshooting

### Blinds Not Moving
1. Check **Control Status** sensor:
   - "Outside Adaptive Time Window" = Check your time settings
   - "Sun Not Visible" = Wait until sunrise
   - "Manual Override" = Blinds were manually moved
2. Verify cover entity state is accessible
3. Check time window configuration

### Sun Position Wrong
1. Verify window azimuth (use compass)
2. Check Home Assistant's `sun.sun` sensor
3. Adjust sunrise/sunset offset

### Weather Override Activating Too Early
1. Increase wind threshold (mph)
2. Narrow tolerance (degrees)
3. Verify weather data is accurate

### Motion Timeout Too Short
1. Increase timeout (seconds)
2. Check motion sensor reliability
3. Consider adding multiple sensors

---

## Maintenance

### Updating Configuration
1. Go to **Settings → Devices & Services → Adaptive Cover Pro**
2. Click **Configure**
3. Adjust settings
4. Click **Save**

### Manual Override Clear
- Click **Manual Override Reset** button in UI
- Or trigger override with empty/invalid sensor state

### Logs & Diagnostics
- Check **Settings → System Logs** for errors
- Use **Decision Trace** sensor for detailed control logic
- Check motion status: "motion_detected", "no_motion", "waiting_for_data"

---

## FAQ

**Q: Do I need motion sensors?**  
A: No. Motion control is optional for users who want occupancy-based behavior.

**Q: What if my blinds don't support tilt?**  
A: Use the "Tilt Mode" option (basic mode only uses position).

**Q: How does manual override work?**  
A: You manually move blinds → system remembers override → after timeout, auto-control resumes.

**Q: Can I use multiple windows?**  
A: Yes. Configure different azimuths, default positions for each window ID.

**Q: Why won't my blinds move during the day?**  
A: Likely "Sun Not Visible" or "Outside Adaptive Time Window" status.

**Q: How long until motion timeout expires?**  
A: Configurable default (5 min). Can be set higher for slower-moving spaces.

---

## Support & Resources

- **GitHub Issues:** https://github.com/jrhubott/adaptive-cover-pro/issues
- **Documentation:** README.md
- **Release Notes:** See version history in HACS

---

*Last Updated: v2.13.8-beta.2*
