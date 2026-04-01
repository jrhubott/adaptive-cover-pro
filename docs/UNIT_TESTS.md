# Unit Tests Documentation

This document describes the unit test structure, organization, and coverage for the Adaptive Cover Pro integration.

## Overview

The test suite provides comprehensive coverage of the core calculation logic, manager classes, pipeline handlers, state providers, and diagnostics builder. Tests are organized by subsystem and use pytest with lightweight fixtures that avoid requiring a running Home Assistant instance.

**Current Status:**
- **Total Tests:** 751
- **Overall Coverage:** 61% (platform and coordinator files not yet fully tested)
- **calculation.py Coverage:** 91% (primary target achieved)
- **Pipeline handlers:** 69% each
- **State providers:** 96–100%
- **Manager classes:** 83–100%

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                            # Shared fixtures and configuration
├── test_calculation.py                    # Calculation logic (146 tests)
├── test_geometric_accuracy.py             # Geometric accuracy enhancements (34 tests)
├── test_helpers.py                        # Helper functions (32 tests)
├── test_inverse_state.py                  # Inverse state behavior (13 tests)
├── test_interpolation.py                  # Position interpolation (8 tests)
├── test_delta_position.py                 # Delta position threshold (6 tests)
├── test_manual_override.py                # Manual override detection (10 tests)
├── test_motion_control.py                 # Motion sensor-based control (31 tests)
├── test_force_override_sensors.py         # Force override sensors (13 tests)
├── test_startup_grace_period.py           # Startup grace period (8 tests)
├── test_control_state_reason.py           # Control state reason tracking (14 tests)
├── test_position_explanation.py           # Position explanation attributes (25 tests)
├── test_position_limits.py                # Min/max position limits (10 tests)
├── test_sill_height.py                    # Sill height geometric calculation (22 tests)
├── test_time_window_sensor.py             # Time window sensor (13 tests)
├── test_duplicate_sync.py                 # Duplicate sync prevention (17 tests)
├── test_device_association.py             # Device entity association (11 tests)
├── test_coordinator_logging.py            # Coordinator logging (9 tests)
├── test_active_temperature_unit.py        # Temperature unit handling (3 tests)
├── test_export_service.py                 # Export service (10 tests)
│
├── test_managers/                         # Manager unit tests
│   ├── test_grace_period.py               # GracePeriodManager (8 tests)
│   ├── test_motion.py                     # MotionManager (17 tests)
│   ├── test_position_verification.py      # PositionVerificationManager (17 tests)
│   └── test_cover_command.py              # CoverCommandService (34 tests)
│
├── test_pipeline/                         # Pipeline handler tests
│   ├── test_handlers.py                   # Individual override handlers (52 tests)
│   └── test_registry.py                   # Pipeline registry (13 tests)
│
├── test_state/                            # State provider tests
│   ├── test_climate_provider.py           # ClimateProvider (29 tests)
│   ├── test_cover_provider.py             # CoverProvider (cover entity state reads)
│   ├── test_snapshot.py                   # SunSnapshot, CoverStateSnapshot dataclasses
│   └── test_sun_provider.py               # SunProvider (4 tests)
│
├── test_engine/                           # Engine module tests
│   └── test_venetian.py                   # VenetianCoverCalculation (dual-axis)
│
└── test_diagnostics/                      # Diagnostics builder tests
    └── test_builder.py                    # DiagnosticsBuilder (48 tests)
