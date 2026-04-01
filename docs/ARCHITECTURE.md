# Adaptive Cover Pro - Architecture

## Overview

Adaptive Cover Pro is a Home Assistant custom integration that automatically controls blinds, awnings, and venetian blinds based on sun position. The integration uses a **layered architecture** that separates HA state access, pure calculation logic, an override priority pipeline, and focused manager classes from a thin coordinator orchestrator.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Home Assistant Core                         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                  Config Flow (UI Setup)                         │
│  - Multi-step wizard for vertical/horizontal/tilt covers        │
│  - Options flow for configuration updates                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│              AdaptiveDataUpdateCoordinator  (~1,477 lines)      │
│  - Thin orchestrator: runs update cycle, routes events          │
│  - Schedules refreshes (end-time, timed)                        │
│  - Manages toggle properties (automatic_control, etc.)          │
│  - Delegates to managers, providers, pipeline, diagnostics      │
└──┬──────────┬───────────┬────────────┬──────────────────────────┘
   │          │           │            │
   ▼          ▼           ▼            ▼
State      Managers    Pipeline    Diagnostics
Providers  (5 classes) (6 handlers) Builder
```

## Core Components

### 1. Coordinator (`coordinator.py`)

**Role:** Thin orchestrator — delegates, does not implement

**Responsibilities:**
- Runs `_async_update_data()` update cycle
- Routes entity state changes (sun, cover, motion)
- Schedules timed and end-time refreshes
- Manages toggle properties (`automatic_control`, `switch_mode`, `manual_override_mode`)
- Wires together providers, managers, pipeline, and diagnostics builder

### 2. State Providers (`state/`)

All Home Assistant state reads are isolated here. The rest of the codebase has zero HA imports for data access.

| File | Class | Reads |
|------|-------|-------|
| `climate_provider.py` | `ClimateProvider` | Temp/weather/presence/lux/irradiance entities → `ClimateReadings` frozen dataclass |
| `sun_provider.py` | `SunProvider` | Astral location from HA → pure `SunData` instance |
| `cover_provider.py` | `CoverProvider` | Cover entity state from HA (position, state) |
| `snapshot.py` | `SunSnapshot`, `CoverStateSnapshot` | Frozen dataclasses holding unified state for each update cycle |

**Benefit:** Calculation engine and pipeline handlers are fully testable without HA mocks.

### 3. Pure Calculation Engine (`calculation.py`, `sun.py`)

**Zero `homeassistant` imports.** Receives pre-computed data from providers.

#### AdaptiveGeneralCover (Base Class)
- Receives `SunData` (not `hass`)
- Shared sun position calculations, FOV validation, elevation limits, blind spot detection

#### AdaptiveVerticalCover
- Up/down blinds
- Projects sun rays to calculate required blind height
- Enhanced geometric accuracy: safety margins, edge case handling, optional window depth and sill height support
- Output: blind height in meters → percentage

#### AdaptiveHorizontalCover
- In/out awnings
- Uses vertical calculation + trigonometry for horizontal extension
- Output: extension length → percentage

#### AdaptiveTiltCover
- Slat rotation for venetian blinds
- Calculates optimal slat angle to block sun while allowing light
- Output: degrees → percentage

#### State Classes
- **NormalCoverState** — basic sun position mode
- **ClimateCoverState** — receives pre-read `ClimateReadings` from `ClimateProvider` (no direct HA reads)
  - Winter: open for solar heating
  - Summer: close for heat blocking
  - Presence-aware strategies

### 4. Manager Classes (`managers/`)

Focused classes extracted from the coordinator, each owning one responsibility:

| File | Class | Responsibility |
|------|-------|---------------|
| `manual_override.py` | `AdaptiveCoverManager` | Manual override detection and tracking |
| `grace_period.py` | `GracePeriodManager` | Per-command and startup grace periods |
| `motion.py` | `MotionManager` | Motion sensor timeout tracking |
| `position_verification.py` | `PositionVerificationManager` | Periodic position verification and retry |
| `cover_command.py` | `CoverCommandService` | Cover service calls, capability detection, delta checks |

### 5. Override Pipeline (`pipeline/`)

A pluggable priority chain replaces the previous `if/elif` override logic.

**Core types** (`pipeline/types.py`):
- `PipelineContext` — snapshot of current state passed to all handlers
- `PipelineResult` — winning handler's decision + full decision trace
- `DecisionStep` — one handler's evaluation record

**Registry** (`pipeline/registry.py`):
- `PipelineRegistry` evaluates handlers in descending priority order
- Returns the first `PipelineResult` that takes effect

**Handlers** (`pipeline/handlers/`):

| Handler | Priority | Condition |
|---------|----------|-----------|
| `force_override.py` | 100 | Force override sensor(s) active |
| `wind.py` | 95 | Wind speed exceeds threshold (stub — passes through until sensors configured) |
| `motion_timeout.py` | 80 | No motion detected within timeout |
| `manual_override.py` | 70 | User manually moved the cover |
| `climate.py` | 50 | Climate mode active and triggered |
| `solar.py` | 40 | Sun in window FOV, direct sun tracking |
| `cloud_suppression.py` | 35 | Cloud coverage suppresses solar radiation (stub — passes through until sensors configured) |
| `default.py` | 0 | Fallback (default position) |

**Adding a new override:** create one file in `pipeline/handlers/`, implement `OverrideHandler` (`pipeline/handler.py`), register in coordinator.

### 6. Diagnostics Builder (`diagnostics/builder.py`)

`DiagnosticsBuilder` with `DiagnosticContext` — extracted from coordinator.

Builds:
- Solar position diagnostics
- Position and time window diagnostics
- Sun validity diagnostics
- Climate diagnostics
- Action diagnostics
- Configuration diagnostics
- Decision trace from `PipelineResult`

### 7. Engine Modules (`engine/`)

New-generation calculation engine for advanced cover types.

| File | Class | Purpose |
|------|-------|---------|
| `engine/sun_geometry.py` | `SunGeometry` | Pure sun angle math (gamma, elevation, azimuth relationships) |
| `engine/covers/venetian.py` | `VenetianCoverCalculation` | Dual-axis venetian blind calculations (position + tilt) |

### 8. Configuration Types (`config_types.py`)

`CoverConfig` — typed dataclass consolidating all cover configuration options. Replaces raw dict lookups throughout the codebase.

### 9. Utility Modules

| File | Purpose |
|------|---------|
| `position_utils.py` | `PositionConverter`: percentage conversion and limit application |
| `geometry.py` | `SafetyMarginCalculator`, `EdgeCaseHandler` for geometric accuracy |
| `enums.py` | Type-safe enumerations (`CoverType`, `TiltMode`, `ClimateStrategy`, etc.) |
| `const.py` | Named constants for thresholds, multipliers, and defaults |
| `helpers.py` | General utility functions |
| `services/configuration_service.py` | Config entry parsing, parameter extraction |

### 10. Entity Base Classes (`entity_base.py`)

- `AdaptiveCoverBaseEntity` — shared `device_info`, coordinator handling
- `AdaptiveCoverSensorBase` — base for sensors
- `AdaptiveCoverDiagnosticSensorBase` — base for diagnostic sensors

### 11. Platform Entities

**Sensor Platform** (`sensor.py`): Cover Position (consolidated — includes position explanation and control method as attributes), Start/End Sun Times, diagnostic sensors (sun azimuth/elevation, control status, decision trace, and more)

**Switch Platform** (`switch.py`): Automatic Control, Climate Mode, Manual Override

**Binary Sensor Platform** (`binary_sensor.py`): Sun Visibility, Position Mismatch

**Button Platform** (`button.py`): Manual Override Reset

## Data Flow

```
1. Entity state change (sun / cover / motion)
        │
        ▼
