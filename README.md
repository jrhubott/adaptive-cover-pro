![Version](https://img.shields.io/github/v/release/jrhubott/adaptive-cover?style=for-the-badge)
![Tests](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover/tests.yml?branch=main&label=Tests&style=for-the-badge)
![Hassfest](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover/hassfest.yml?branch=main&label=Hassfest&style=for-the-badge)
![HACS](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover/hacs.yaml?branch=main&label=HACS&style=for-the-badge)
![Coverage](https://img.shields.io/codecov/c/github/jrhubott/adaptive-cover?style=for-the-badge)

![logo](https://github.com/jrhubott/adaptive-cover/blob/main/images/logo.png#gh-light-mode-only)
![logo](https://github.com/jrhubott/adaptive-cover/blob/main/images/dark_logo.png#gh-dark-mode-only)

# Adaptive Cover Pro

This Custom-Integration provides sensors for vertical and horizontal blinds based on the sun's position by calculating the position to filter out direct sunlight.

This integration builds upon the template sensor from this forum post [Automatic Blinds](https://community.home-assistant.io/t/automatic-blinds-sunscreen-control-based-on-sun-platform/)

## Credits

**Adaptive Cover Pro** is a fork of the original [Adaptive Cover](https://github.com/basbruss/adaptive-cover) integration created by **[Bas Brussee (@basbruss)](https://github.com/basbruss)**.

This fork includes enhancements and modifications, but the core functionality and architecture are based on Bas Brussee's excellent work. Please visit the [original repository](https://github.com/basbruss/adaptive-cover) to see the upstream project and consider supporting the original author.

## For Developers

If you're interested in contributing to this project, please see the **[Development Guide (docs/DEVELOPMENT.md)](docs/DEVELOPMENT.md)** for comprehensive documentation on:
- Setting up your development environment
- Project structure and architecture
- Development workflow and scripts
- Testing strategies
- **Release process** (automated with `./scripts/release`)
- Code standards and best practices

## Testing the Algorithms

Want to visualize how the blinds will behave before installing? The **Jupyter notebook** (`notebooks/test_env.ipynb`) lets you test and visualize the position calculation algorithms without needing Home Assistant or physical covers.

### Quick Start

**1. Install Jupyter:**
```bash
pip install jupyter matplotlib pandas pvlib
```

**2. Run the notebook:**
```bash
# From the repository root
jupyter notebook notebooks/test_env.ipynb
```

Or open in VS Code with the Jupyter extension installed.

**3. Configure and run:**
- Modify the configuration variables (location, window dimensions, orientation)
- Run all cells (Cell → Run All)
- Review the plots showing cover positions throughout the day

### What You'll See

The notebook generates two plots:
- **Vertical Cover Plot** - Shows blind position based on sun position for up/down blinds
- **Horizontal Cover Plot** - Shows awning extension for in/out awnings

Each plot displays:
- Sun elevation and azimuth over 24 hours
- Calculated cover position overlaid
- Sunrise/sunset times (red lines)
- When sun enters/exits your window's field of view (yellow lines)

### Example Configuration

```python
# Location (modify for your testing location)
timezone = "America/New_York"  # Your timezone
lat = 40.7128                  # Your latitude
lon = -74.0060                 # Your longitude

# Window properties
windown_azimuth = 180          # 180 = South-facing
window_fov_left = 90           # Field of view (degrees)
window_fov_right = 90
window_height = 3              # meters
window_distance = 0.5          # Distance from window to blind (meters)
```

**Perfect for:**
- Testing different window orientations before configuration
- Experimenting with field of view angles
- Validating behavior for your specific location
- Understanding how the algorithm responds to sun position

For detailed documentation, see the [Manual Testing section in CLAUDE.md](CLAUDE.md#manual-testing).

## Table of Contents

- [Adaptive Cover Pro](#adaptive-cover-pro)
  - [Credits](#credits)
  - [For Developers](#for-developers)
  - [Testing the Algorithms](#testing-the-algorithms)
    - [Quick Start](#quick-start)
    - [What You'll See](#what-youll-see)
    - [Example Configuration](#example-configuration)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Known Limitations & Best Practices](#known-limitations--best-practices)
  - [Installation](#installation)
    - [HACS (Recommended)](#hacs-recommended)
    - [Manual](#manual)
  - [Migrating from Adaptive Cover](#migrating-from-adaptive-cover)
    - [How to Import](#how-to-import)
    - [What Gets Imported](#what-gets-imported)
    - [After Import](#after-import)
    - [Import from Options Menu](#import-from-options-menu)
  - [Setup](#setup)
  - [Cover Types](#cover-types)
  - [Modes](#modes)
    - [Basic mode](#basic-mode)
    - [Climate mode](#climate-mode)
      - [Climate strategies](#climate-strategies)
  - [Variables](#variables)
    - [Common](#common)
    - [Vertical](#vertical)
    - [Horizontal](#horizontal)
    - [Tilt](#tilt)
    - [Automation](#automation)
    - [Climate](#climate)
    - [Blindspot](#blindspot)
  - [Entities](#entities)
  - [Features Planned](#features-planned)

## Features

- Individual service devices for `vertical`, `horizontal` and `tilted` covers
- Two mode approach with multiple strategies [Modes(`basic`,`climate`)](https://github.com/jrhubott/adaptive-cover?tab=readme-ov-file#modes)
- Binary Sensor to track when the sun is in front of the window
- Sensors for `start` and `end` time
- Auto manual override detection
- Smart device naming - automatically suggests device names based on your cover entities
- Support for both position-capable and open/close-only covers
  - Automatic detection of cover capabilities at runtime
  - Configurable threshold for open/close decision (default 50%)

- **Climate Mode**

  - Weather condition based operation
  - Presence based operation
  - Switch to toggle climate mode
  - Sensor for displaying the operation modus (`winter`,`intermediate`,`summer`)

- **Adaptive Control**

  - Turn control on/off
  - Control multiple covers
  - Optional return to default position when automatic control is disabled
  - Set start time to prevent opening blinds while you are asleep
  - Set minimum interval time between position changes
  - Set minimum percentage change
  - **Automatic Position Verification** (built-in reliability feature)
    - Periodically verifies covers reached the positions we sent them to (every 2 minutes)
    - Automatically retries failed position commands (up to 3 attempts)
    - Detects position mismatches between target and actual position (3% tolerance)
    - Respects manual override detection and skips during active moves
    - Separate from normal position updates - only retries failed commands, doesn't chase sun movement
    - No configuration required - works automatically when automatic control is enabled
    - Diagnostic sensors available for troubleshooting cover movement issues

- **Diagnostic Sensors** (Optional, disabled by default)
  - Real-time troubleshooting sensors to understand integration behavior
  - Priority 0 sensors (enabled by default when diagnostics enabled):
    - Sun position (azimuth, elevation, gamma)
    - Control status (why covers aren't moving)
    - Calculated position (before adjustments)
    - Last cover action (tracks most recent cover action with full details)
  - Priority 1 sensors (disabled by default, enable individually):
    - Position verification tracking (last check time, retry counts, mismatch detection)
    - Active temperature (climate mode only)
    - Climate conditions (climate mode only)
    - Time window status
    - Sun validity status
  - Enable in automation settings
  - All sensors use diagnostic entity category

- **Enhanced Geometric Accuracy** (automatic improvements)
  - Angle-dependent safety margins for better sun blocking at extreme angles
  - Automatic edge case handling for very low/high sun elevations
  - Smooth transitions across all sun angles using interpolation
  - Optional window depth parameter for advanced precision
  - No configuration required - works automatically
  - Backward compatible - existing installations benefit immediately

## Known Limitations & Best Practices

### Temperature Unit Consistency
**IMPORTANT:** All temperature sensors used in Climate mode must use the same unit system. The integration currently does not perform automatic unit conversion between Fahrenheit and Celsius.

- If your `Indoor Temperature Entity` reports in Celsius, your `Outdoor Temperature Entity` must also report in Celsius
- The `Minimum Comfort Temperature` and `Maximum Comfort Temperature` values should match your sensor units
- Mixing °F and °C will result in incorrect calculations

**Workaround:** Ensure all climate entities report in the same units, or use template sensors to convert them to a consistent unit.

**Future:** Automatic unit system support is planned (see [Features Planned](#features-planned))

### Start with Basic Mode
If you're new to Adaptive Cover Pro, we strongly recommend:

1. **Start with Basic Mode** - Configure and test basic sun position-based control first
2. **Understand the calculations** - Observe how your covers respond to sun position throughout the day
3. **Add Climate Mode gradually** - Once comfortable with Basic Mode, enable Climate Mode and add temperature/presence features incrementally

Climate Mode introduces additional complexity with temperature thresholds, presence detection, and weather conditions. Understanding Basic Mode operation first will help you troubleshoot issues more effectively.

### Venetian Blinds (Dual Control)
Home Assistant cover entities can only control a single dimension (position OR tilt angle, not both simultaneously). For venetian blinds that support both vertical movement and slat tilting:

**You must create TWO separate Adaptive Cover Pro instances:**
1. **Vertical instance** - Controls up/down position using the same cover entity
2. **Tilt instance** - Controls slat angle using the same cover entity

**Example:**
- Instance 1: "Adaptive Office Blind Vertical" → Controls `cover.office_blind` position (0-100%)
- Instance 2: "Adaptive Office Blind Tilt" → Controls `cover.office_blind` tilt angle (0-100%)

Both instances monitor the sun independently and send appropriate commands to the same physical device.

### Weather Entity Reliability
Weather entities in Home Assistant may not always reflect real-time conditions accurately, which can affect Climate Mode operation:

- Weather forecasts may lag actual conditions
- Some integrations update infrequently (e.g., hourly)
- Not all weather services distinguish between types of cloud cover

**Recommendations:**
- Consider using **lux sensors** or **irradiance sensors** for more accurate real-time light level detection
- The integration supports both `Lux Entity` and `Irradiance Entity` for direct sunlight measurement
- If using weather entities, verify they update frequently enough for your needs (every 5-15 minutes is ideal)

### Sensor Startup Reliability
The integration gracefully handles sensors that are unavailable during Home Assistant startup (common with Zigbee2MQTT, Z-Wave, and other hub-based devices):

- **Automatic recovery**: If temperature, lux, or irradiance sensors report `unavailable` or `None` during startup, the integration uses safe defaults and continues operating
- **Typical startup time**: Zigbee2MQTT devices often take 20-60 seconds to initialize after Home Assistant starts
- **No manual intervention required**: Once sensors become available, the integration automatically uses their values

**What happens during startup:**
- Missing lux/irradiance sensors → Defaults to "light available" (continues with weather-based operation)
- Missing temperature sensors → Defaults to "comfortable" range (uses basic glare calculations)
- Missing weather sensors → Uses sun position calculations only

This ensures your covers operate correctly even during brief sensor outages or Home Assistant restarts.

### Open/Close-Only Covers

Covers that only support OPEN and CLOSE commands (no position control) are supported with threshold-based control:

- The integration calculates position as normal (0-100%)
- If calculated position ≥ threshold → cover opens
- If calculated position < threshold → cover closes
- Default threshold is 50% (adjustable in Automation settings, 1-99%)

**Limitations:**
- Granular position control is not possible
- Intermediate positions are not available
- Tilt covers must support SET_TILT_POSITION (open/close mode not supported)

**Example Use Cases:**
- Simple roller shutters with only up/down buttons
- Garage doors with open/close only
- Budget blinds without position feedback

**Inverse State with Open/Close-Only Covers:**

The "Inverse the state" option works with open/close-only covers by inverting the calculated position **before** comparing to the threshold:

- Without inverse: Position 30% → 30% < 50% → CLOSE command
- With inverse: Position 30% → inverted to 70% → 70% ≥ 50% → OPEN command

This allows the integration to support covers with non-standard OPEN/CLOSE behavior that don't follow Home Assistant guidelines. Enable this option if your cover's OPEN and CLOSE commands appear to work backwards.

### Response Time and Control Delays

The integration uses periodic checks to balance responsiveness with system performance. Understanding these timing behaviors helps set appropriate expectations:

**Time Window Transitions (Start/End Times):**
- When start time or end time is reached, covers will respond within **1 minute**
- The integration checks time window state every minute and triggers immediate action when transitions occur
- Log messages will indicate: "Time window state changed: inactive → active" (or vice versa)

**Sun Position Changes:**
- Sun position changes trigger updates immediately
- Temperature, weather, and presence sensor changes also trigger immediate updates
- Delta position and delta time settings control how frequently covers actually move

**After Home Assistant Restart:**
- Covers are automatically repositioned during first refresh (typically within 30 seconds)
- Target positions are calculated and stored even if covers don't need to move
- Position verification begins immediately after first refresh

**Position Verification:**
- Every minute, the integration verifies covers reached their target positions
- If a mismatch is detected (e.g., cover failed to move), automatic retry occurs
- Up to 3 retry attempts before logging a warning

**Why Not Instant?**
- Periodic checks balance responsiveness with Home Assistant performance
- Prevents excessive processor usage from continuous monitoring
- 1-minute intervals are imperceptible for sun-based automation (sun moves slowly)
- Immediate triggers still available for sun position, temperature, and sensor changes

**Recommendation:**
- For time-critical automations at specific times, consider using Home Assistant automations that trigger on time patterns instead of relying on start/end times
- Start/end times are designed for daily operational windows, not precision timing

## Enhanced Geometric Accuracy

Adaptive Cover Pro includes sophisticated geometric calculations to ensure accurate sun blocking even at extreme sun angles. These improvements work automatically - no configuration required.

### Angle-Dependent Safety Margins

Safety margins automatically increase blind extension at extreme angles to compensate for geometric uncertainties:

- **Horizontal angles (gamma)**: Up to 20% increase when sun is at extreme side angles (>45° from direct front)
- **Low elevations**: Up to 15% increase when sun is near the horizon (<10° elevation)
- **High elevations**: Up to 10% increase when sun is nearly overhead (>75° elevation)
- **Combined extremes**: Margins multiply together (e.g., 85° gamma + 8° elevation ≈ 27% total increase)

Margins activate automatically, use smoothstep interpolation for smooth transitions, and are zero at normal angles (gamma < 45°, 10° < elevation < 75°). Check the "Control Status" diagnostic sensor to see when margins are active.

#### Edge Case Handling

Automatic fallback positions for extreme conditions where standard calculations become unreliable:

| Condition | Behavior | Reason |
|-----------|----------|--------|
| Elevation < 2° | Full window coverage | Sun nearly horizontal, precise calculation unreliable |
| \|Gamma\| > 85° | Full window coverage | Sun perpendicular to window, standard formula unstable |
| Elevation > 88° | Simplified calculation | Sun nearly overhead, path length correction minimal |

### Optional Window Depth

For users who want maximum precision, the **Window Depth** parameter accounts for window reveals/frames creating additional shadow at angled sun positions.

#### Configuration

Located in the vertical blind configuration screen:

**Parameter:** Window Depth (Reveal)
**Range:** 0.0 - 0.5 meters (0 - 50cm)
**Default:** 0.0 (disabled)
**Unit:** meters

**Typical values:**
- `0.0m` - Disabled (default)
- `0.05m` (2 inches) - Flush-mounted windows
- `0.10m` (4 inches) - Standard window frames
- `0.15m` (6 inches) - Deep reveals or thick walls

**How to measure:**
1. Stand outside your building
2. Measure from the outer wall surface to the inner edge of the window frame
3. Convert to meters (1 inch ≈ 0.025m, 1 foot ≈ 0.30m)

#### How It Works

Window depth creates an additional horizontal offset at angled sun positions:

```
Outer wall surface
    |
    |<-- Window Depth -->|
    |                    Window glass
                         |
                         Sun at angle
```

At angled sun positions (gamma > 10°), the window depth effectively extends the glare zone, requiring the blind to extend further to block sunlight.

**Effect magnitude:**
- **Zero effect** at gamma < 10° (sun directly in front)
- **Minimal effect** at gamma 10-30° (1-3cm additional extension)
- **Moderate effect** at gamma 30-60° (3-8cm additional extension)
- **Significant effect** at gamma > 60° (8-15cm additional extension)

**Example:**
- Window depth: 0.10m (4 inches)
- Sun angle: gamma = 45° from window normal
- Additional blind extension: ≈7cm
- Result: Tighter sun blocking at angled positions

#### When to Use Window Depth

**Enable window depth (set > 0) if:**
- You notice sun "leaking" around the blind at extreme angles
- Your windows have deep reveals (thick walls, recessed frames)
- You want maximum precision for critical applications (art preservation, glare-sensitive workspaces)
- You're willing to measure window depth accurately

**Leave at default (0.0) if:**
- Your windows are flush-mounted or nearly flush
- Current sun blocking is satisfactory
- You prefer simpler configuration

#### Backward Compatibility

- **Existing installations:** Unaffected — window_depth defaults to 0.0
- **Optional enhancement:** Set window_depth > 0 only if needed
- **No performance impact:** Adds minimal computational cost

### Technical Details

#### Safety Margin Formula

The integration calculates safety margins using smoothstep interpolation for smooth transitions:

```python
# Gamma margin (horizontal angles)
if gamma_abs > 45°:
    t = (gamma_abs - 45°) / 45°  # 0 at 45°, 1 at 90°
    smooth_t = t² × (3 - 2t)     # Smoothstep
    margin += 0.2 × smooth_t     # Up to 20%

# Elevation margins
if elevation < 10°:
    t = (10° - elevation) / 10°
    margin += 0.15 × t  # Up to 15%
elif elevation > 75°:
    t = (elevation - 75°) / 15°
    margin += 0.10 × t  # Up to 10%
```

#### Window Depth Contribution

```python
if window_depth > 0 and |gamma| > 10°:
    depth_contribution = window_depth × sin(|gamma|)
    effective_distance = base_distance + depth_contribution
```

#### Regression Testing

All enhancements are verified to:
- Maintain <5% deviation from baseline at normal angles
- Never reduce protection (always ≥ baseline position)
- Produce no NaN, infinity, or numerical errors
- Provide smooth transitions across all angle ranges

#### Test Coverage

- 34 dedicated tests for geometric accuracy
- 214 total integration tests (all passing)
- 92% code coverage on calculation engine

### Diagnostic Sensors

Enable diagnostic sensors to monitor enhanced geometric accuracy:

**Key sensors:**
- `Calculated Position` - Raw calculated position before adjustments
- `Sun Gamma` - Horizontal angle from window normal
- `Sun Elevation` - Vertical angle above horizon
- `Control Status` - Shows active safety margins and adjustments

Compare "Calculated Position" to actual cover position to see safety margin effects.

### Troubleshooting

**Q: My blinds extend more than before at extreme angles**
A: This is expected behavior. Safety margins automatically increase extension at challenging angles to ensure effective sun blocking. You can check the diagnostic sensor "Control Status" to see when margins are applied.

**Q: Should I enable window depth?**
A: Only if you notice sun leaking at extreme angles or have deep window reveals (>10cm). Most users don't need this.

**Q: Can I disable the safety margins?**
A: No, safety margins are automatic and cannot be disabled. They're essential for reliable sun blocking at extreme angles. However, margins are zero at normal angles (gamma < 45°, 10° < elevation < 75°).

**Q: How do I measure window depth accurately?**
A: Use a tape measure or ruler to measure from the outer wall surface (outside your home) to the inner edge of the window frame. If you're unsure, leave at the default (0.0) — the automatic safety margins work well without it.

## Installation

### HACS (Recommended)

Add <https://github.com/jrhubott/adaptive-cover> as custom repository to HACS.
Search and download Adaptive Cover Pro within HACS.

Restart Home-Assistant and add the integration.

### Manual

Download the `adaptive_cover_pro` folder from this github.
Add the folder to `config/custom_components/`.

Restart Home-Assistant and add the integration.

## Migrating from Adaptive Cover

Adaptive Cover Pro includes an automatic import tool to migrate your existing Adaptive Cover configurations seamlessly. This allows you to preserve all your settings without manual reconfiguration.

### How to Import

1. Install Adaptive Cover Pro from HACS (see [Installation](#installation) above)
2. Restart Home Assistant
3. Go to **Settings** → **Devices & Services** → **Add Integration**
4. Search for "Adaptive Cover Pro"
5. If you have existing Adaptive Cover installations, you'll see a menu with two options:
   - **Create new configuration** - Set up a fresh configuration
   - **Import from Adaptive Cover (X found)** - Import existing configurations
6. Select **Import from Adaptive Cover**
7. Choose which configurations to import (you can select multiple)
8. Review the configuration summary
9. Confirm the import

The import process will detect all your loaded Adaptive Cover entries and guide you through migrating them.

### What Gets Imported

All configuration settings are preserved during import:

- **Window Parameters**: Azimuth, field of view, height, distance, elevation limits
- **Cover Entities**: All associated cover entities
- **Position Settings**: Default position, min/max positions, sunset position
- **Automation Settings**:
  - Delta position and delta time thresholds
  - Start and end times (fixed times and entity-based)
  - Manual override settings (duration, threshold, reset behavior)
  - Return to sunset position option
- **Climate Mode Settings**:
  - Temperature entities and thresholds
  - Presence detection entities
  - Weather entities and sunny state definitions
  - Outside temperature thresholds
- **Advanced Features**:
  - Blind spot configurations
  - Interpolation settings
  - Transparent blind mode
  - Light sensors (lux and irradiance thresholds)

All 50+ configuration fields are mapped 1:1 from the original integration.

### After Import

Once the import completes successfully:

1. **Verify the imported configurations** work correctly
   - Check that entities are created properly
   - Verify the position calculations match your expectations
2. **Test cover movements** to ensure automation works as expected
3. **Review and adjust** any settings if needed through the Configure options
4. When you're confident everything works correctly:
   - **Disable the old Adaptive Cover integration entries** in Settings → Devices & Services
   - *Optional*: Remove the old Adaptive Cover from HACS if you no longer need it

**Important Notes:**
- Both integrations can run simultaneously during the transition period
- Your original Adaptive Cover entries remain completely unchanged
- If anything goes wrong, you can simply disable Adaptive Cover Pro and continue using the original
- Entity IDs will be different between the two integrations, so you may need to update automations and dashboards

### Import from Options Menu

You can also import additional configurations later if you add more Adaptive Cover entries:

1. Go to **Settings** → **Devices & Services**
2. Find your Adaptive Cover Pro integration
3. Click **Configure**
4. Select **Import another Adaptive Cover configuration** from the menu
5. Follow the same import process

This is useful if you want to migrate your configurations gradually or if you add new Adaptive Cover entries after the initial migration.

## Setup

Adaptive Cover Pro supports (for now) three types of covers/blinds; `Vertical` and `Horizontal` and `Venetian (Tilted)` blinds.
Each type has its own specific parameters to setup a sensor. To setup the sensor you first need to find out the azimuth of the window(s). This can be done by finding your location on [Open Street Map Compass](https://osmcompass.com/).

During setup, the integration will automatically suggest a device name based on the first cover entity you select, prefixed with "Adaptive" (e.g., "Living Room Blind" becomes "Adaptive Living Room Blind"). You can modify this suggested name if desired.

**Enhanced Configuration UI:** The setup flow includes comprehensive descriptions for every configuration field, with practical examples, recommended values, and explanations of technical terms. Each field now provides context about why it matters and how it affects cover behavior, making configuration easier for both new and experienced users.

## Cover Types

|              | Vertical                      | Horizontal                      | Tilted                          |
| ------------ | ----------------------------- | ------------------------------- | ------------------------------- |
|              | ![alt text](images/image.png) | ![alt text](images/image-2.png) | ![alt text](images/image-1.png) |
| **Movement** | Up/Down                       | In/Out                          | Tilting                         |
|              | [variables](#vertical)        | [variables](#horizontal)        | [variables](#tilt)              |
| **Note**     |                               |                                 | For venetian blinds with both vertical and tilt capabilities, see [Known Limitations](#known-limitations--best-practices) |

## Modes

This component supports two strategy modes: A `basic` mode and a `climate comfort/energy saving` mode that works with presence and temperature detection.

```mermaid
  graph TD

  A[("fa:fa-sun Sundata")]
  A --> B["Basic Mode"]
  A --> C["Climate Mode"]

  subgraph "Basic Mode"
      B --> BA("Sun within field of view")

      BA --> |No| BC{{Default}}
      BC --> BE("Time between sunset and sunrise?")
      BE --> |Yes| BF["Return default"]
      BE --> |No| BG["Return Sunset default"]

      BA --> |Yes| BD("Elevation above 0?")
      BD --> |Yes| BH{{"Calculated Position"}}
      BD --> |No| BC
  end

  subgraph "Climate Mode"
      C --> CA("Check Presence")
  end

  subgraph "Occupants"
      CA --> |True| CB("Temperature above maximum comfort (summer)?")

      CB --> |Yes| CD("Transparent blind?")
      CB --> |No| CE("Lux/Irradiance below threshold or Weather is not sunny?")

      CD --> |Yes| CF["Return fully closed (0%)"]
      CD --> |No| B

      CE --> |Yes| CG("Temperature below minimum comfort (winter) and sun infront of window and elevation > 0?")
      CE --> |No| B

      CG --> |Yes| CH["Return fully open (100%)"]
      CG --> |No| BC
  end

  subgraph "No Occupants"
      CA --> |False| CC("Sun infront of window and elevation > 0?")
      CC --> |No| BC
      CC --> |Yes| CI("Temperature above maximum comfort (summer)?")
      CI --> |Yes| CF
      CI --> |No| CJ("Temperature below minimum comfort (winter)")
      CJ --> |Yes| CH
      CJ --> |No| BC
  end
```

### Basic mode

This mode uses the calculated position when the sun is within the specified azimuth range of the window. Else it defaults to the default value or after sunset value depending on the time of day.

### Climate mode

> **⚠️ Start with Basic Mode First**
> Climate mode adds significant complexity with temperature thresholds, presence detection, and weather conditions. We recommend configuring Basic mode first and ensuring it works correctly before enabling Climate mode features.
>
> **Temperature Unit Consistency Required:** All temperature sensors must use the same unit system (°C or °F). The integration does not automatically convert between units. See [Known Limitations](#known-limitations--best-practices) for details.

This mode calculates the position based on extra parameters for presence, indoor temperature, minimal comfort temperature, maximum comfort temperature and weather (optional).
This mode is split up in two types of strategies; [Presence](https://github.com/jrhubott/adaptive-cover?tab=readme-ov-file#presence) and [No Presence](https://github.com/jrhubott/adaptive-cover?tab=readme-ov-file#no-presence).

#### Climate strategies

Climate mode uses a **priority-based decision system** to balance comfort, energy efficiency, and glare reduction:

- **No Presence**:
  Providing daylight to the room is no objective if there is no presence.

  - **Below minimal comfort temperature (Winter Mode)**:
    If the sun is above the horizon and the indoor temperature is below the minimal comfort temperature it opens the blind fully or tilt the slats to be parallel with the sun rays to allow for maximum solar radiation to heat up the room.

  - **Above maximum comfort temperature (Summer Mode)**:
    The objective is to not heat up the room any further by blocking out all possible radiation. All blinds close fully to block out light. <br> <br>
    If the indoor temperature is between both thresholds the position defaults to the set default value based on the time of day.

- **Presence** (or no Presence Entity set):
  The objective is to reduce glare while providing daylight to the room. The system uses the following priority order:

  1. **Winter Mode (Priority 1)**: When indoor temperature is below the minimal comfort threshold and the sun is in front of the window, blinds open to 100% for solar heating. This takes priority over all other conditions including light sensors and weather state.

  2. **Low Light Conditions (Priority 2)**: When it's not summer and light levels are low (lux/irradiance below threshold) or weather is not sunny, the position defaults to the configured default value to allow more sunlight while minimizing glare.

  3. **Summer Mode (Priority 3)**: When indoor temperature is above the maximum comfort threshold with transparent blinds, blinds close to 0% to block heat.

  4. **Normal Glare Calculation (Priority 4)**: In all other conditions (comfortable temperature on sunny days), uses the basic sun-tracking calculation to reduce glare while providing daylight.

  **Weather Integration**: If you configure a weather entity, the system checks if the current weather state indicates direct sunlight (default states: `sunny`, `windy`, `partlycloudy`, `cloudy` - customizable in weather options). However, winter mode (Priority 1) activates regardless of weather or light conditions when temperature thresholds are met. <br><br>
  **Tilted Blinds**: Follow the same priority system, but in summer mode (when inside temperature exceeds maximum comfort), slats are positioned at 45 degrees as this is [found optimal](https://www.mdpi.com/1996-1073/13/7/1731) for heat blocking while maintaining some light.

## Variables

### Common

![Field of View and Blind Spot diagram](images/diagram_fov.svg)

| Variables                     | Default | Range | Description                                                                                              |
| ----------------------------- | ------- | ----- | -------------------------------------------------------------------------------------------------------- |
| Entities                      | []      |       | Denotes entities controllable by the integration                                                         |
| Window Azimuth                | 180     | 0-359 | The compass direction of the window, discoverable via [Open Street Map Compass](https://osmcompass.com/) |
| Default Position              | 60      | 0-100 | Initial position of the cover in the absence of sunlight glare detection                                 |
| Minimal Position              | 100     | 0-99  | Minimal opening position for the cover, suitable for partially closing certain cover types               |
| Maximum Position              | 100     | 1-100 | Maximum opening position for the cover, suitable for partially opening certain cover types               |
| Field of view Left            | 90      | 0-180 | Unobstructed viewing angle from window center to the left, in degrees                                    |
| Field of view Right           | 90      | 0-180 | Unobstructed viewing angle from window center to the right, in degrees                                   |
| Minimal Elevation             | None    | 0-90  | Minimal elevation degree of the sun to be considered                                                     |
| Maximum Elevation             | None    | 1-90  | Maximum elevation degree of the sun to be considered                                                     |
| Default position after Sunset | 0       | 0-100 | Cover's default position from sunset to sunrise                                                          |
| Offset Sunset time            | 0       |       | Additional minutes before/after sunset                                                                   |
| Offset Sunrise time           | 0       |       | Additional minutes before/after sunrise                                                                  |
| Inverse State                 | False   |       | Calculates inverse state for covers fully closed at 100%                                                 |

#### Position Limits: Min and Max Position

The Minimal Position and Maximum Position settings create boundaries for automatic cover control. Each limit has an associated toggle that controls **when** the limit applies:

**Apply min/max only during sun tracking** (toggles):
- **Unchecked (default, recommended)**: The position limit applies **ALL THE TIME** - during sun tracking, default position, climate modes, and all other states. The cover will never go below the minimum or above the maximum value.
- **Checked (advanced)**: The position limit **ONLY applies when the sun is directly in front of the window** during active sun tracking. During default/fallback states (sun behind window, outside tracking hours, etc.), the cover can go below minimum or above maximum values.

**Most users should leave these toggles UNCHECKED** for consistent protection and predictable behavior. The "checked" option is for advanced users who want limits to apply only during active sun tracking, allowing more flexibility during other times.

**Common use cases:**
- **Minimum Position** (e.g., 20%): Prevents cover from fully closing, maintains some natural light, protects from jamming at bottom
- **Maximum Position** (e.g., 80%): Prevents cover from fully opening, maintains some privacy/shade, protects from jamming at top

#### Position Interpolation (Range Adjustment)

Position Interpolation allows you to adjust how calculated positions (0-100%) map to actual cover positions sent to your devices. This is useful for covers with non-standard behavior or limited operating ranges.

**When to use:**
- Covers that don't respond across the full 0-100% range
- Covers that need inverted operation (alternative to `inverse_state`)
- Covers requiring non-linear position mapping

**Simple Mode** (Start/End values):

Configure two values to linearly map the 0-100% calculated range to a custom output range.

| Use Case | Configuration | Result |
|----------|---------------|--------|
| **Limited Range Cover** | Start: 10%, End: 90% | 0% calculated → 10% sent<br>100% calculated → 90% sent<br>50% calculated → 50% sent |
| **Inverted Operation** | Start: 100%, End: 0% | 0% calculated → 100% sent<br>100% calculated → 0% sent<br>50% calculated → 50% sent |
| **Offset Range** | Start: 20%, End: 80% | 0% calculated → 20% sent<br>100% calculated → 80% sent<br>50% calculated → 50% sent |

**Advanced Mode** (Point Lists):

For non-linear mappings, define custom control points. Useful for covers with aggressive closing behavior or custom position curves.

**Example - Aggressive Closing:**
```
Normal List:     [0, 25, 50, 75, 100]
Interpolated List: [0, 15, 35, 60, 100]
```

This mapping causes the cover to close more aggressively:
- 0% calc → 0% sent (no change)
- 25% calc → 15% sent (closes more)
- 50% calc → 35% sent (closes more)
- 75% calc → 60% sent (closes more)
- 100% calc → 100% sent (no change)

**Example - Inverted with Custom Curve:**
```
Normal List:     [0, 25, 50, 75, 100]
Interpolated List: [100, 75, 50, 25, 0]
```

**Important Notes:**
- Interpolation is applied **AFTER** position calculation and **BEFORE** sending to cover
- Works with both position-capable and open/close-only covers
- Cannot be used together with `inverse_state` (choose one or the other)
- List mode requires at least 2 points, values must be sorted ascending in Normal List

### Vertical

![Vertical blind measurement diagram](images/diagram_vertical.svg)

| Variables         | Default | Range | Description                                                                                 |
| ----------------- | ------- | ----- | ------------------------------------------------------------------------------------------- |
| Window Height     | 2.1     | 0.1-6 | Length of fully extended cover/window                                                       |
| Glare Zone        | 0.5     | 0.1-5 | Objects within this distance of the cover recieve direct sunlight. Measured horizontally from the bottom of the cover when fully extended |

### Horizontal

![Horizontal awning measurement diagram](images/diagram_horizontal.svg)

| Variables                  | Default | Range | Description                                    |
| -------------------------- | ------- | ----- | ---------------------------------------------- |
| Awning Height              | 2       | 0.1-6 | Height from work area to awning mounting point |
| Awning Length (horizontal) | 2.1     | 0.3-6 | Length of the awning when fully extended       |
| Awning Angle               | 0       | 0-45  | Angle of the awning from the wall              |
| Glare Zone                 | 0.5     | 0.1-5 | Objects within this distance of the cover recieve direct sunlight |

### Tilt

![Venetian slat measurement diagram](images/diagram_tilt.svg)

| Variables     | Default        | Range      | Description                                                |
| ------------- | -------------- | ---------- | ---------------------------------------------------------- |
| Slat Depth    | 3 cm           | 0.1-15 cm  | Width of each slat (measure one slat front to back)        |
| Slat Distance | 2 cm           | 0.1-15 cm  | Vertical distance between slat centers                     |
| Tilt Mode     | Bi-directional |            | Mode1: 0-90°, Mode2: 0-180° slat rotation                  |

### Automation

| Variables                                  | Default      | Range | Description                                                                                    |
| ------------------------------------------ | ------------ | ----- | ---------------------------------------------------------------------------------------------- |
| Minimum Delta Position                     | 1            | 1-90  | Minimum position change required before another change can occur                               |
| Minimum Delta Time                         | 2            |       | Minimum time gap between position change                                                       |
| Start Time                                 | `"00:00:00"` |       | Earliest time a cover can be adjusted after midnight                                           |
| Start Time Entity                          | None         |       | The earliest moment a cover may be changed after midnight. _Overrides the `start_time` value_  |
| Manual Override Duration                   | `15 min`     |       | Minimum duration for manual control status to remain active                                    |
| Manual Override reset Timer                | False        |       | Resets duration timer each time the position changes while the manual control status is active |
| Manual Override Threshold                  | None         | 1-99  | Minimal position change to be recognized as manual change                                      |
| Manual Override ignore intermediate states | False        |       | Ignore StateChangedEvents that have state `opening` or `closing`                               |
| End Time                                   | `"00:00:00"` |       | Latest time a cover can be adjusted each day                                                   |
| End Time Entity                            | None         |       | The latest moment a cover may be changed . _Overrides the `end_time` value_                    |
| Adjust at end time                         | `False`      |       | Make sure to always update the position to the default setting at the end time.                |

### Climate

| Variables                     | Default | Range | Example                                       | Description                                                                                                                                          |
| ----------------------------- | ------- | ----- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Indoor Temperature Entity     | `None`  |       | `climate.living_room` \| `sensor.indoor_temp` |                                                                                                                                                      |
| Minimum Comfort Temperature   | 21      | 0-86  |                                               |                                                                                                                                                      |
| Maximum Comfort Temperature   | 25      | 0-86  |                                               |                                                                                                                                                      |
| Outdoor Temperature Entity    | `None`  |       | `sensor.outdoor_temp`                         |                                                                                                                                                      |
| Outdoor Temperature Threshold | `None`  |       |                                               | If the minimum outside temperature for summer mode is set and the outside temperature falls below this threshold, summer mode will not be activated. |
| Presence Entity               | `None`  |       |                                               |                                                                                                                                                      |
| Weather Entity                | `None`  |       | `weather.home`                                | Can also serve as outdoor temperature sensor                                                                                                         |
| Lux Entity                    | `None`  |       | `sensor.lux`                                  | Returns measured lux                                                                                                                                 |
| Lux Threshold                 | `1000`  |       |                                               | "In non-summer, above threshold, use optimal position. Otherwise, default position or fully open in winter."                                         |
| Irradiance Entity             | `None`  |       | `sensor.irradiance`                           | Returns measured irradiance                                                                                                                          |
| Irradiance Threshold          | `300`   |       |                                               | "In non-summer, above threshold, use optimal position. Otherwise, default position or fully open in winter."                                         |

### Blindspot

> The blind spot is shown as an orange shaded area within the FOV in the diagram above (see [Common](#common) section). It represents an angular range within the field of view where obstructions (trees, buildings) block direct sunlight.

| Variables            | Default | Range                 | Example | Description                                                                                                          |
| -------------------- | ------- | --------------------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| Blind Spot Left      | None    | 0-max(fov_right, 180) |         | Start point of the blind spot on the predefined field of view, where 0 is equal to the window azimuth - fov left.    |
| Blind Spot Right     | None    | 1-max(fov_right, 180) |         | End point of the blind spot on the predefined field of view, where 1 is equal to the window azimuth - fov left + 1 . |
| Blind Spot Elevation | None    | 0-90                  |         | Minimal elevation of the sun for the blindspot area.                                                                 |

## Entities

The integration dynamically adds multiple entities based on the used features.

**Note on Entity Naming:**

Entity IDs follow the pattern: `{domain}.{device_name}_{entity_name}`

Where `{device_name}` is the slugified version of the device name you configured during setup.

**Example:** For a device named "Adaptive Living Room Blind":
- `sensor.adaptive_living_room_blind_cover_position`
- `switch.adaptive_living_room_blind_automatic_control`
- `binary_sensor.adaptive_living_room_blind_sun_infront`

These entities are always available:
| Entities | Default | Description |
| --------------------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `sensor.{device_name}_cover_position` | | Reflects the current state determined by predefined settings and factors such as sun position, weather, and temperature |
| `sensor.{device_name}_control_method` | `intermediate` | **Climate Control Strategy Indicator**: Shows the active climate control strategy when climate mode is enabled. **`intermediate`** - Temperature is between comfort thresholds; uses calculated sun position. **`winter`** - Indoor temperature is below minimum comfort temperature; opens covers fully when sun is present to maximize solar heat gain. **`summer`** - Indoor temperature is above maximum comfort temperature; closes covers to block heat and prevent further temperature rise. This sensor helps you understand why covers are positioned differently based on climate conditions. |
| `sensor.{device_name}_start_sun` | | Shows the starting time when the sun enters the window's view, with an interval of every 5 minutes. |
| `sensor.{device_name}_end_sun` | | Indicates the ending time when the sun exits the window's view, with an interval of every 5 minutes. |
| `binary_sensor.{device_name}_manual_override` | `off` | Indicates if manual override is engaged for any blinds. |
| `binary_sensor.{device_name}_sun_infront` | `off` | Indicates whether the sun is in front of the window within the designated field of view. |
| `switch.{device_name}_automatic_control` | `on` | Activates the adaptive control feature. When enabled, blinds adjust based on calculated position, unless manually overridden. |
| `switch.{device_name}_manual_override` | `on` | **Manual Override Detection Switch**: Enables automatic detection of manual position changes. When enabled, the integration monitors your covers and pauses automatic control if you manually adjust a cover's position (via physical controls, app, or automation). The cover remains in manual mode for the configured duration (default: 15 minutes), after which automatic control resumes. This allows you to temporarily take control without disabling automation entirely. Turn this switch off to disable manual override detection and always apply calculated positions. |
| `switch.{device_name}_return_to_default_when_disabled` (vertical & horizontal only) | `off` | When enabled, covers automatically return to their default position when automatic control is turned off. Useful for retracting awnings or setting blinds to a safe position. |
| `button.{device_name}_reset_manual_override` | `on` | Resets manual override tags for all covers; if `switch.{device_name}_automatic_control` is on, it also restores blinds to their correct positions. |

When climate mode is setup you will also get these entities:

| Entities                                   | Default | Description                                                                                                 |
| ------------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------- |
| `switch.{device_name}_climate_mode`        | `on`    | Enables climate mode strategy; otherwise, defaults to the standard strategy.                                |
| `switch.{device_name}_outside_temperature` | `on`    | Switches between inside and outside temperatures as the basis for determining the climate control strategy. |

**Diagnostic Sensors (Optional):**

These sensors are created when diagnostics are enabled in automation settings. They help troubleshoot and monitor integration behavior.

| Entity | Default | Description |
| ------ | ------- | ----------- |
| `sensor.{device_name}_sun_azimuth` | Enabled | Current sun azimuth angle in degrees (0-360°). Verify window azimuth configuration. |
| `sensor.{device_name}_sun_elevation` | Enabled | Current sun elevation angle in degrees. Debug elevation constraints and blind spots. |
| `sensor.{device_name}_gamma` | Enabled | Surface solar azimuth - sun angle relative to window (most critical for troubleshooting). |
| `sensor.{device_name}_control_status` | Enabled | Shows why covers aren't moving: `active`, `outside_time_window`, `manual_override`, `automatic_control_off`, `sun_not_visible`, etc. |
| `sensor.{device_name}_calculated_position` | Enabled | Raw calculated position before interpolation/inversion adjustments. |
| `sensor.{device_name}_last_cover_action` | Enabled | Tracks the most recent cover action: service called, entity controlled, timestamp. Attributes include position sent, threshold used (for open/close-only covers), and whether inverse_state was applied. Useful for debugging. |
| `sensor.{device_name}_last_position_verification` | Disabled | Timestamp of the last position verification check. Attributes show per-entity verification times. |
| `sensor.{device_name}_position_verification_retries` | Disabled | Current retry count for position verification (0-3). Attributes show max retries, retries remaining, and per-entity counts. Helps identify covers that repeatedly fail to reach target positions. |
| `binary_sensor.{device_name}_position_mismatch` | Disabled | Indicates position mismatch between target and actual position (problem class). Attributes show target position sent, actual position per entity, position delta, and retry counts. Useful for troubleshooting cover movement issues. |
| `sensor.{device_name}_active_temperature` | Disabled | Currently active temperature value (climate mode only). Shows which sensor is used. Enable manually if needed. |
| `sensor.{device_name}_climate_conditions` | Disabled | Climate mode state (Summer Mode, Winter Mode, Intermediate) with condition flags as attributes (climate mode only). Enable manually if needed. |
| `sensor.{device_name}_time_window` | Disabled | Time window status (Active/Outside Window) with time details as attributes. Enable manually if needed. |
| `sensor.{device_name}_sun_validity` | Disabled | Sun validity status (Valid, In Blind Spot, Invalid Elevation) with validation details as attributes. Enable manually if needed. |

**Note:** Priority 1 sensors (last 7) are created disabled by default to reduce entity overhead. Enable them individually in the entity list if needed for troubleshooting.

## Features Planned

- Manual override controls

  - ~~Time to revert back to adaptive control~~
  - ~~Reset button~~
  - Wait until next manual/none adaptive change

- Support Home Assistant unit system (automatic conversion between °F/°C, meters/feet, etc.)
  - This will resolve the current requirement for all temperature sensors to use matching units
  - Will automatically handle conversions based on your Home Assistant unit system preference

- ~~Algorithm to control radiation and/or illumination~~


