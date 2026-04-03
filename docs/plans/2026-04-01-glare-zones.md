# Glare Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add up to 4 named floor zones per vertical cover, each a (x, y, radius) circle in cm, toggled via switch entities, so the blind lowers enough to shade all active zones.

**Architecture:** Glare zones extend `AdaptiveVerticalCover.calculate_position()` by computing an effective distance for each active zone and taking the max across all candidates (including the existing base distance). No pipeline changes. New `GlareZone` and `GlareZonesConfig` dataclasses carry the zone definitions; per-zone switch entities drive which zones are active each update cycle.

**Tech Stack:** Python 3.11, numpy (already used in vertical.py), Home Assistant config flow (voluptuous + selector), pytest.

**Design spec:** `docs/design/2026-04-01-glare-zones-design.md`

---

## Branch Setup

Before starting:

```bash
git checkout main
git pull origin main
git checkout -b feature/issue-64-glare-zones
source venv/bin/activate
```

---

## Task 1: GlareZone Data Model

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_types.py`
- Modify: `tests/cover_helpers.py`
- Create: `tests/test_glare_zones.py` (start here)

### Geometry coordinate system reminder

- Origin (0, 0) = window center projected onto floor
- X = along wall (positive = right when facing window from inside)
- Y = into room (always positive)
- Units = centimetres everywhere in the config; metres when passed to the existing calculation

---

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_glare_zones.py`:

```python
"""Tests for glare zone data model and geometry."""

import pytest

from custom_components.adaptive_cover_pro.config_types import (
    GlareZone,
    GlareZonesConfig,
    VerticalConfig,
)


class TestGlareZoneDataModel:
    """Test GlareZone and GlareZonesConfig dataclasses."""

    def test_glare_zone_fields(self):
        """GlareZone stores name, x, y, radius."""
        zone = GlareZone(name="Desk", x=50.0, y=200.0, radius=30.0)
        assert zone.name == "Desk"
        assert zone.x == 50.0
        assert zone.y == 200.0
        assert zone.radius == 30.0

    def test_glare_zones_config_fields(self):
        """GlareZonesConfig stores zones list and window_width."""
        zone = GlareZone(name="Table", x=0.0, y=150.0, radius=60.0)
        cfg = GlareZonesConfig(zones=[zone], window_width=120.0)
        assert len(cfg.zones) == 1
        assert cfg.window_width == 120.0

    def test_vertical_config_glare_zones_defaults_none(self):
        """VerticalConfig.glare_zones defaults to None."""
        vc = VerticalConfig(distance=0.5, h_win=2.0)
        assert vc.glare_zones is None

    def test_vertical_config_accepts_glare_zones(self):
        """VerticalConfig.glare_zones accepts a GlareZonesConfig."""
        zone = GlareZone(name="Couch", x=-80.0, y=300.0, radius=50.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        vc = VerticalConfig(distance=0.5, h_win=2.0, glare_zones=zones_cfg)
        assert vc.glare_zones is zones_cfg
```

- [ ] **Step 1.2: Run to confirm it fails**

```bash
python -m pytest tests/test_glare_zones.py -v
```

Expected: `ImportError` — `GlareZone` does not exist yet.

- [ ] **Step 1.3: Add GlareZone and GlareZonesConfig to config_types.py**

Open `custom_components/adaptive_cover_pro/config_types.py`. Add after the existing imports, before `CoverConfig`:

```python
@dataclass
class GlareZone:
    """A single glare protection zone on the floor.

    Coordinates are relative to the window centre projected onto the floor:
      x = along the wall (positive = right when facing window from inside), cm
      y = into the room (perpendicular to window), cm — must be positive
    """

    name: str
    x: float
    y: float
    radius: float


@dataclass
class GlareZonesConfig:
    """All glare zone configuration for a vertical cover."""

    zones: list[GlareZone]
    window_width: float  # cm — used to check if a sun ray can reach a zone
```

Then update `VerticalConfig` to add the optional field at the end:

```python
@dataclass
class VerticalConfig:
    """Configuration specific to vertical blinds."""

    distance: float
    h_win: float
    window_depth: float = 0.0
    sill_height: float = 0.0
    glare_zones: GlareZonesConfig | None = None
```

- [ ] **Step 1.4: Run to confirm tests pass**

```bash
python -m pytest tests/test_glare_zones.py::TestGlareZoneDataModel -v
```

Expected: 4 PASSED.

- [ ] **Step 1.5: Update cover_helpers.py so make_vertical_config accepts glare_zones**

Open `tests/cover_helpers.py`. In `make_vertical_config`, add `glare_zones` to defaults:

```python
def make_vertical_config(**overrides) -> VerticalConfig:
    """Create a VerticalConfig with sensible defaults and optional overrides."""
    defaults = {
        "distance": 0.5,
        "h_win": 2.0,
        "window_depth": 0.0,
        "sill_height": 0.0,
        "glare_zones": None,
    }
    defaults.update(overrides)
    return VerticalConfig(**defaults)
```

Also add `"glare_zones"` to `_VERT_CONFIG_FIELDS` so `build_vertical_cover` routes it correctly:

```python
_VERT_CONFIG_FIELDS = {"distance", "h_win", "window_depth", "sill_height", "glare_zones"}
```

