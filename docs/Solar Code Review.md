# Solar Math Code Review

**Date:** 2026-04-12
**Reviewer:** Deep dive review of all solar/geometric calculation code
**References:** ASHRAE Fundamentals Ch.14, NRC Canada CBD-59, Duffie & Beckman "Solar Engineering of Thermal Processes", MDPI Energies 2020 13(7) 1731, pvlib documentation

---

## Overall Verdict: The math is solid

All five core formulas are mathematically correct and consistent with published solar geometry references. The edge case handling is thorough with multiple safety nets. The code is well-structured with clear separation between data acquisition (astral library), geometric reasoning, and numerical stability.

---

## Files Reviewed

| File | Role |
|------|------|
| `engine/sun_geometry.py` | FOV checks, gamma, solar times, blind spot |
| `engine/covers/vertical.py` | Vertical blind shadow height |
| `engine/covers/horizontal.py` | Awning extension (law of sines) |
| `engine/covers/tilt.py` | Venetian slat angle optimization |
| `geometry.py` | Safety margins, edge case fallbacks |
| `sun.py` | Astral wrapper (5-min resolution trajectories) |
| `position_utils.py` | Percentage conversion, limits, interpolation |

---

## Formula-by-Formula Assessment

### 1. Surface Solar Azimuth (gamma)

**File:** `engine/sun_geometry.py:72`
**Formula:** `gamma = (win_azi - sol_azi + 180) % 360 - 180`
**Reference:** ASHRAE Ch.14, NRC CBD-59, Duffie & Beckman S1.6
**Verdict:** CORRECT

This is the standard wall-solar azimuth (also called Horizontal Shadow Angle / HSA) with [-180, +180] normalization. The `(x + 180) % 360 - 180` wrapping correctly handles all compass angles including north-facing windows that span the 0/360 boundary.

Downstream formulas use `cos(gamma)` and `abs(gamma)` exclusively, so the sign convention (which is the negation of the ASHRAE convention) has no effect on computed positions.

**Issue found:** The docstring (line 65-66) says "Positive values indicate sun to the right of window normal, negative to the left." This is backwards. With `win_azi - sol_azi`, if the sun is at azimuth 190 and window faces 180, gamma = 180 - 190 + 180 mod 360 - 180 = -10, meaning the sun is 10 degrees clockwise (to the right looking out) but the value is negative. Documentation-only bug; no behavioral impact.

---

### 2. Profile Angle (beta) for Venetian Blinds

**File:** `engine/covers/tilt.py:50`
**Formula:** `beta = arctan(tan(elevation) / cos(gamma))`
**Reference:** NRC CBD-59: "Tan V.S.A. = Tan Altitude Angle / Cos H.S.A."
**Verdict:** CORRECT

This is the textbook Vertical Shadow Angle (VSA) formula. It projects the 3D sun position onto the 2D plane perpendicular to the slat direction. pvlib's `projected_solar_zenith_angle` function computes a generalized version of this for arbitrary surface tilts; for a vertical surface it simplifies to this exact formula.

**Edge cases:**
- gamma near +/-90: `cos(gamma)` approaches 0, `arctan(infinity) = 90`. Physically correct (sun parallel to window projects to maximum angle). numpy handles gracefully.
- elevation near 0: `tan(0) = 0`, so beta = 0 regardless of gamma. Correct.
- elevation near 90: `tan(90)` overflows, but `arctan(inf/cos(gamma)) = 90`. numpy handles gracefully.
- gamma = 90 AND elevation = 0: produces `arctan(0/0)` = NaN. Caught by the NaN guard at line 90 which returns 0.0 (closed). Safe -- sun at horizon and parallel to window means no shading needed.

---

### 3. Optimal Venetian Slat Angle

**File:** `engine/covers/tilt.py:76-86`
**Formula:**
```
discriminant = tan(beta)^2 - (slat_distance / slat_depth)^2 + 1
slat_angle = 2 * arctan((tan(beta) + sqrt(discriminant)) / (1 + slat_distance/slat_depth))
```
**Reference:** MDPI Energies 2020, 13(7), 1731
**Verdict:** CORRECT (consistent with referenced paper)

This derives from the Weierstrass substitution (`t = tan(theta/2)`) on the cut-off angle equation -- the geometric condition where a sun ray passes exactly through the gap between two adjacent slats. The quadratic in `t` has two solutions; the `+sqrt` branch selects the maximum-opening solution that still blocks direct sun, optimizing for daylight and view while preventing glare.

**Negative discriminant:** Occurs when the ratio `slat_distance / slat_depth` is too large relative to the profile angle -- the slats are physically too far apart and too shallow to block the sun at the current angle, regardless of tilt. The code correctly returns 0.0 (fully closed) as a conservative fallback. This is the right choice.

**High profile angles:** When beta is near 90, `tan(beta)` dominates the discriminant (always positive), which is correct -- steep sun is easy to block with any tilt.

---

### 4. Vertical Blind Shadow Height