```

### Test Markers

Tests use pytest markers to categorize:
- `@pytest.mark.unit` - Unit tests (fast, no I/O)
- `@pytest.mark.integration` - Integration tests (slower, may involve I/O)
- `@pytest.mark.asyncio` - Async tests requiring event loop

## Test Coverage by Module

### calculation.py (91% coverage, 146 tests)

**Core calculation tests in `test_calculation.py`**

Calculation tests no longer require a `hass` mock. They pass `sun_data=mock_sun_data` directly to cover constructors, keeping tests fast and HA-independent.

**Azimuth and Gamma Calculations:**
- `test_gamma_angle_calculation_sun_directly_in_front` - Gamma = 0° when sun directly ahead
- `test_gamma_angle_calculation_sun_to_left` - Positive gamma for sun to the left
- `test_gamma_angle_calculation_sun_to_right` - Negative gamma for sun to the right
- `test_gamma_wrapping_around_180` - Gamma wrapping at ±180° boundaries
- `test_azi_min_abs_standard` / `test_azi_max_abs_standard` - Standard azimuth calculations
- `test_azi_edges_calculation` - FOV edge calculation

**Sun Validity:**
- Valid/invalid sun in FOV and above/below horizon
- Direct sun validity with sunset offset
- Blind spot detection (enabled, disabled, None config, elevation limits)

**Default Position Logic:**
- Before/after sunset behavior
- Sunset position application

**AdaptiveVerticalCover (8+ tests):**
- Standard height calculation (45° sun)
- High/low sun position clipping
- Angled sun path length with gamma
- Height-to-percentage conversion

**AdaptiveHorizontalCover (7+ tests):**
- Standard awning extension
- Non-zero awning angle
- High/low sun scenarios
- Awning angle variations (0°, 15°, 30°, 45°)

**AdaptiveTiltCover (9+ tests):**
- Beta angle calculation
- Mode1 (90°) and Mode2 (180°) tilt angles
- Slat depth and distance variations
- Beta with different sun elevations

**NormalCoverState (20 tests):**
- Sun valid/invalid → calculated vs. default position
- Max/min position clamping (always and during direct sun only)
- After-sunset behavior

**ClimateCoverData:**
Pre-read values are passed directly (no HA state mocking needed for the data object itself):
- Temperature readings (outside entity, weather entity, inside sensor, climate entity)
- Presence detection (device_tracker, zone, binary_sensor, None)
- Winter/summer detection
- Weather and lux/irradiance thresholds

**ClimateCoverState (50 tests):**
- Normal/tilt cover routing
- Winter strategy: open fully when cold and sunny
- Summer strategy: close when hot
- Intermediate positions with weather awareness
- Presence vs. no-presence paths
- Max/min position clamping in climate mode

### test_geometric_accuracy.py (34 tests)

Dedicated tests for the enhanced geometric accuracy calculations added in v2.7.0:
- Safety margin behavior at all angle ranges (low, normal, high elevation; small/large gamma)
- Edge case handling at thresholds (elevation < 2°, |gamma| > 85°, elevation > 88°)
- Smooth transitions across ranges (no discontinuities)
- Regression tests (< 5% deviation at normal angles vs. baseline)
- Window depth parameter (offset when window_depth > 0 and |gamma| > 10°)
- Backward compatibility (window_depth=0 produces identical results to previous behavior)

### test_sill_height.py (22 tests)

Tests for the sill height geometric parameter:
- Sill height reduces effective distance and raises the blind
- Zero sill height produces identical results to previous behavior
- Various sill heights across elevation ranges
- Only applies to vertical covers (not horizontal or tilt)

### test_helpers.py (32 tests, ~100% coverage)

Helper function tests covering all utility functions:

**get_safe_state:** Valid numeric states, invalid states (None, unavailable, unknown), string-to-float conversion, entity not found

**get_domain:** Extracts domain from entity_id, various entity formats, None handling

**get_state_attr:** Retrieves attributes from entities, missing attribute and None entity handling

**get_position:** Cover position attribute reading, position-capable vs. open/close-only covers

**get_open_close_state:** Maps cover states to percentages (open → 100, closed → 0, invalid states)

### test_inverse_state.py (13 tests, 100% coverage)

Inverse state behavior tests (critical feature):
- Function-level inversion (0 → 100, 100 → 0, 50 → 50, intermediate values)
- Position-capable cover flow with inversion
- Open/close cover flow above/at/below threshold
- Order of operations: invert → threshold check (never reversed)
- Disabled when interpolation is active

### test_managers/ (76 tests total)

Manager tests use real manager instances with mocked dependencies (hass, logger). No HA-specific imports needed.

**test_grace_period.py (8 tests) — GracePeriodManager:**
- Startup grace period tracking (active vs. expired)
- Command-issued grace period (prevents re-issuing commands)
- Grace period reset on new commands
- Concurrent startup and command grace periods

**test_motion.py (17 tests) — MotionManager:**
- OR logic: ANY sensor detecting motion enables automatic positioning
- No-motion debounce: configurable timeout before reverting to default
- Immediate response when motion detected; delayed response when motion stops
- Empty sensor list disables the feature (backward compatible)
- Unavailable sensors treated as "off" (no motion)
- Config updates (sensor list, timeout) take effect immediately

**test_position_verification.py (17 tests) — PositionVerificationManager:**
- Position tolerance checking (within/outside threshold)
- Retry counting and max retry enforcement
- Check interval enforcement (avoids excessive verification calls)
- Reset behavior on new commands

**test_cover_command.py (34 tests) — CoverCommandService:**
- Cover service call construction (set_cover_position, open_cover, close_cover)
- Special position handling (force override position, motion timeout default)
- Grace period integration (skip commands during grace)
- build_special_positions helper function

### test_pipeline/ (65 tests total)

Pipeline handler tests are pure unit tests with no HA dependency. They work with `PipelineContext` data objects directly.

**test_handlers.py (52 tests) — Individual override handlers:**

Each handler is tested independently with a `make_ctx()` helper that builds a `PipelineContext`:

- **ForceOverrideHandler** — Overrides all other control when any force-override sensor is "on"; sets position to configured force_override_position
- **MotionTimeoutHandler** — Returns default position when motion has timed out; passes through when motion is active
- **ManualOverrideHandler** — Defers to manual control when manual override is active; passes through otherwise
- **ClimateHandler** — Uses climate-calculated position when climate mode active and position available
- **SolarHandler** — Uses sun-calculated position when direct sun is valid and within time window
- **DefaultHandler** — Always returns the configured default position (fallback)

All handlers are tested for:
- Expected `ControlMethod` enum value on output
- Correct position selection
- Pass-through behavior when handler condition is not met
- Edge cases (None positions, boundary states)

**test_registry.py (13 tests) — PipelineRegistry:**
- Handler registration order (force override → motion timeout → manual → climate → solar → default)
- `process()` returns first handler whose condition is met
- All handlers registered by default
- Custom handler lists work correctly

### test_state/ (33+ tests total)

State provider tests isolate HA-reading logic from calculation logic.

**test_cover_provider.py — CoverProvider:**
Tests for reading cover entity state (position, open/close state) from Home Assistant.

**test_snapshot.py — SunSnapshot, CoverStateSnapshot:**
Tests for the frozen dataclasses that hold unified state for each update cycle.

**test_climate_provider.py (29 tests) — ClimateProvider:**

`ClimateProvider` reads HA entity states and produces a `ClimateReadings` dataclass. Tests pass mocked `hass.states.get()` return values:

- Temperature reading from outside sensor, weather entity, inside sensor, climate entity
- Presence detection (device_tracker home/away, zone count, binary_sensor on/off, None defaults to True)
- Weather state matching against configured sunny-condition list
- Lux/irradiance threshold comparison (enabled and disabled paths)
- `ClimateReadings` dataclass field types and defaults

**test_sun_provider.py (4 tests) — SunProvider:**
- `create_sun_data()` returns a `SunData` instance
- Correct astral location lookup
- Timezone handling

### test_engine/test_venetian.py

Tests for `VenetianCoverCalculation` — dual-axis venetian blind calculations:
- Calculates both position and tilt angle simultaneously
- Handles elevation and gamma inputs from `SunGeometry`
- Edge cases for near-horizontal and near-vertical sun positions
- Regression tests against known angle combinations

### test_diagnostics/test_builder.py (48 tests)

`DiagnosticsBuilder` assembles the `position_explanation` attribute and control-state diagnostics.

Tests use a `_make_cover()` helper to construct minimal cover-like objects:
- All sun-validity decision branches (in FOV, above horizon, blind spot, sunset)
- Safety margin application in position explanation
- Climate mode branch reporting (winter open, summer close, intermediate)
- Force override, motion timeout, manual override branches
- Control method label for each `ControlMethod` enum value
- Sensor unavailability reporting

### Existing Feature Tests

| File | Tests | What it covers |
|------|-------|----------------|
| `test_interpolation.py` | 8 | Position interpolation smoothing |
| `test_delta_position.py` | 6 | Delta threshold prevents micro-movements |
| `test_manual_override.py` | 10 | Manual override detection and reset |
| `test_motion_control.py` | 31 | Full motion control integration (OR logic, debounce) |
| `test_force_override_sensors.py` | 13 | Force override sensor behavior |
| `test_startup_grace_period.py` | 8 | Startup grace period integration |
| `test_control_state_reason.py` | 14 | Control state reason tracking |
| `test_position_explanation.py` | 25 | position_explanation diagnostic attribute |
| `test_position_limits.py` | 10 | Min/max position limits |
| `test_time_window_sensor.py` | 13 | Time window sensor entities |
| `test_duplicate_sync.py` | 17 | Prevents duplicate cover commands |
| `test_device_association.py` | 11 | Entity-to-device association |
| `test_coordinator_logging.py` | 9 | Coordinator logging output |
| `test_active_temperature_unit.py` | 3 | Temperature unit (°C/°F) handling |
| `test_export_service.py` | 10 | Configuration export service |

## Running Tests

### All Tests

```bash
# Activate virtual environment (required)
source venv/bin/activate