- [ ] **Step 1.6: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v --tb=short -q
```

Expected: all existing tests still PASS, 4 new tests PASS.

- [ ] **Step 1.7: Commit**

```bash
git add custom_components/adaptive_cover_pro/config_types.py tests/cover_helpers.py tests/test_glare_zones.py
git commit -m "feat(glare-zones): Add GlareZone and GlareZonesConfig data model"
```

---

## Task 2: Geometry Function — Zone to Effective Distance

**Files:**
- Modify: `custom_components/adaptive_cover_pro/engine/covers/vertical.py`
- Modify: `tests/test_glare_zones.py`

### Math recap

For a zone at (x_c, y_c) with radius r and sun surface azimuth gamma (degrees):

1. Sun comes from direction `(sin γ, −cos γ)` on the floor. The first-hit point on the zone circle is:
   ```
   nearest_x = x_c + r · sin(γ)
   nearest_y = y_c − r · cos(γ)
   ```

2. If `nearest_y ≤ 0`, the zone is behind the window wall — skip (return None).

3. A sun ray hitting floor point `(fx, fy)` entered the window at `x_w = fx + fy · tan(γ)`. Check:
   ```
   x_at_window = nearest_x + nearest_y · tan(γ)
   if |x_at_window| > window_half_width: return None
   ```

4. Return `nearest_y / 100.0` (cm → metres). This is the effective distance fed into the existing calculation.

---

- [ ] **Step 2.1: Write failing tests**

Add this class to `tests/test_glare_zones.py`:

```python
from math import cos, radians, sin, tan

from custom_components.adaptive_cover_pro.config_types import GlareZone
from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    _glare_zone_effective_distance,
)