**File:** `engine/covers/vertical.py:184-189`
**Formula:** `height = (distance / cos(gamma)) * tan(elevation)`
**Reference:** Standard solar geometry; NRC CBD-59 overhang depth calculations
**Verdict:** CORRECT

The physical reasoning:
1. `distance` is the perpendicular distance from the window to the shaded area
2. `distance / cos(gamma)` corrects for oblique sun angle -- the actual path length through the room is longer by `1/cos(gamma)` when the sun arrives at angle gamma
3. `path_length * tan(elevation)` gives the vertical height the sun ray reaches

This can also be written as `distance * tan(profile_angle)` since `tan(elevation)/cos(gamma) = tan(VSA)` (the profile angle identity from Formula 2), confirming internal consistency between the vertical blind and tilt calculations.

**Safety nets (double-layered):**
- `EdgeCaseHandler` catches |gamma| > 85 and returns full coverage *before* this formula runs
- `cos_gamma` clamped to minimum 0.01 (89.4) for the 85-89.4 gap
- Result clipped to `[0, h_win]`

**Additional adjustments applied before the base calculation:**
- **Window depth** (`vertical.py:170-173`): `depth_contribution = window_depth * sin(|gamma|)` added to effective_distance when |gamma| > 10. Models extra shadow from recessed window frames at oblique angles.
- **Sill height** (`vertical.py:176-181`): `sill_offset = sill_height / max(tan(elevation), 0.05)` subtracted from effective_distance. Accounts for windows that don't start at floor level.
- **Safety margin** (`vertical.py:192-193`): Multiplicative factor (1.0 to 1.35 in practice) applied to base_height for extreme angles. Uses smoothstep for gamma contribution, linear for elevation.

---

### 5. Horizontal Awning Extension (Law of Sines)

**File:** `engine/covers/horizontal.py:49-66`
**Formula:**
```
a_angle = 90 - elevation
c_angle = elevation + awning_angle - 90
length = gap * sin(a_angle) / sin(c_angle)
```
where `gap = window_height - vertical_blind_position`
**Reference:** Basic trigonometry (law of sines on sun-ray/awning/window triangle)
**Verdict:** CORRECT

The triangle is formed by:
- The vertical gap on the window that needs shading (from where the vertical blind stops to the top)
- The awning extending outward at the mounting angle
- The sun ray connecting the far edge of the gap to the awning tip

The law of sines gives: `length / sin(A) = gap / sin(C)`, solving for the required awning extension.

**Division-by-zero guard:** When `elevation + awning_angle = 90` (c_angle = 0), the sun ray is exactly parallel to the awning surface. The awning cannot shade anything in this configuration regardless of extension. The code returns full extension as a safe fallback -- correct.

**When c_angle < 0** (`elevation + awning_angle < 90`): `sin(c_angle) < 0`, producing a negative length (sun goes under the awning). Caught by `np.clip(length, 0, ...)`.

---

## Safety Margin System

**File:** `geometry.py:24-62`

The `SafetyMarginCalculator` provides a multiplicative correction factor (always >= 1.0) to increase blind deployment at extreme angles where geometric calculations lose precision.

| Component | Trigger | Max Effect | Interpolation | Rationale |
|-----------|---------|------------|---------------|-----------|
| Gamma margin | \|gamma\| > 45 | +20% | Smoothstep (Hermite) | `cos(gamma)` loses precision at oblique angles |
| Low elevation | elev < 10 | +15% | Linear | `tan(elev)` approaches 0, shadow lengths approach infinity |
| High elevation | elev > 75 | +10% | Linear | Simplified overhead calculation less accurate |

The three components are additive. Since low and high elevation are mutually exclusive, the practical maximum is 1.35 (not the theoretical 1.45).

The smoothstep function `S(t) = 3t^2 - 2t^3` for the gamma component provides a C1-continuous (no derivative discontinuity) transition at the 45 threshold, preventing the blind from "jumping" when the sun crosses that angle.

---

## Edge Case Handling

**File:** `geometry.py:71-107`

The `EdgeCaseHandler` detects three numerically unstable regimes and returns safe fallback positions:

| Condition | Threshold | Fallback | Why |
|-----------|-----------|----------|-----|
| Very low elevation | < 2.0 | Full coverage (h_win) | `tan(elev)` near 0, shadow lengths approach infinity |
| Extreme gamma | \|gamma\| > 85 | Full coverage (h_win) | `cos(gamma)` near 0, division instability |
| Very high elevation | > 88.0 | `clip(dist * tan(elev), 0, h_win)` | Standard geometry breaks down; simplified calc |

The edge case handler runs first, before any window depth, sill height, or safety margin adjustments. This is the correct ordering -- no point applying corrections to a formula that's in an unstable regime.

---

## Solar Data Layer

**File:** `sun.py`

Wraps the `astral` library to produce:
- 5-minute resolution solar azimuth/elevation trajectories (288 points/day)
- Sunrise/sunset times with polar edge case sentinels (`00:01:00` / `23:59:59`)