# Run all tests with verbose output
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=custom_components/adaptive_cover_pro --cov-report=term

# Run with short traceback (faster feedback)
python -m pytest tests/ -q --tb=short
```

### Specific Test Files

```bash
# Run only calculation tests
python -m pytest tests/test_calculation.py -v

# Run only the pipeline tests
python -m pytest tests/test_pipeline/ -v

# Run all manager tests
python -m pytest tests/test_managers/ -v

# Run specific test file
python -m pytest tests/test_diagnostics/test_builder.py -v
```

### Coverage Reports

```bash
# Generate HTML coverage report
python -m pytest tests/ --cov=custom_components/adaptive_cover_pro --cov-report=html

# View report
open htmlcov/index.html
```

### One-liner

```bash
source venv/bin/activate && python -m pytest tests/ -v
```

## Fixtures

Fixtures are defined in `tests/conftest.py` and provide reusable test components.

### Core Fixtures

**`hass`**
- Mock HomeAssistant instance
- Configured with default units (°C)
- Returns None for `states.get()` by default

**`mock_logger`**
- Mock ConfigContextAdapter logger
- All logging methods mocked (debug, info, warning, error)

**`mock_sun_data`**
- Mock SunData instance
- Default values: azimuth=180°, elevation=45°
- Predictable sun position for testing — passed as `sun_data=` to cover constructors

**`mock_state`**
- Factory fixture for creating mock state objects
- Usage: `mock_state("entity_id", "state_value", {"attr": "value"})`

### Configuration Fixtures

**`sample_vertical_config`**
- Standard vertical cover configuration dictionary
- Window facing south (180°), 45° FOV each side
- Distance 0.5m, window height 2.0m

**`sample_horizontal_config`**
- Standard horizontal cover configuration
- Awning length 2.0m, angle 0°

**`sample_tilt_config`**
- Standard tilt cover configuration
- Slat depth 0.02m, distance 0.03m, mode1

**`sample_climate_config`**
- Standard climate mode configuration
- Temperature thresholds, weather conditions

### Cover Instance Fixtures

**`vertical_cover_instance`**
- Real `AdaptiveVerticalCover` instance with `sun_data=mock_sun_data`
- No `hass` required
- Fully instantiated with all parameters

**`horizontal_cover_instance`**
- Real `AdaptiveHorizontalCover` instance with `sun_data=mock_sun_data`
- Includes awning-specific parameters

**`tilt_cover_instance`**
- Real `AdaptiveTiltCover` instance with `sun_data=mock_sun_data`
- Includes slat parameters and mode

**`climate_data_instance`**
- Real `ClimateCoverData` instance with pre-read values
- Accepts pre-computed booleans (is_presence, is_sunny, etc.) instead of mocking HA state

## Test Patterns

### Testing Calculation Classes (No hass Required)

```python
@pytest.mark.unit
def test_property_name(self, vertical_cover_instance):
    """Test property with standard configuration."""
    vertical_cover_instance.sol_azi = 180.0

    result = vertical_cover_instance.property_name

    assert result == expected_value
