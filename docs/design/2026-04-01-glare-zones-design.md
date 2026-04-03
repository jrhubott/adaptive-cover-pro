# Glare Zones Design Spec

**Date:** 2026-04-01
**Issue:** [#64](https://github.com/jrhubott/adaptive-cover-pro/issues/64)
**Status:** Approved — ready for implementation

## Context

Users want to block direct sunlight from specific floor areas in a room (e.g., a computer screen, a dining table, a bed) rather than using a single blanket depth from the window. The current model uses one `distance` scalar to represent how far into the room to protect. This feature extends that to up to 4 named zones, each defined as a point on the floor with a radius, independently togglable via switch entities and HA automations.

## Scope

- **Cover types:** Vertical blinds only (first iteration)
- **Zones per instance:** Up to 4
- **Zone shape:** Circle (center point + radius)
- **Zone control:** Switch entity per zone
- **Conflict resolution:** Maximum position wins (most protective)
- **Backward compatibility:** Zero behavior change when no zones are configured

---

## Coordinate System

- **Origin (0, 0):** Center of the window projected onto the floor
- **X axis:** Along the wall — positive = right when facing the window from inside
- **Y axis:** Into the room (perpendicular to the window wall) — must be positive
- **Units:** Centimeters

---

## Data Model

### New dataclasses in `config_types.py`

```python
@dataclass
class GlareZone:
    """A single glare protection zone on the floor."""
    name: str         # User-friendly name ("Desk", "Dining Table")
    x: float          # X offset from window center in cm (-500 to 500)
    y: float          # Y distance into room in cm (0 to 1000)
    radius: float     # Protection radius in cm (10 to 200)

@dataclass
class GlareZonesConfig:
    """All glare zone configuration for a vertical cover."""
    zones: list[GlareZone]   # 1 to 4 zones
    window_width: float       # Window width in cm (for ray-window intersection check)
```

### New field on `VerticalConfig`

```python
@dataclass
class VerticalConfig:
    distance: float
    h_win: float
    window_depth: float = 0.0
    sill_height: float = 0.0
    glare_zones: GlareZonesConfig | None = None  # NEW — None means feature disabled
```

---

## Geometry

### Zone-to-effective-distance conversion

New private function in `engine/covers/vertical.py`:

```python
def _glare_zone_effective_distance(
    zone: GlareZone,
    gamma: float,        # degrees, surface solar azimuth
    window_half_width: float,  # cm
) -> float | None:
    """Convert a glare zone to an effective distance for the current sun angle.

    Returns the perpendicular depth (in meters) that must be shaded to protect
    the nearest edge of the zone circle. Returns None if the sun cannot reach
    this zone through the window opening.
    """
    gamma_rad = radians(gamma)

    # Nearest point on zone circle facing the incoming sun.
    # Sun comes from direction (sin γ, −cos γ) in floor XY, so the
    # first-hit point on the circle is offset toward that source.
    nearest_x = zone.x + zone.radius * sin(gamma_rad)   # cm
    nearest_y = zone.y - zone.radius * cos(gamma_rad)   # cm

    # Zone must be in front of the window
    if nearest_y <= 0:
        return None

    # Project back to find which window X position the sun ray enters from.
    # A ray hitting floor point (fx, fy) entered the window at x_w = fx + fy * tan(γ).
    x_at_window = nearest_x + nearest_y * tan(gamma_rad)
    if abs(x_at_window) > window_half_width:
        return None   # Sun ray enters outside the window opening — zone naturally blocked

    return nearest_y / 100.0  # cm → meters
```

**Key properties:**
- The "nearest point toward the sun" is offset from center by `radius × direction_to_sun` — this ensures the entire zone circle is protected, not just its center.
- The `x_at_window` check filters out zones that the sun cannot reach through the window at the current gamma angle. This avoids over-closing the blind when the sun is approaching from a side that naturally misses the zone.
- The result is a plain distance in meters — identical units to `VerticalConfig.distance` — so it drops directly into the existing `calculate_position()` math.

---

## Changes to `calculate_position()`

In `AdaptiveVerticalCover.calculate_position()`, after the edge case check:

```python
# Compute effective distances: base + any active glare zones
distances_to_protect: list[float] = [self.distance]

if self.glare_zones and self.active_zone_names:
    window_half_width = self.glare_zones.window_width / 2.0
    for zone in self.glare_zones.zones:
        if zone.name not in self.active_zone_names:
            continue
        zone_dist = _glare_zone_effective_distance(zone, self.gamma, window_half_width)
        if zone_dist is not None:
            distances_to_protect.append(zone_dist)

effective_distance = max(distances_to_protect)
```

The rest of the calculation is unchanged — window depth, sill height, safety margins, and clipping all apply to `effective_distance` as before.

### New fields on `AdaptiveVerticalCover`

```python
glare_zones: GlareZonesConfig | None = None
active_zone_names: set[str] = field(default_factory=set)
```

`active_zone_names` is updated by the coordinator each update cycle from the zone switch states.

### Diagnostic details

`_last_calc_details` extended with:

```python
{
    "glare_zones_active": ["Desk"],              # names of active zones that affected position
    "glare_zone_distances": {"Desk": 2.45},      # effective distance per active zone (m)
    "effective_distance_source": "glare_zone",   # "base" | "glare_zone"
}
```

---

## Constants (`const.py`)

```python
CONF_ENABLE_GLARE_ZONES = "enable_glare_zones"
CONF_WINDOW_WIDTH = "window_width"
```

Per-zone option keys are not module-level constants — they're generated at runtime as `f"glare_zone_{i}_name"`, `f"glare_zone_{i}_x"`, `f"glare_zone_{i}_y"`, `f"glare_zone_{i}_radius"` where `i` is 1–4. These are used in `config_flow.py` and `configuration_service.py` directly.

---

## Config Flow (`config_flow.py`)

### Geometry step additions

- **`window_width`** slider (10–500 cm, optional, shown only for vertical covers)
- **`enable_glare_zones`** checkbox (vertical covers only)

### New step: `async_step_glare_zones`

Shown after Geometry step when `enable_glare_zones` is True and cover type is vertical.

HA config flow schemas are static — dynamic field counts based on a zone count selector are not supported in a single step. Instead, always show all 4 zone name fields. A blank or absent name means the zone is not configured. Users fill in 1–4 zones; any zone with a non-empty name gets coordinates shown on the same form.

Schema (all shown at once):
- For each zone index `i` in `[1, 4]`:
  - `glare_zone_{i}_name`: TextSelector (optional — leave blank to skip this zone)
  - `glare_zone_{i}_x`: NumberSelector (slider, -500 to 500, step 10, default 0)
  - `glare_zone_{i}_y`: NumberSelector (slider, 0 to 1000, step 10, default 100)
  - `glare_zone_{i}_radius`: NumberSelector (slider, 10 to 200, step 5, default 30)

`configuration_service.py` ignores any zone with an empty name when building `GlareZonesConfig`.

Step appears in both initial flow and options menu. Options menu conditionally adds `"glare_zones"` when `enable_glare_zones` is set.

---

## Entities

### Switch entities (one per configured zone)

Created in `switch.py` during `async_setup_entry` when `enable_glare_zones` is True:

```python
for i, zone in enumerate(glare_zones_config.zones):
    switches.append(AdaptiveCoverSwitch(
        entry_id=config_entry.entry_id,
        hass=hass,
        config_entry=config_entry,
        coordinator=coordinator,
        switch_name=f"Glare Zone: {zone.name}",
        initial_state=True,
        key=f"glare_zone_{i}",
    ))
```

- Uses `RestoreEntity` — state persists across HA restarts
- Coordinator reads `coordinator.glare_zone_0`, `coordinator.glare_zone_1`, etc. to build `active_zone_names`

### Binary sensor: `glare_active`

New `AdaptiveCoverBinarySensor` reading `coordinator.data.states["glare_active"]`:
- `True` when ≥1 zone is active AND that zone's effective distance is greater than the base distance (i.e., glare zones are actually extending the blind further than it would otherwise go)
- Useful for HA automations: "notify me when glare zones are protecting something"

---

## Coordinator Changes (`coordinator.py`)

In `_async_update_data`:

1. Read zone switch states from `self.glare_zone_0` ... `self.glare_zone_3`
2. Build `active_zone_names: set[str]` from which switches are on
3. Pass to `AdaptiveVerticalCover` via `cover_data.active_zone_names = active_zone_names`
4. After calculation, set `coordinator.data.states["glare_active"]` based on whether zones extended the position

In `get_blind_data` (via `ConfigurationService`):
- `GlareZonesConfig` is built from options and attached to `VerticalConfig.glare_zones`

---

## Configuration Service (`services/configuration_service.py`)

New method:

```python
def get_glare_zones_config(self, options: dict) -> GlareZonesConfig | None:
    """Build GlareZonesConfig from config entry options. Returns None if disabled."""
    if not options.get(CONF_ENABLE_GLARE_ZONES):
        return None
    count = int(options.get(CONF_GLARE_ZONE_COUNT, 0))
    zones = []
    for i in range(1, count + 1):
        name = options.get(f"glare_zone_{i}_name")
        if not name:
            continue
        zones.append(GlareZone(
            name=name,
            x=float(options.get(f"glare_zone_{i}_x", 0)),
            y=float(options.get(f"glare_zone_{i}_y", 100)),
            radius=float(options.get(f"glare_zone_{i}_radius", 30)),
        ))
    if not zones:
        return None
    return GlareZonesConfig(
        zones=zones,
        window_width=float(options.get(CONF_WINDOW_WIDTH, 100)),
    )
```

---

## Translations (`translations/en.json`)

New keys needed:
- `enable_glare_zones`: "Glare Zones" + description
- `glare_zone_count`: "Number of zones"
- `window_width`: "Window width (cm)"
- Per-zone: `glare_zone_{i}_name`, `glare_zone_{i}_x`, `glare_zone_{i}_y`, `glare_zone_{i}_radius`
- Switch entity names: "Glare Zone: {name}"
- Binary sensor: "Glare Active"

---

## Testing

### Unit tests (`tests/test_glare_zones.py`)

- **Geometry:** `_glare_zone_effective_distance` returns correct distance for known (x, y, r, gamma) inputs
- **Window clip:** Returns None when sun ray exits window frame
- **Depth clip:** Returns None when `nearest_y <= 0`
- **Max selection:** Cover position uses the maximum of base distance and all active zone distances
- **Inactive zones:** Disabled zones do not affect position
- **No zones configured:** `calculate_position()` result identical to pre-feature baseline
- **Edge cases:** Zones at window center, at extreme x offsets, gamma = 0

### Integration smoke test

1. Configure vertical cover with one glare zone (desk at x=50, y=200cm, r=30cm)
2. Enable the zone switch
3. At a sun angle where the zone requires more blind coverage than the base distance → cover position increases
4. Disable the zone switch → cover reverts to base distance position
5. Verify `binary_sensor.*_glare_active` reflects state correctly

---

## Files Modified

| File | Change |
|------|--------|
| `config_types.py` | Add `GlareZone`, `GlareZonesConfig` dataclasses; add `glare_zones` field to `VerticalConfig` |
| `engine/covers/vertical.py` | Add `_glare_zone_effective_distance()`, `glare_zones` and `active_zone_names` fields, update `calculate_position()` |
| `const.py` | Add `CONF_ENABLE_GLARE_ZONES`, `CONF_GLARE_ZONE_COUNT`, `CONF_WINDOW_WIDTH`, per-zone constants |
| `config_flow.py` | Add `window_width` to geometry step, add `async_step_glare_zones`, update options menu |
| `services/configuration_service.py` | Add `get_glare_zones_config()` |
| `coordinator.py` | Read zone switch states, pass `active_zone_names` to cover object, set `glare_active` state |
| `switch.py` | Create per-zone switch entities when glare zones enabled |
| `binary_sensor.py` | Add `glare_active` binary sensor |
| `translations/en.json` | New translation keys |
| `tests/test_glare_zones.py` | New test file |