The actual astronomical calculations (solar declination, hour angle, atmospheric refraction) are delegated entirely to `astral`, which implements standard algorithms. This is a good design choice -- no need to reimplement well-tested ephemeris calculations.

---

## Issues Found

### Bug: Gamma docstring sign convention is backwards
**File:** `engine/sun_geometry.py:65-66`
**Severity:** Low (documentation only, no behavioral impact)
**Details:** Docstring says "Positive values indicate sun to the right of window normal" but the formula produces the opposite. Should read positive = sun to the LEFT, or the formula could be switched to `(sol_azi - win_azi + 180) % 360 - 180` to match ASHRAE convention.

---

## Potential Improvements

### 1. Window depth formula direction (conservative but possibly inverted)
**File:** `engine/covers/vertical.py:172`
**Current:** `depth_contribution = window_depth * sin(|gamma|)` added to effective_distance (increases blind height)
**Details:** A recessed window at oblique angles means the window reveal blocks some sun, which arguably should *reduce* required blind coverage (the reveal acts as a mini overhang). The current formula is conservative (more coverage) which is safe, but may over-deploy the blind for deeply recessed windows at oblique angles. Whether this is the desired behavior depends on the product intent -- if the goal is to guarantee zero direct sun penetration, conservative is correct. If the goal is optimal comfort/energy, the formula could be refined to subtract the reveal shadow contribution.

### 2. Sill height can produce negative effective_distance
**File:** `engine/covers/vertical.py:178-181`
**Details:** `effective_distance -= sill_offset` has no floor at zero. With a high sill (e.g., 2m), short distance (0.5m), and low elevation, `sill_offset` can exceed `effective_distance`. The negative value flows to `base_height < 0`, caught by `np.clip(..., 0, h_win)` returning 0 (no blind needed). The final result is actually correct (high sill = sun can't reach the floor anyway), but intermediate negative values in debug logs could confuse troubleshooting. A `effective_distance = max(effective_distance, 0)` guard after the subtraction would improve clarity.

### 3. Horizontal awning 2x clamp is generous
**File:** `engine/covers/horizontal.py:77`
**Current:** `np.clip(length, 0, self.awn_length * 2)` -- allows reporting up to 200% extension
**Details:** Any value > `awn_length` means the awning physically cannot fully shade the window. Clamping to `awn_length` (1x) would be more physically accurate for position reporting. The 2x buffer may have been intentional for interpolation or to signal "this awning is insufficient" -- if so, documenting the rationale would be helpful.

### 4. Solar trajectory resolution (5-minute intervals)
**File:** `sun.py` (`times` property)
**Details:** 288 data points per day at 5-minute intervals. At mid-latitudes the sun moves ~15/hour in azimuth, giving ~1.25 resolution -- more than adequate. At high latitudes in summer with rapid azimuth change near the horizon, there could be 2-3 minutes of imprecision in FOV entry/exit times. Not a practical issue for most users, but a configurable resolution (1-min for polar-adjacent locations) could improve accuracy for edge cases.

### 5. Blind spot anchored to fov_left (not window normal)
**File:** `engine/sun_geometry.py:151-152`
**Current:**
```python
left_edge = self.config.fov_left - self.config.blind_spot_left
right_edge = self.config.fov_left - self.config.blind_spot_right
```
**Details:** Both edges are calculated relative to `fov_left`, not the window normal (gamma=0). This means blind spot angles are offsets from the left FOV boundary. This works correctly but may be unintuitive for users configuring the blind spot -- most would expect angles relative to center. This is a design choice, not a bug. If users report confusion, consider offering an alternative reference point.

---

## Test Coverage Gaps

The following paths lack dedicated unit tests (though some may be hit incidentally by property-based fuzz tests):

| Gap | File | Line(s) |
|-----|------|---------|
| Horizontal cover `sin_c < 1e-6` guard | `horizontal.py` | 59-64 |
| Tilt cover negative discriminant with known inputs | `tilt.py` | 76-82 |
| `solar_times()` method (full-day FOV window) | `sun_geometry.py` | 229-284 |
| Non-zero `sill_height` values | `vertical.py` | 176-181 |
| `valid_elevation` with min-only and max-only limits | `sun_geometry.py` | 89-92 |
| Exact FOV boundary (`gamma == fov_left`) | `sun_geometry.py` | valid property |
| `control_state_reason` "FOV Exit", "Elevation Limit", "Blind Spot" branches | `sun_geometry.py` | 194-200 |
| `effective_distance_override` (non-None) from GlareZoneHandler | `vertical.py` | 160-162 |

---

## Summary

The solar math implementation is well-engineered. The formulas are correct, internally consistent (the vertical blind formula and tilt formula share the same underlying profile angle identity), and well-guarded against numerical instability. The three-layer safety system (edge case handler, cos_gamma clamp, safety margins) provides defense in depth. The `astral` delegation for ephemeris calculations is the right architectural choice.

The issues found are minor: one documentation bug, one missing floor guard on an intermediate value, and a handful of test coverage gaps. No formula changes are recommended.