```

### Testing Pipeline Handlers (Pure Logic)

```python
def test_solar_handler_in_time_window(self):
    """SolarHandler uses calculated position when direct sun is valid."""
    ctx = make_ctx(
        direct_sun_valid=True,
        in_time_window=True,
        calculated_position=65,
    )
    handler = SolarHandler()

    result = handler.process(ctx)

    assert result.position == 65
    assert result.control_method == ControlMethod.SOLAR
```

### Testing Manager Classes

```python
def test_grace_period_active_after_command(self, manager):
    """Grace period is active immediately after issuing a command."""
    manager.record_command_issued()

    assert manager.is_command_grace_active() is True
```

### Testing State Providers (Mock hass.states.get)

```python
@pytest.mark.unit
def test_outside_temperature_from_sensor(self, hass, mock_state, provider):
    """ClimateProvider reads outside temperature from sensor entity."""
    hass.states.get.return_value = mock_state(
        "sensor.outside_temp", "18.5", {}
    )

    readings = provider.read(config)

    assert readings.outside_temperature == 18.5
```

### Testing ClimateCoverData (Pre-read Values)

```python
@pytest.mark.unit
def test_is_winter_true(self, climate_data_instance):
    """Returns winter=True when temperature is below temp_low threshold."""
    climate_data_instance.outside_temperature = "15.0"  # below temp_low=20

    assert climate_data_instance.is_winter is True
