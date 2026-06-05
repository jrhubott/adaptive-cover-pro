# Proposed Wiki Changes — Multi-Entity Cover Types (Dual Panel & Split Panel)

> **Status:** proposal for review. These pages are **not** yet pushed to the
> wiki repo. Once approved, create `Dual-Panel-Covers.md` and
> `Split-Panel-Covers.md` in `../adaptive-cover-pro.wiki/`, add the sidebar
> entries below, and push.
>
> Conventions reminder (from `CLAUDE.md`): wiki links use `[text](Page-Name)`
> (never `#anchor-only`); images use absolute raw URLs; the wiki publishes on
> commit (no review gate).

---

## New page: `Dual-Panel-Covers.md`

```markdown
# Dual Panel (sheer + blackout)

A **dual panel** cover controls **two full-height vertical shades layered
front-to-back** on the same window, as two separate Home Assistant cover
entities:

- **Front (sheer)** — a light-filtering shade that tracks the sun exactly like
  a normal [vertical blind](Configuration-Vertical), filtering glare while
  letting diffuse daylight through.
- **Back (blackout)** — an opaque shade that stays open and only **deploys
  (closes)** when conditions call for a full block.

This matches a common real-world setup: a day/night or sheer + blackout
dual-roller on one window.

## How it decides positions

Each update cycle:

| Panel | Behaviour |
|-------|-----------|
| **Front** | Gets the normal adaptive sun-tracking position — the same value a single vertical blind would use. |
| **Back** | **Open** by default; **closes** when any configured *blackout trigger* is active. |

### Blackout triggers

The back shade deploys when **any** selected trigger is active:

- **Climate** — the climate strategy calls for a full heat block (summer
  cooling, or winter insulation). Requires [Climate Mode](Configuration-Climate)
  to be configured.
- **Sunset** — the astronomical sunset / return-to-default (privacy) window is
  active.

Both triggers are enabled by default. Safety overrides
([force override](Configuration-Force-Override) and
[weather](Configuration-Weather-Override)) close **both** panels regardless of
the trigger selection.

## Configuration

1. Add a new Adaptive Cover Pro instance and choose **Dual panel (sheer +
   blackout)** as the cover type.
2. On the **Covers & Device** step, pick:
   - **Front Panel (sheer)** — the light-filtering cover entity.
   - **Back Panel (blackout)** — the opaque cover entity.
3. Enter the shared [window geometry](Configuration-Vertical) (both panels span
   the same window).
4. Configure sun tracking, climate, and the other sections as usual.

> **Tip:** both entities must be real HA `cover` entities that accept
> `set_cover_position`. They are driven independently — moving one by hand
> triggers [manual override](Manual-Override) for that panel only; the other
> keeps tracking.

## Entities

A dual-panel instance exposes two extra sensors alongside the standard ones:

- **Target Front** — the resolved position for the sheer front.
- **Target Back** — the resolved position for the blackout back (0 % / 100 %
  in the simple case, mirrored to the dispatched value otherwise).

See [Sensors & Entities](Entities-Sensors) for the full list.

## Notes

- **Inverse state** is honoured for both panels.
- The front and back are **uncoupled** — there is no physical interference
  between two separate shades. If you have *one fabric split into two sections*
  instead, use a [Split Panel](Split-Panel-Covers).
```

---

## New page: `Split-Panel-Covers.md`

```markdown
# Split Panel (top + bottom)

A **split panel** cover is a **single vertical shade split into a top section
and a bottom section**, driven as two separate Home Assistant cover entities.
The two sections share one fabric, so their coverage cannot overlap.

Current behaviour — **"bottom blocks, top free"**:

- **Bottom section** — tracks the sun exactly like a normal
  [vertical blind](Configuration-Vertical), rising from the sill to block the
  direct-sun band.
- **Top section** — stays **open** for daylight and view.

Because the top section stays open, the two sections never overlap on the
shared fabric, so the one-fabric constraint is satisfied automatically.

## How it decides positions

| Section | Behaviour |
|---------|-----------|
| **Bottom** | Gets the normal adaptive sun-tracking position. |
| **Top** | Stays open. |

Safety overrides ([force override](Configuration-Force-Override) and
[weather](Configuration-Weather-Override)) close **both** sections.

## Configuration

1. Add a new Adaptive Cover Pro instance and choose **Split panel (top +
   bottom)** as the cover type.
2. On the **Covers & Device** step, pick:
   - **Top Section** — the upper cover entity.
   - **Bottom Section** — the lower cover entity.
3. Enter the shared [window geometry](Configuration-Vertical).
4. Configure sun tracking and the other sections as usual.

## Entities

A split-panel instance exposes two extra sensors:

- **Target Top** — the resolved position for the upper section.
- **Target Bottom** — the resolved position for the lower section (the
  sun-tracking value).

## Notes

- **Inverse state** is honoured for both sections.
- Split panel vs **[Dual Panel](Dual-Panel-Covers)**: a split panel is *one
  fabric in two sections* (coupled); a dual panel is *two separate shades*
  layered front-to-back (uncoupled, with a distinct sheer/blackout behaviour).
```

---

## `_Sidebar.md` additions

Under the **Configuration** group, after the existing cover-type pages
(Vertical / Horizontal / Tilt / Venetian), add:

```markdown
  - [Dual Panel](Dual-Panel-Covers)
  - [Split Panel](Split-Panel-Covers)
```

---

## Cross-page updates to consider

- **Features / cover-types overview page:** add Dual Panel and Split Panel to
  the list of supported cover types with one-line descriptions.
- **[Sensors & Entities](Entities-Sensors):** document the four new per-role
  Target sensors (Target Front / Target Back / Target Top / Target Bottom) and
  note they appear only for the matching multi-entity cover type.
- **Diagnostics page:** mention the new `panel_targets` map in the diagnostics
  dump (role → resolved position), present only for dual-/split-panel instances.

---

## Implementation reference (not for the wiki — for the maintainer)

- Cover types: `cover_dual_panel`, `cover_split_panel`
  (`custom_components/adaptive_cover_pro/cover_types/dual_panel.py`,
  `split_panel.py`, shared base `_multi_entity.py`).
- Engines: `engine/covers/layered.py` (dual), `engine/covers/dual_section.py`
  (split).
- Dispatch: both ride the unified `CoverTypePolicy.resolve_axis_targets` →
  `AxisTarget` model; two role-tagged entities, each an `INDEPENDENT` position
  target.
- Config keys: `dual_panel_front` / `dual_panel_back` /
  `dual_panel_blackout_triggers`; `split_panel_top` / `split_panel_bottom`.
- Backward compatibility: additive only — no config-entry migration, no
  version bump. Existing cover types are behaviourally unchanged. A
  newly-created dual-/split-panel entry will fail to load if the integration is
  rolled back to a version that predates these types (no data loss; other
  covers unaffected).
```
