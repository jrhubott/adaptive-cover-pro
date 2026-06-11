"""Code-owned English source for the policy summary i18n (follow-up to #258).

The cover-type label and the physical-dimension / geometry block of the config
summary are policy-owned (per ``CODING_GUIDELINES.md`` — per-type labels and
geometry rendering live on the policy, never branched on cover-type strings
outside ``cover_types/``). So their English source lives here, NOT in
``config_flow._SUMMARY_LABELS_EN``.

Two dicts:

* ``COVER_TYPE_LABELS_EN`` — the 5 ``cover_types.*`` keys → English label.
* ``GEOMETRY_LABELS_EN`` — the deduplicated ``geometry.*`` keys → English
  ``str.format`` template.

Each policy method (``display_label`` / ``summary_geometry_lines`` / the shared
``window_dimensions_lines`` helper / ``computed_fov_line``) resolves with the
matching dict as its base layer, overlaid by whatever translated ``labels`` dict
it receives:

    L = {**GEOMETRY_LABELS_EN, **(labels or {})}
    L["geometry.slat.depth"].format(v=...)

so English is used when ``labels`` is ``None`` (e.g. sensor names with no flow
context) or when a key is untranslated — fully backward-compatible.

``en.json["config_summary"]["cover_types"]`` and ``...["geometry"]`` mirror
these exact keys/values (prefix-stripped). A drift test in
``tests/test_policy_summary_i18n.py`` keeps the two in lockstep, exactly like
#258's ``_SUMMARY_LABELS_EN`` ↔ en.json byte-identity guard.
"""

from __future__ import annotations

# --- Cover-type labels (namespace config_summary.cover_types.*) -------------
COVER_TYPE_LABELS_EN: dict[str, str] = {
    "cover_types.blind": "Vertical Blind",
    "cover_types.awning": "Horizontal Awning",
    "cover_types.tilt": "Venetian / Tilt Blind",
    "cover_types.oscillating_awning": "Oscillating Awning",
    "cover_types.venetian": "Venetian Blind (Dual-Axis)",
}

# --- Geometry / physical-dimension templates (namespace config_summary.geometry.*)
#
# Deduplicated: keys whose English text is byte-identical across cover types are
# shared (slat depth/spacing/mode → tilt + venetian; window height → awning +
# oscillating; window dims → blind + venetian via the shared helper).
GEOMETRY_LABELS_EN: dict[str, str] = {
    # Window-dimensions block (blind + venetian, via window_dimensions_lines).
    "geometry.window.tall": "{h}m tall window",
    "geometry.window.blocking_glass": "blocking sun {d}m from the glass",
    "geometry.window.reveal": "reveal {depth}m",
    "geometry.window.sill": "sill {sill}m",
    # Shared window-height line (awning + oscillating awning).
    "geometry.window.height": "{v}m window height",
    # Awning.
    "geometry.awning.length": "{v}m awning",
    "geometry.awning.angle": "angled at {v}°",
    "geometry.awning.blocking_wall": "blocking sun {v}m from wall",
    # Slat block (tilt + venetian).
    "geometry.slat.depth": "slat depth {v}cm",
    "geometry.slat.spacing": "spacing {v}cm",
    "geometry.slat.mode": "mode: {v}",
    # Oscillating awning.
    "geometry.oscillating.arm": "{v}m arm",
    "geometry.oscillating.sweep": "sweep {lo}°–{hi}°",
    "geometry.oscillating.housing_offset": "{v}m housing offset",
    # Venetian-only extras.
    "geometry.venetian.skip_tilt": "skip tilt when position > {skip_above}%",
    "geometry.venetian.mode_position_and_tilt": "position and tilt",
    "geometry.venetian.mode_tilt_only": "tilt only",
    "geometry.venetian.inverse_tilt": "Inverse tilt",
    "geometry.venetian.max_tilt": "max tilt {max_tilt}%",
    "geometry.venetian.min_tilt": "min tilt {min_tilt}%",
    "geometry.venetian.post_settle_hold": "post-settle hold {hold}s",
    "geometry.venetian.backrotate_lag": "back-rotate publish lag {lag}s",
    # Computed-FOV read-only line (blind + venetian Measurements mode, #565).
    "geometry.fov.computed": (
        "Computed FOV ≈ {deg}°/{deg}° " "({w} m width, {d} m reveal depth)"
    ),
}