2. Coordinator event handler
        │
        ▼
3. State providers build snapshot
   SunProvider  → SunData
   ClimateProvider → ClimateReadings
        │
        ▼
4. Pure calculation engine
   AdaptiveXXXCover.calculate_position()
        │
        ▼
5. Override pipeline
   PipelineRegistry.evaluate() → PipelineResult
        │
        ▼
6. Post-processing
   Interpolation, inverse state, position limits (PositionConverter)
        │
        ▼
7. Cover commands
   CoverCommandService → cover.set_cover_position
        │
        ▼
8. DiagnosticsBuilder
   Produces diagnostic data + decision trace from PipelineResult
        │
        ▼
9. coordinator.data updated → entities refresh
```

## Configuration Structure

### `config_entry.data` (Setup Phase)
- `name` — instance name
- `sensor_type` — `cover_blind` / `cover_awning` / `cover_tilt`

### `config_entry.options` (User Configurable)

**Window Properties:** `set_azimuth`, `fov_left`, `fov_right`, `min_elevation`, `max_elevation`, `window_height`, `distance_shaded_area`, `window_depth`, `sill_height`

**Position Limits:** `min_position`, `max_position`, `enable_min_position`, `enable_max_position`

**Automation:** `delta_position`, `delta_time`, `start_time`, `end_time`, `manual_override_duration`, `manual_threshold`

**Climate Mode:** `temp_entity`, `presence_entity`, `weather_entity`, `temp_low`, `temp_high`, `weather_state`, `lux_entity`, `lux_threshold`, `irradiance_entity`, `irradiance_threshold`

**Force Override:** `force_override_sensors`, `force_override_position`

**Motion Control:** `motion_sensors`, `motion_timeout`

**Blind Spots:** `blind_spot_left`, `blind_spot_right`, `blind_spot_elevation`

## Extension Points

### New Override Type
1. Create handler in `pipeline/handlers/` implementing `OverrideHandler`
2. Set priority relative to existing handlers
3. Register in coordinator

### New Cover Type
1. Create class extending `AdaptiveGeneralCover` in `calculation.py`
2. Implement `calculate_position()` and `calculate_percentage()`
3. Add `CoverType` enum value
4. Update coordinator to handle new type

### New State Source
1. Create provider in `state/` returning a frozen dataclass
2. Inject into coordinator and pass to calculation engine or pipeline context

## Testing

- **751 tests, 61% coverage**
- Calculation engine tests require no HA mocks (zero HA imports in `calculation.py` and `sun.py`)
- Each manager, pipeline handler, state provider, and engine module has dedicated test coverage
- Key test files: `tests/test_calculation.py`, `tests/test_geometric_accuracy.py`, `tests/test_motion_control.py`, `tests/test_force_override_sensors.py`, `tests/test_control_state_reason.py`, `tests/test_engine/`

## File Organization

```
custom_components/adaptive_cover_pro/
  __init__.py                        # Integration entry point
  coordinator.py                     # Thin orchestrator (~1,477 lines)
  calculation.py                     # Pure calculation engine (0 HA imports)
  sun.py                             # Pure solar calculations (0 HA imports)
  config_flow.py                     # Configuration UI
  config_types.py                    # CoverConfig typed dataclass

  engine/                            # Next-gen calculation engine
    sun_geometry.py                  # SunGeometry dataclass
    covers/
      venetian.py                    # VenetianCoverCalculation (dual-axis)

  managers/                          # Focused coordinator responsibilities
    manual_override.py               # AdaptiveCoverManager
    grace_period.py                  # GracePeriodManager
    motion.py                        # MotionManager
    position_verification.py         # PositionVerificationManager
    cover_command.py                 # CoverCommandService

  state/                             # HA boundary layer (all HA reads)
    climate_provider.py              # ClimateProvider → ClimateReadings
    cover_provider.py                # CoverProvider → cover entity state
    snapshot.py                      # SunSnapshot, CoverStateSnapshot
    sun_provider.py                  # SunProvider → SunData

  pipeline/                          # Override priority chain
    registry.py                      # PipelineRegistry
    types.py                         # PipelineContext, PipelineResult, DecisionStep
    handler.py                       # OverrideHandler abstract base
    handlers/
      force_override.py              # Priority 100
      wind.py                        # Priority 95 (stub)
      motion_timeout.py              # Priority 80
      manual_override.py             # Priority 70
      climate.py                     # Priority 50
      solar.py                       # Priority 40
      cloud_suppression.py           # Priority 35 (stub)
      default.py                     # Priority 0

  diagnostics/
    builder.py                       # DiagnosticsBuilder, DiagnosticContext

  entity_base.py                     # Base entity classes
  sensor.py                          # Sensor platform
  switch.py                          # Switch platform
  binary_sensor.py                   # Binary sensor platform
  button.py                          # Button platform
  helpers.py                         # Utility functions
  const.py                           # Constants
  enums.py                           # Type-safe enumerations
  geometry.py                        # Geometric utilities
  position_utils.py                  # Position conversion utilities
  services/
    configuration_service.py         # Config entry parsing
```

## Dependencies

**Core:**
- `homeassistant` — Home Assistant framework
- `pandas` — solar data calculations
- `numpy` — fast mathematical operations
- `astral` — sun position and timing

**Development:**
- `pytest` — testing framework
- `ruff` — linting and formatting

---

For more information, see:
- [DEVELOPMENT.md](DEVELOPMENT.md) — Developer guide
- [UNIT_TESTS.md](UNIT_TESTS.md) — Testing documentation
- [README.md](../README.md) — User documentation