```

### Mocking Datetime

```python
@pytest.mark.unit
@patch("custom_components.adaptive_cover_pro.calculation.datetime")
def test_with_datetime(self, mock_datetime, vertical_cover_instance):
    """Test with mocked datetime."""
    mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)

    vertical_cover_instance.sun_data.sunset = MagicMock(
        return_value=datetime(2024, 1, 1, 18, 0, 0)
    )

    result = vertical_cover_instance.sunset_valid
    assert result is False
```

### Testing Edge Cases

```python
@pytest.mark.unit
def test_edge_case_nan_handling(self, tilt_cover_instance):
    """Test edge case where calculation produces NaN."""
    try:
        result = tilt_cover_instance.calculate_percentage()
        assert 0 <= result <= 100
    except ValueError:
        # ValueError from round(NaN) is expected
        pass
```

## Coverage Goals

### Current Coverage

| Module | Coverage | Status |
|--------|----------|--------|
| calculation.py | 91% | Target achieved |
| helpers.py | ~100% | Complete |
| inverse_state behavior | 100% | Complete |
| managers/ | 83–100% | Mostly complete |
| pipeline/handlers/ | ~69% each | In progress |
| pipeline/registry.py | ~100% | Complete |
| state/climate_provider.py | 96% | Near complete |
| state/sun_provider.py | 100% | Complete |
| state/cover_provider.py | ~90% | Mostly complete |
| state/snapshot.py | ~90% | Mostly complete |
| engine/covers/venetian.py | ~90% | Mostly complete |
| diagnostics/builder.py | ~100% | Complete |
| coordinator.py | ~30% | Future work |
| sensor.py | 29% | Future work |
| switch.py | 0% | Future work |
| **Overall** | **61%** | In progress |

### Missing Coverage in calculation.py (9%)

**solar_times() method** — Requires SunData with real pandas DataFrames; integration-level test needed.

**ClimateCoverData property edge cases** — None handling paths that require specific entity configurations.

**ClimateCoverState edge cases** — Complex branching requiring specific combinations of winter/summer/presence/weather.

## Best Practices

### When Writing New Tests

1. **No hass for calculations** — Pass `sun_data=mock_sun_data` to cover constructors; avoid mocking HA state for pure calculation tests.

2. **Use pre-read values for ClimateCoverData** — Pass booleans directly (`is_presence=True`, `is_sunny=False`) rather than mocking entity states.

3. **Pipeline handlers are pure** — Use `make_ctx()` or `PipelineContext(...)` directly; no HA dependency needed.

4. **Use descriptive test names** — Format: `test_<what>_<condition>_<expected>`.

5. **One assertion per test when possible** — Makes failures easier to diagnose.

6. **Test edge cases explicitly** — Boundary values (0, 100, min, max), None/invalid inputs, wrapping/overflow.

7. **Mock external dependencies** — Always mock datetime for time-dependent tests; mock entity states via fixtures for provider tests.

8. **Use fixtures for common setup** — Add to conftest.py if used by multiple tests.

### Code Coverage Guidelines

- **90%+ coverage** for core calculation logic
- **100% coverage** for critical utility functions
- **100% coverage** for critical behaviors like inverse_state
- **Future:** Add integration tests for coordinator and platform files

## Continuous Integration

Tests run automatically on:
- Every commit via GitHub Actions
- Pull requests before merge

**CI Configuration:** `.github/workflows/test.yml`

**Python Versions Tested:**
- Python 3.11
- Python 3.12

**Home Assistant Versions:**
- Minimum: 2024.5.0
- Tested with latest stable

## Troubleshooting

### Common Test Failures

**ImportError: No module named 'pytest'**
```bash
source venv/bin/activate
pip install -e ".[dev]"
```

**datetime comparison errors**
```python
# Always mock datetime for time-dependent tests
@patch("custom_components.adaptive_cover_pro.calculation.datetime")
def test_with_time(self, mock_datetime, ...):
    mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
```

**NaN comparison failures**
```python
# Use np.isnan() or handle ValueError from round(NaN)
assert (0 <= result <= 100) or np.isnan(result)
```

**numpy.int64 vs int type errors**
```python
# Accept both types
assert isinstance(result, (int, np.integer))
```

### Running Specific Tests

```bash
# Run a single test by name
python -m pytest tests/test_calculation.py::test_gamma_angle_calculation_sun_directly_in_front -v

# Run a test class
python -m pytest tests/test_calculation.py::TestAdaptiveVerticalCover -v

# Run tests matching a keyword pattern
python -m pytest tests/ -k "blind_spot" -v

# Run all tests in a subdirectory
python -m pytest tests/test_managers/ -v
```

## Contributing

When adding new tests:

1. Follow existing test organization patterns
2. Place tests in the appropriate subdirectory (test_managers/, test_pipeline/, etc.)
3. Add fixtures to conftest.py if reusable across multiple test files
4. Use descriptive test names and docstrings
5. Test edge cases and error conditions
6. Run full test suite before committing: `source venv/bin/activate && python -m pytest tests/ -v`
7. Update this document if adding a new test category or subdirectory