class TestGlareZoneGeometry:
    """Test _glare_zone_effective_distance."""

    def test_zone_directly_in_front_gamma_zero(self):
        """Zone centred on window normal, gamma=0 → nearest_y = y - radius."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=30.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # nearest_y = 200 - 30*cos(0) = 170 cm → 1.70 m
        assert dist == pytest.approx(1.70, abs=1e-6)

    def test_zone_on_right_side_gamma_zero(self):
        """Zone offset to the right, gamma=0, centred ray still passes through window."""
        zone = GlareZone(name="Z", x=50.0, y=100.0, radius=0.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # x_at_window = 50 + 100*tan(0) = 50 < 150 → reachable; nearest_y = 100
        assert dist == pytest.approx(1.00, abs=1e-6)

    def test_zone_behind_window_wall_returns_none(self):
        """Zone with y ≤ radius (nearest_y ≤ 0) is behind the wall."""
        zone = GlareZone(name="Z", x=0.0, y=20.0, radius=30.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=150.0)
        # nearest_y = 20 - 30 = -10 → None
        assert dist is None

    def test_zone_outside_window_width_returns_none(self):
        """Zone whose sun ray enters outside the window frame → None."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=0.0)
        # gamma=0: x_at_window = 0 + 200*tan(0) = 0; well within any window
        # But with a very large positive x and small window:
        zone2 = GlareZone(name="Z2", x=200.0, y=100.0, radius=0.0)
        # x_at_window = 200 + 100*tan(0) = 200; window_half_width=50 → outside
        dist = _glare_zone_effective_distance(zone2, gamma=0.0, window_half_width=50.0)
        assert dist is None

    def test_zone_angled_sun_reachable(self):
        """Zone at centre, moderate gamma: check the nearest_y is correct."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=0.0)
        gamma = 30.0
        gamma_rad = radians(gamma)
        # nearest_x = 0 + 0*sin(30) = 0; nearest_y = 200 - 0*cos(30) = 200
        # x_at_window = 0 + 200*tan(30) ≈ 115.47
        x_at_window = 200 * tan(gamma_rad)
        # With window_half_width=150, 115.47 < 150 → reachable
        dist = _glare_zone_effective_distance(zone, gamma=gamma, window_half_width=150.0)
        assert dist == pytest.approx(2.00, abs=1e-6)

    def test_zone_outside_window_angle_returns_none(self):
        """Zone at (0, 200), gamma=30°, narrow window (half=50cm) → None."""
        zone = GlareZone(name="Z", x=0.0, y=200.0, radius=0.0)
        # x_at_window ≈ 115.47 > 50 → outside window
        dist = _glare_zone_effective_distance(zone, gamma=30.0, window_half_width=50.0)
        assert dist is None

    def test_returns_metres_not_cm(self):
        """Result is in metres (nearest_y / 100)."""
        zone = GlareZone(name="Z", x=0.0, y=300.0, radius=0.0)
        dist = _glare_zone_effective_distance(zone, gamma=0.0, window_half_width=200.0)
        assert dist == pytest.approx(3.00, abs=1e-6)
```

- [ ] **Step 2.2: Run to confirm tests fail**

```bash
python -m pytest tests/test_glare_zones.py::TestGlareZoneGeometry -v
```

Expected: `ImportError` — `_glare_zone_effective_distance` does not exist.

- [ ] **Step 2.3: Implement the geometry function in vertical.py**

Open `custom_components/adaptive_cover_pro/engine/covers/vertical.py`. Add to the imports at the top (numpy's `tan` is already imported):

The existing imports already include `from numpy import cos, sin, tan` and `from numpy import radians as rad`. No new imports needed — but `GlareZone` must be imported:

```python
from ...config_types import GlareZonesConfig, GlareZone, VerticalConfig
```

(The existing import is just `from ...config_types import VerticalConfig` — extend it.)

Then add the module-level function just before the `AdaptiveVerticalCover` class definition:

```python
def _glare_zone_effective_distance(
    zone: GlareZone,
    gamma: float,
    window_half_width: float,
) -> float | None:
    """Convert a glare zone to an effective distance (metres) for this sun angle.

    Returns the perpendicular depth into the room (in metres) that the blind
    must shade to protect the nearest edge of the zone circle. Returns None if
    the sun cannot reach this zone through the window opening at angle gamma.

    Args:
        zone: The glare zone definition (x, y in cm, radius in cm).
        gamma: Surface solar azimuth in degrees (positive = sun to the right).
        window_half_width: Half the window width in cm.

    """
    gamma_rad = rad(gamma)

    # First-hit point on the zone circle: the point facing the incoming sun.
    # Sun arrives from direction (sin γ, −cos γ) on the floor XY plane,
    # so the facing point is offset from centre in that direction.
    nearest_x = zone.x + zone.radius * float(sin(gamma_rad))
    nearest_y = zone.y - zone.radius * float(cos(gamma_rad))

    # Zone must be in front of the window wall
    if nearest_y <= 0:
        return None

    # Project back to find where the sun ray enters the window.
    # A ray hitting floor point (fx, fy) entered at x_w = fx + fy * tan(γ).
    x_at_window = nearest_x + nearest_y * float(tan(gamma_rad))
    if abs(x_at_window) > window_half_width:
        return None  # Ray enters outside the window opening — zone is naturally blocked

    return nearest_y / 100.0  # cm → metres
```

- [ ] **Step 2.4: Run geometry tests**

```bash
python -m pytest tests/test_glare_zones.py::TestGlareZoneGeometry -v
```

Expected: 7 PASSED.

- [ ] **Step 2.5: Run full suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 2.6: Commit**

```bash
git add custom_components/adaptive_cover_pro/engine/covers/vertical.py tests/test_glare_zones.py
git commit -m "feat(glare-zones): Add _glare_zone_effective_distance geometry function"
```

---

## Task 3: Integrate Glare Zones into calculate_position()

**Files:**
- Modify: `custom_components/adaptive_cover_pro/engine/covers/vertical.py`
- Modify: `tests/test_glare_zones.py`

`AdaptiveVerticalCover` is a `@dataclass`. New fields with defaults go at the end of the field list (after `vert_config` which already defaults to `None`).

---

- [ ] **Step 3.1: Write failing tests**

Add this class to `tests/test_glare_zones.py`:

```python
from unittest.mock import MagicMock

from tests.cover_helpers import build_vertical_cover, make_vertical_config


class TestGlareZoneCalculation:
    """Test glare zone integration in AdaptiveVerticalCover.calculate_position()."""

    def _make_cover(self, glare_zones=None, active_zone_names=None, **kwargs):
        """Build a vertical cover with optional glare zone config."""
        logger = MagicMock()
        sun_data = MagicMock()
        cover = build_vertical_cover(
            logger=logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=sun_data,
            distance=0.5,    # 0.5m base distance
            h_win=2.0,
            glare_zones=glare_zones,
            **kwargs,
        )
        cover.active_zone_names = active_zone_names or set()
        return cover

    def test_no_zones_configured_unchanged(self):
        """With no glare zones, calculate_position() is identical to baseline."""
        cover_no_zones = self._make_cover(glare_zones=None)
        baseline = cover_no_zones.calculate_position()

        # Same setup, explicitly empty zones
        cover_empty = self._make_cover(
            glare_zones=GlareZonesConfig(zones=[], window_width=200.0)
        )
        result = cover_empty.calculate_position()
        assert result == pytest.approx(baseline, rel=1e-6)

    def test_active_zone_farther_than_base_extends_position(self):
        """Zone farther than base distance → higher blind position."""
        # base distance 0.5m; zone at y=200cm (2.0m), gamma≈0 → effective=1.7m > 0.5m
        zone = GlareZone(name="Desk", x=0.0, y=200.0, radius=30.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Desk"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result > baseline

    def test_active_zone_closer_than_base_does_not_reduce(self):
        """Zone closer than base distance → position is still the base (max wins)."""
        # base distance 0.5m (50cm); zone at y=30cm, radius=0 → effective=0.30m < 0.5m
        zone = GlareZone(name="Near", x=0.0, y=30.0, radius=0.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Near"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)

    def test_inactive_zone_does_not_affect_position(self):
        """Zone present in config but not in active_zone_names → ignored."""
        zone = GlareZone(name="Desk", x=0.0, y=200.0, radius=30.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=200.0)
        # Zone configured but NOT active
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names=set())

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)

    def test_multiple_zones_max_wins(self):
        """Two active zones: position equals the farther one."""
        zone1 = GlareZone(name="Near", x=0.0, y=80.0, radius=0.0)   # 0.8m
        zone2 = GlareZone(name="Far", x=0.0, y=250.0, radius=0.0)   # 2.5m (→ clips to h_win)
        zones_cfg = GlareZonesConfig(zones=[zone1, zone2], window_width=200.0)
        cover = self._make_cover(
            glare_zones=zones_cfg,
            active_zone_names={"Near", "Far"},
            distance=0.5,
        )
        cover_far_only = self._make_cover(
            glare_zones=GlareZonesConfig(zones=[zone2], window_width=200.0),
            active_zone_names={"Far"},
        )
        result = cover.calculate_position()
        far_result = cover_far_only.calculate_position()
        assert result == pytest.approx(far_result, rel=1e-6)

    def test_zone_unreachable_through_window_falls_back_to_base(self):
        """Zone whose sun ray exits the window frame → treated as if inactive."""
        # Narrow window (5cm half-width), zone far to the side → x_at_window > 5
        zone = GlareZone(name="Corner", x=300.0, y=200.0, radius=0.0)
        zones_cfg = GlareZonesConfig(zones=[zone], window_width=10.0)  # 5cm half-width
        cover = self._make_cover(glare_zones=zones_cfg, active_zone_names={"Corner"})

        baseline_cover = self._make_cover(glare_zones=None)
        baseline = baseline_cover.calculate_position()
        result = cover.calculate_position()

        assert result == pytest.approx(baseline, rel=1e-6)
```

- [ ] **Step 3.2: Run to confirm they fail**

```bash
python -m pytest tests/test_glare_zones.py::TestGlareZoneCalculation -v
```

Expected: `AttributeError` — `active_zone_names` attribute not found on the cover.

- [ ] **Step 3.3: Add fields and update calculate_position() in vertical.py**

In `custom_components/adaptive_cover_pro/engine/covers/vertical.py`:

**Add `field` to the dataclasses import** at the top:
```python
from dataclasses import dataclass, field
```

**Add two new fields to `AdaptiveVerticalCover`** (after `vert_config`):

```python
@dataclass
class AdaptiveVerticalCover(AdaptiveGeneralCover):
    """Calculate state for Vertical blinds."""

    vert_config: VerticalConfig = None  # type: ignore[assignment]
    glare_zones: GlareZonesConfig | None = None
    active_zone_names: set[str] = field(default_factory=set)
```

**Update `calculate_position()`** — add zone distance gathering after the edge case check and before the `effective_distance` line. Replace the single-line `effective_distance = self.distance` with:

```python
        # Gather all distances to protect: base + any active glare zones
        distances_to_protect: list[float] = [self.distance]
        glare_zones_contributing: list[str] = []

        if self.glare_zones and self.active_zone_names:
            window_half_width = self.glare_zones.window_width / 2.0
            for zone in self.glare_zones.zones:
                if zone.name not in self.active_zone_names:
                    continue
                zone_dist = _glare_zone_effective_distance(
                    zone, self.gamma, window_half_width
                )
                if zone_dist is not None:
                    distances_to_protect.append(zone_dist)
                    glare_zones_contributing.append(zone.name)

        effective_distance = max(distances_to_protect)
        effective_distance_source = (
            "glare_zone" if glare_zones_contributing else "base"
        )
```

**Also update `_last_calc_details`** at the end of `calculate_position()`. In the non-edge-case path, extend the dict:

```python
        self._last_calc_details = {
            "edge_case_detected": False,
            "safety_margin": round(safety_margin, 4),
            "effective_distance": round(effective_distance, 4),
            "window_depth_contribution": round(depth_contribution, 4),
            "sill_height_offset": round(sill_offset, 4),
            "glare_zones_active": glare_zones_contributing,
            "effective_distance_source": effective_distance_source,
        }
```

> **Important:** The existing code already uses `effective_distance` as a variable name in the non-edge-case path. After adding the zone block above, make sure there is no second assignment to `effective_distance` lower in the method — the existing `effective_distance = self.distance` line must be removed and replaced by the zone block above.

The full updated flow after the edge case check should be:

```python
        # Gather all distances to protect: base + any active glare zones
        distances_to_protect: list[float] = [self.distance]
        glare_zones_contributing: list[str] = []

        if self.glare_zones and self.active_zone_names:
            window_half_width = self.glare_zones.window_width / 2.0
            for zone in self.glare_zones.zones:
                if zone.name not in self.active_zone_names:
                    continue
                zone_dist = _glare_zone_effective_distance(
                    zone, self.gamma, window_half_width
                )
                if zone_dist is not None:
                    distances_to_protect.append(zone_dist)
                    glare_zones_contributing.append(zone.name)

        effective_distance = max(distances_to_protect)
        effective_distance_source = (
            "glare_zone" if glare_zones_contributing else "base"
        )
        depth_contribution = 0.0
        if self.window_depth > 0 and abs(self.gamma) > WINDOW_DEPTH_GAMMA_THRESHOLD:
            depth_contribution = self.window_depth * sin(rad(abs(self.gamma)))
            effective_distance += depth_contribution

        sill_offset = 0.0
        if self.sill_height > 0:
            sill_offset = self.sill_height / max(tan(rad(self.sol_elev)), 0.05)
            effective_distance -= sill_offset

        path_length = effective_distance / cos(rad(self.gamma))
        base_height = path_length * tan(rad(self.sol_elev))

        safety_margin = self._calculate_safety_margin(self.gamma, self.sol_elev)
        adjusted_height = base_height * safety_margin
        result = float(np.clip(adjusted_height, 0, self.h_win))

        self.logger.debug(
            "Vertical calc: elev=%.1f°, gamma=%.1f°, dist=%.3f→%.3f (depth=%.3f, sill=%.3f), "
            "base=%.3f, margin=%.3f, adjusted=%.3f, clipped=%.3f, source=%s",
            self.sol_elev,
            self.gamma,
            self.distance,
            effective_distance,
            depth_contribution,
            sill_offset,
            base_height,
            safety_margin,
            adjusted_height,
            result,
            effective_distance_source,
        )
        self._last_calc_details = {
            "edge_case_detected": False,
            "safety_margin": round(safety_margin, 4),
            "effective_distance": round(effective_distance, 4),
            "window_depth_contribution": round(depth_contribution, 4),
            "sill_height_offset": round(sill_offset, 4),
            "glare_zones_active": glare_zones_contributing,
            "effective_distance_source": effective_distance_source,
        }
        return result
```

- [ ] **Step 3.4: Run the new tests**

```bash
python -m pytest tests/test_glare_zones.py -v
```

Expected: all PASSED.

- [ ] **Step 3.5: Run full suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 3.6: Commit**

```bash
git add custom_components/adaptive_cover_pro/engine/covers/vertical.py tests/test_glare_zones.py
git commit -m "feat(glare-zones): Integrate active glare zones into calculate_position()"
```

---

## Task 4: Constants and Configuration Service

**Files:**
- Modify: `custom_components/adaptive_cover_pro/const.py`
- Modify: `custom_components/adaptive_cover_pro/services/configuration_service.py`
- Create: `tests/test_glare_zones_config_service.py`

---

- [ ] **Step 4.1: Write failing tests**

Create `tests/test_glare_zones_config_service.py`:

```python
"""Tests for GlareZonesConfig building from config entry options."""

import pytest

from custom_components.adaptive_cover_pro.config_types import GlareZone, GlareZonesConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_WINDOW_WIDTH,
)
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)
from unittest.mock import MagicMock


def _make_service():
    """Create a ConfigurationService with mocked HA dependencies."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Cover"}
    logger = MagicMock()
    return ConfigurationService(
        hass=hass,
        config_entry=config_entry,
        logger=logger,
        cover_type="cover_blind",
        temp_toggle=False,
        lux_toggle=False,
        irradiance_toggle=False,
    )


