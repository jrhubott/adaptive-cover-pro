# Room-wide sunlight penetration height

For vertical blinds, **Cover Geometry → Sunlight penetration measurement height**
sets the horizontal plane on which the **Shaded area** distance is measured.
The input follows Home Assistant's length unit (metres or inches), appears next
to Shaded area, and defaults to zero. Existing configurations continue measuring
sunlight penetration at floor level; no migration is required.

For example, enter **48 inches** for Shaded area and **24 inches** for measurement
height to limit direct sunlight to four feet perpendicular to the window wall
on a horizontal plane two feet above the floor. Sunlight below that plane may
travel farther into the room. This is a plane-wide boundary, not a circular
furniture zone. Window Sill Height remains the physical height of the glass
bottom above the floor, and Window Height remains the height of the glass.

## Calculation

With penetration distance `D`, protected height `Z`, sill height `S`, solar
elevation `e`, and surface solar azimuth `gamma`, the exposed glass height is:

```text
opening = clamp(D * tan(e) / cos(gamma) + Z - S, 0, window_height)
```

The engine reuses its existing perpendicular height-to-distance projection.
The cosine guard, clamped low-elevation divisor, very-low-sun closed fallback,
and window-reveal full-open gate remain in place. Above roughly 2.9° elevation,
the height contribution is exactly `Z`; below that angle the existing divisor
clamp conservatively reduces it. Normal tracking and forecasts use the same
engine calculation.

Glare zones continue using their own Z height, without adding the room-wide
height again. Their priority comparison uses the room-wide boundary's
floor-equivalent distance so a zone needing extra protection can still win.

Position limits, movement minimization, minimum movement/time thresholds,
manual overrides, cloud suppression, and higher-priority handlers still apply.
In particular, five coverage steps can round a calculated 28% opening down to
20%. A minimum opening limit can permit more sun than the geometry requests.

The input is initially available only for vertical blinds (`cover_blind`).
Pitched roof windows and other cover types do not expose it.

## Runtime configuration and export

`adaptive_cover_pro.set_geometry` and `adaptive_cover_pro.set_options` accept
`protected_height` in **canonical metres**, independent of UI units. For
example, `protected_height: 0.6096` means 24 inches. Values must be finite and
between 0 and 50 metres; missing or cleared values mean zero.

The configuration summary shows nonzero measurement height. Geometry copying
includes it for compatible covers, and configuration export includes it under
`vertical.protected_height`. Nonzero-height calculation traces report
`protected_height_m` (zero when a glare-zone override supplies its own height).