class TestGetGlareZonesConfig:
    """Test ConfigurationService.get_glare_zones_config."""

    def test_returns_none_when_disabled(self):
        """Returns None when CONF_ENABLE_GLARE_ZONES is False."""
        svc = _make_service()
        options = {CONF_ENABLE_GLARE_ZONES: False}
        result = svc.get_glare_zones_config(options)
        assert result is None

    def test_returns_none_when_missing(self):
        """Returns None when CONF_ENABLE_GLARE_ZONES not in options."""
        svc = _make_service()
        result = svc.get_glare_zones_config({})
        assert result is None

    def test_returns_none_when_no_named_zones(self):
        """Returns None when all zone names are blank."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 200.0,
            "glare_zone_1_name": "",
        }
        result = svc.get_glare_zones_config(options)
        assert result is None

    def test_builds_single_zone(self):
        """Builds a GlareZonesConfig with one zone."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 150.0,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 50.0,
            "glare_zone_1_y": 200.0,
            "glare_zone_1_radius": 30.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert isinstance(result, GlareZonesConfig)
        assert result.window_width == 150.0
        assert len(result.zones) == 1
        assert result.zones[0].name == "Desk"
        assert result.zones[0].x == 50.0
        assert result.zones[0].y == 200.0
        assert result.zones[0].radius == 30.0

    def test_skips_blank_zone_names(self):
        """Zones with blank names are skipped; named ones are included."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            CONF_WINDOW_WIDTH: 200.0,
            "glare_zone_1_name": "Table",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 150.0,
            "glare_zone_1_radius": 60.0,
            "glare_zone_2_name": "",   # blank — skip
            "glare_zone_3_name": "Bed",
            "glare_zone_3_x": -80.0,
            "glare_zone_3_y": 300.0,
            "glare_zone_3_radius": 50.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert len(result.zones) == 2
        names = [z.name for z in result.zones]
        assert "Table" in names
        assert "Bed" in names

    def test_window_width_defaults_to_100(self):
        """window_width defaults to 100cm when not set."""
        svc = _make_service()
        options = {
            CONF_ENABLE_GLARE_ZONES: True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 100.0,
            "glare_zone_1_radius": 20.0,
        }
        result = svc.get_glare_zones_config(options)
        assert result is not None
        assert result.window_width == 100.0
```

- [ ] **Step 4.2: Run to confirm they fail**

```bash
python -m pytest tests/test_glare_zones_config_service.py -v
```

Expected: `ImportError` — `CONF_ENABLE_GLARE_ZONES` does not exist.

- [ ] **Step 4.3: Add constants to const.py**

Open `custom_components/adaptive_cover_pro/const.py`. Add after `CONF_CLOUD_COVERAGE_THRESHOLD`:

```python
CONF_ENABLE_GLARE_ZONES = "enable_glare_zones"
CONF_WINDOW_WIDTH = "window_width"
```

- [ ] **Step 4.4: Add get_glare_zones_config to ConfigurationService**

Open `custom_components/adaptive_cover_pro/services/configuration_service.py`.

Add to the imports from `..const`:
```python
    CONF_ENABLE_GLARE_ZONES,
    CONF_WINDOW_WIDTH,
```

Add to the imports from `..config_types`:
```python
from ..config_types import CoverConfig, GlareZone, GlareZonesConfig, HorizontalConfig, TiltConfig, VerticalConfig
```

Add the new method at the end of the class:

```python
    def get_glare_zones_config(self, options: dict) -> GlareZonesConfig | None:
        """Build GlareZonesConfig from config entry options.

        Returns None if glare zones are disabled or no zones have names.
        """
        if not options.get(CONF_ENABLE_GLARE_ZONES):
            return None

        zones = []
        for i in range(1, 5):  # zones 1–4
            name = options.get(f"glare_zone_{i}_name", "")
            if not name:
                continue
            zones.append(
                GlareZone(
                    name=name,
                    x=float(options.get(f"glare_zone_{i}_x", 0)),
                    y=float(options.get(f"glare_zone_{i}_y", 100)),
                    radius=float(options.get(f"glare_zone_{i}_radius", 30)),
                )
            )

        if not zones:
            return None

        return GlareZonesConfig(
            zones=zones,
            window_width=float(options.get(CONF_WINDOW_WIDTH, 100)),
        )
```

- [ ] **Step 4.5: Run new tests**

```bash
python -m pytest tests/test_glare_zones_config_service.py -v
```

Expected: all PASSED.

- [ ] **Step 4.6: Run full suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 4.7: Commit**

```bash
git add custom_components/adaptive_cover_pro/const.py \
        custom_components/adaptive_cover_pro/services/configuration_service.py \
        tests/test_glare_zones_config_service.py
git commit -m "feat(glare-zones): Add constants and configuration service method"
```

---

## Task 5: Config Flow — Glare Zones as Top-Level Menu Item

**Design change (user feedback):** Glare Zones must appear as a permanent top-level menu item in the options flow for all vertical covers — not gated behind an enable toggle in the geometry step. The enable toggle (`CONF_ENABLE_GLARE_ZONES`) and `window_width` field move inside the glare_zones step itself. In the initial setup flow, the glare_zones step always follows geometry for vertical covers.

**Files:**
- Modify: `custom_components/adaptive_cover_pro/config_flow.py`

No unit tests for config flow (it's HA UI code, 0% coverage by convention — verified manually via `./scripts/develop`).

---

- [ ] **Step 5.1: Add CONF_ENABLE_GLARE_ZONES and CONF_WINDOW_WIDTH to the import list in config_flow.py**

Open `custom_components/adaptive_cover_pro/config_flow.py`. In the `from .const import (...)` block, add:

```python
    CONF_ENABLE_GLARE_ZONES,
    CONF_WINDOW_WIDTH,
```

- [ ] **Step 5.2: Add SensorType import if not present**

Search: `grep -n "SensorType" custom_components/adaptive_cover_pro/config_flow.py`

If not found, add to imports:

```python
from .enums import SensorType
```

- [ ] **Step 5.3: Define _build_glare_zones_schema at module level**

Add after `GEOMETRY_VERTICAL_SCHEMA` (around line 157), before `GEOMETRY_HORIZONTAL_SCHEMA`.

The glare_zones step includes the enable toggle, window_width, and all 4 zone slots in one form:

```python
def _build_glare_zones_schema(options: dict | None = None) -> vol.Schema:
    """Build the glare zones schema: enable toggle, window width, and 4 zone slots."""
    opts = options or {}
    schema_dict: dict = {
        vol.Optional(CONF_ENABLE_GLARE_ZONES, default=opts.get(CONF_ENABLE_GLARE_ZONES, False)): (
            selector.BooleanSelector()
        ),
        vol.Optional(CONF_WINDOW_WIDTH, default=opts.get(CONF_WINDOW_WIDTH, 100)): (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=500, step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        ),
    }
    for i in range(1, 5):
        prefix = f"glare_zone_{i}"
        schema_dict[vol.Optional(f"{prefix}_name", default=opts.get(f"{prefix}_name", ""))] = (
            selector.TextSelector()
        )
        schema_dict[vol.Optional(f"{prefix}_x", default=opts.get(f"{prefix}_x", 0))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-500, max=500, step=10,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        )
        schema_dict[vol.Optional(f"{prefix}_y", default=opts.get(f"{prefix}_y", 100))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1000, step=10,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        )
        schema_dict[vol.Optional(f"{prefix}_radius", default=opts.get(f"{prefix}_radius", 30))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=200, step=5,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="cm",
                )
            )
        )
    return vol.Schema(schema_dict)
```

- [ ] **Step 5.4: Add async_step_glare_zones to ConfigFlowHandler (initial setup)**

In `ConfigFlowHandler`, `async_step_geometry` (around line 893) currently ends with `return await self.async_step_sun_tracking()`. Update it to always chain to glare_zones for vertical covers:

```python
    async def async_step_geometry(self, user_input: dict[str, Any] | None = None):
        """Configure cover geometry dimensions."""
        if user_input is not None:
            self.config.update(user_input)
            if self.type_blind == SensorType.BLIND:
                return await self.async_step_glare_zones()
            return await self.async_step_sun_tracking()

        schema = _get_geometry_schema(self.type_blind)
        return self.async_show_form(step_id="geometry", data_schema=schema)

    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (initial flow)."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_sun_tracking()

        schema = _build_glare_zones_schema(self.config)
        return self.async_show_form(step_id="glare_zones", data_schema=schema)
```

- [ ] **Step 5.5: Add async_step_glare_zones to AdaptiveCoverOptionsFlow**

In `AdaptiveCoverOptionsFlow`, add the method after `async_step_geometry` (around line 1377):

```python
    async def async_step_glare_zones(self, user_input: dict[str, Any] | None = None):
        """Configure glare zone definitions (options)."""
        if user_input is not None:
            self.options.update(user_input)
            return await self.async_step_init()

        schema = _build_glare_zones_schema(self.options)
        return self.async_show_form(
            step_id="glare_zones",
            data_schema=self.add_suggested_values_to_schema(schema, self.options),
        )
```

- [ ] **Step 5.6: Add glare_zones as a permanent top-level menu item in async_step_init**

In `async_step_init` (around line 1327), add `"glare_zones"` **always** for vertical covers — not behind any conditional. Add it after the `blind_spot` / `interp` conditional block, before `"automation"`:

```python
        if self.sensor_type == SensorType.BLIND:
            menu_options.append("glare_zones")
```

- [ ] **Step 5.7: Verify config flow runs without errors**

```bash
python -c "from custom_components.adaptive_cover_pro import config_flow; print('OK')"
```

Expected: `OK` printed, no import errors.

- [ ] **Step 5.8: Run full test suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 5.9: Commit**

```bash
git add custom_components/adaptive_cover_pro/config_flow.py
git commit -m "feat(glare-zones): Add glare_zones as top-level options menu item"
```

---

## Task 6: Coordinator — Wire Zone States to Cover Object

**Files:**
- Modify: `custom_components/adaptive_cover_pro/coordinator.py`

No unit tests for coordinator (HA integration code, ~34% coverage). Tested via `./scripts/develop`.

The coordinator must:
1. In `get_blind_data` (which builds the cover calculation object), attach `GlareZonesConfig` from options via `ConfigurationService`.
2. In `_async_update_data` (after building the cover object), read each zone switch attribute and build `active_zone_names`.
3. After calculation, populate `coordinator.data.states["glare_active"]`.

---

- [ ] **Step 6.1: Find the relevant section in coordinator.py**

```bash
grep -n "get_blind_data\|get_vertical_data\|_config_service" custom_components/adaptive_cover_pro/coordinator.py | head -30
```

Note the line numbers for:
- `get_blind_data` — where `VerticalConfig` is built from `_config_service.get_vertical_data()`
- `_async_update_data` — where `cover_data` is used and `PipelineContext` is built

- [ ] **Step 6.2: Add glare zones config to get_blind_data**

In `coordinator.py`, find `get_blind_data`. Locate the section that builds `AdaptiveVerticalCover`. It currently does something like:

```python
vert_config = self._config_service.get_vertical_data(options)
cover_data = AdaptiveVerticalCover(
    ...
    vert_config=vert_config,
    ...
)
```

Add glare zones support. Immediately after `vert_config = ...`:

```python
            glare_zones_cfg = self._config_service.get_glare_zones_config(options)
            vert_config = VerticalConfig(
                distance=vert_config.distance,
                h_win=vert_config.h_win,
                window_depth=vert_config.window_depth,
                sill_height=vert_config.sill_height,
                glare_zones=glare_zones_cfg,
            )
```

Then set `active_zone_names` after building the cover object:

```python
            if glare_zones_cfg:
                active_names = set()
                for idx, zone in enumerate(glare_zones_cfg.zones):
                    if getattr(self, f"glare_zone_{idx}", True):
                        active_names.add(zone.name)
                cover_data.active_zone_names = active_names
```

> **Note:** Zone switch attributes are named `glare_zone_0`, `glare_zone_1`, etc. on the coordinator (matching the switch `key` from Task 7). Default to `True` (active) if the attribute hasn't been set yet (first run before switch state is restored).

- [ ] **Step 6.3: Add glare_active to coordinator data states**

In `_async_update_data`, after the pipeline runs and `pipeline_result` is obtained, set `glare_active`. Find where `data.states["manual_override"]` or similar states are set, and add:

```python
            # Glare zones active: any zone contributed a larger distance than base
            if cover_data_obj and hasattr(cover_data_obj, "_last_calc_details"):
                details = getattr(cover_data_obj, "_last_calc_details", {})
                glare_active = bool(details.get("glare_zones_active"))
            else:
                glare_active = False
            states["glare_active"] = glare_active
```

> `cover_data_obj` is the name of the calculation object in the coordinator — check the actual variable name in `_async_update_data`. It may be `cover_data` or `blind_data`. Use `grep -n "cover_data\|blind_data" coordinator.py` to find it.

- [ ] **Step 6.4: Run full test suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 6.5: Commit**

```bash
git add custom_components/adaptive_cover_pro/coordinator.py
git commit -m "feat(glare-zones): Wire glare zone states through coordinator"
```

---

## Task 7: Switch Entities — Per-Zone Toggles

**Files:**
- Modify: `custom_components/adaptive_cover_pro/switch.py`

No unit tests for platform code — tested via `./scripts/develop`.

---

- [ ] **Step 7.1: Add CONF_ENABLE_GLARE_ZONES and CONF_SENSOR_TYPE imports to switch.py**

Open `switch.py`. The existing imports already include `CONF_SENSOR_TYPE`. Add `CONF_ENABLE_GLARE_ZONES` to the `from .const import (...)` block.

Also add `GlareZonesConfig` import via the configuration service — instead, import the config constants and access options directly.

- [ ] **Step 7.2: Add glare zone switch creation to async_setup_entry**

At the end of `async_setup_entry` in `switch.py`, before `async_add_entities(switches)`, add:

```python
    # Glare zone switches — one per configured zone for vertical covers
    if sensor_type == "cover_blind" and config_entry.options.get(CONF_ENABLE_GLARE_ZONES):
        for idx in range(1, 5):
            zone_name = config_entry.options.get(f"glare_zone_{idx}_name", "")
            if not zone_name:
                continue
            switches.append(
                AdaptiveCoverSwitch(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    coordinator,
                    f"Glare Zone: {zone_name}",
                    True,  # default on
                    f"glare_zone_{idx - 1}",  # key: glare_zone_0, glare_zone_1, ...
                )
            )
```

> The key `f"glare_zone_{idx - 1}"` uses 0-based indexing so it matches the coordinator lookup `getattr(self, "glare_zone_0", True)` etc.

- [ ] **Step 7.3: Verify no import errors**

```bash
python -c "from custom_components.adaptive_cover_pro import switch; print('OK')"
```

Expected: `OK`.

- [ ] **Step 7.4: Run full test suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 7.5: Commit**

```bash
git add custom_components/adaptive_cover_pro/switch.py
git commit -m "feat(glare-zones): Create per-zone switch entities"
```

---

## Task 8: Binary Sensor — Glare Active

**Files:**
- Modify: `custom_components/adaptive_cover_pro/binary_sensor.py`

No unit tests for platform code — tested via `./scripts/develop`.

---

- [ ] **Step 8.1: Add glare_active binary sensor to async_setup_entry**

Open `binary_sensor.py`. In `async_setup_entry`, after the `entities.extend([binary_sensor, manual_override])` line, add:

```python
    # Glare active sensor — only created when glare zones are configured
    from .const import CONF_ENABLE_GLARE_ZONES, CONF_SENSOR_TYPE
    if (
        config_entry.options.get(CONF_ENABLE_GLARE_ZONES)
        and config_entry.data.get(CONF_SENSOR_TYPE) == "cover_blind"
    ):
        glare_active_sensor = AdaptiveCoverBinarySensor(
            config_entry,
            config_entry.entry_id,
            "Glare Active",
            False,
            "glare_active",
            BinarySensorDeviceClass.RUNNING,
            coordinator,
        )
        entities.append(glare_active_sensor)
```

> `CONF_SENSOR_TYPE` is already imported at the top of `binary_sensor.py` (check — if not, add to the `from .const import` block). `CONF_ENABLE_GLARE_ZONES` must also be imported.

- [ ] **Step 8.2: Move CONF imports to top-of-file (clean up the inline import)**

After confirming it works, move the `from .const import` lines added in 8.1 to the existing `from .const import DOMAIN` line at the top of the file:

```python
from .const import CONF_ENABLE_GLARE_ZONES, CONF_SENSOR_TYPE, DOMAIN
```

And remove the inline import inside `async_setup_entry`.

- [ ] **Step 8.3: Verify no import errors**

```bash
python -c "from custom_components.adaptive_cover_pro import binary_sensor; print('OK')"
```

Expected: `OK`.

- [ ] **Step 8.4: Run full test suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 8.5: Commit**

```bash
git add custom_components/adaptive_cover_pro/binary_sensor.py
git commit -m "feat(glare-zones): Add glare_active binary sensor"
```

---

## Task 9: Translations

**Files:**
- Modify: `custom_components/adaptive_cover_pro/translations/en.json`

No tests — translations are loaded at runtime by HA.

---

- [ ] **Step 9.1: Find the relevant sections in en.json**

```bash
grep -n "blind_spot\|sill_height\|step.*geometry\|step.*blind_spot" \
  custom_components/adaptive_cover_pro/translations/en.json | head -30
```

This shows you where the blind_spot step is structured (use as a model for glare_zones).

- [ ] **Step 9.2: Add the glare_zones step**

In the `"step"` section, add a new `"glare_zones"` entry. Pattern it after `"blind_spot"`. The step includes the enable toggle and window_width (they live here, not in geometry):

```json
"glare_zones": {
  "title": "Glare Zones",
  "description": "Define up to 4 floor areas to protect from direct sunlight. Leave a zone name blank to skip it. Coordinates are relative to the window centre projected onto the floor.",
  "data": {
    "enable_glare_zones": "Enable Glare Zones",
    "window_width": "Window Width",
    "glare_zone_1_name": "Zone 1 Name",
    "glare_zone_1_x": "Zone 1 X Position",
    "glare_zone_1_y": "Zone 1 Y Position",
    "glare_zone_1_radius": "Zone 1 Radius",
    "glare_zone_2_name": "Zone 2 Name",
    "glare_zone_2_x": "Zone 2 X Position",
    "glare_zone_2_y": "Zone 2 Y Position",
    "glare_zone_2_radius": "Zone 2 Radius",
    "glare_zone_3_name": "Zone 3 Name",
    "glare_zone_3_x": "Zone 3 X Position",
    "glare_zone_3_y": "Zone 3 Y Position",
    "glare_zone_3_radius": "Zone 3 Radius",
    "glare_zone_4_name": "Zone 4 Name",
    "glare_zone_4_x": "Zone 4 X Position",
    "glare_zone_4_y": "Zone 4 Y Position",
    "glare_zone_4_radius": "Zone 4 Radius"
  },
  "data_description": {
    "glare_zone_1_name": "Name for this zone (e.g. 'Desk'). Leave blank to skip.",
    "glare_zone_1_x": "Left/right offset from window centre in cm. Positive = right.",
    "glare_zone_1_y": "Distance into the room in cm (perpendicular to window).",
    "glare_zone_1_radius": "Protection radius around the zone centre in cm."
  }
}
```

- [ ] **Step 9.4: Add switch translation keys**

In the `"entity"` → `"switch"` section, add translation keys for the zone switches. The switch uses `_attr_translation_key = key` where key = `"glare_zone_0"` etc. Add:

```json
"glare_zone_0": {"name": "Glare Zone 1"},
"glare_zone_1": {"name": "Glare Zone 2"},
"glare_zone_2": {"name": "Glare Zone 3"},
"glare_zone_3": {"name": "Glare Zone 4"}
```

> Note: The switch `name` property is set directly from the zone name string (e.g. `"Glare Zone: Desk"`), so the translation key is a fallback. The actual displayed name comes from the `name` property override in `AdaptiveCoverSwitch`.

- [ ] **Step 9.5: Verify JSON is valid**

```bash
python -c "import json; json.load(open('custom_components/adaptive_cover_pro/translations/en.json')); print('JSON OK')"
```

Expected: `JSON OK`.

- [ ] **Step 9.6: Run full test suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing.

- [ ] **Step 9.7: Commit**

```bash
git add custom_components/adaptive_cover_pro/translations/en.json
git commit -m "feat(glare-zones): Add translations for glare zone config and entities"
```

---

## Task 10: Lint and Final Verification

- [ ] **Step 10.1: Run ruff**

```bash
ruff check custom_components/adaptive_cover_pro/engine/covers/vertical.py \
           custom_components/adaptive_cover_pro/config_types.py \
           custom_components/adaptive_cover_pro/services/configuration_service.py \
           custom_components/adaptive_cover_pro/config_flow.py \
           custom_components/adaptive_cover_pro/switch.py \
           custom_components/adaptive_cover_pro/binary_sensor.py \
           custom_components/adaptive_cover_pro/coordinator.py \
           --fix
ruff format custom_components/ tests/
```

Expected: no unfixable errors.

- [ ] **Step 10.2: Run full test suite with coverage**

```bash
python -m pytest tests/ -v --tb=short \
  --cov=custom_components/adaptive_cover_pro/engine/covers/vertical \
  --cov=custom_components/adaptive_cover_pro/config_types \
  --cov=custom_components/adaptive_cover_pro/services/configuration_service \
  --cov-report=term-missing
```

Expected: all passing, `engine/covers/vertical.py` and `configuration_service.py` show the new code covered.

- [ ] **Step 10.3: Commit lint fixes if any**

```bash
git add -u
git commit -m "chore: Apply ruff formatting to glare zones implementation"
```

(Skip this commit if there were no changes.)

- [ ] **Step 10.4: Manual smoke test with ./scripts/develop**

1. Start: `./scripts/develop`
2. Go to Settings → Integrations → Add Integration → Adaptive Cover Pro
3. Select "Vertical blind"
4. In the Geometry step: set a window width, enable "Glare Zones"
5. In the Glare Zones step: enter "Desk" for Zone 1, set X=50, Y=200, Radius=30
6. Complete the setup flow
7. Verify `switch.*_glare_zone_desk` appears in the entity list, defaulting to ON
8. Verify `binary_sensor.*_glare_active` appears
9. When the sun is in the FOV, verify the cover position is influenced by the zone
10. Toggle the switch OFF — verify cover returns to base-distance position

---

## File Change Summary

| File | Type | Change |
|------|------|--------|
| `config_types.py` | Modify | Add `GlareZone`, `GlareZonesConfig`; add `glare_zones` to `VerticalConfig` |
| `engine/covers/vertical.py` | Modify | Add `_glare_zone_effective_distance()`, `glare_zones`/`active_zone_names` fields, update `calculate_position()` |
| `const.py` | Modify | Add `CONF_ENABLE_GLARE_ZONES`, `CONF_WINDOW_WIDTH` |
| `services/configuration_service.py` | Modify | Add `get_glare_zones_config()` |
| `config_flow.py` | Modify | Add fields to geometry schema, add `_build_glare_zones_schema()`, add `async_step_glare_zones` to both flows, update options menu |
| `coordinator.py` | Modify | Attach `GlareZonesConfig` and `active_zone_names` to cover object, set `glare_active` state |
| `switch.py` | Modify | Create per-zone switch entities |
| `binary_sensor.py` | Modify | Add `glare_active` binary sensor |
| `translations/en.json` | Modify | Add geometry step fields, glare_zones step, switch keys |
| `tests/cover_helpers.py` | Modify | Add `glare_zones` to `make_vertical_config` and `_VERT_CONFIG_FIELDS` |
| `tests/test_glare_zones.py` | Create | Geometry and calculation tests |
| `tests/test_glare_zones_config_service.py` | Create | Config service tests |
