"""Tilt-only cover policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import (
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_MIN_TILT,
    CONF_MIN_TILT_SUN_ONLY,
    CONF_TILT_ANGLE_0,
    CONF_TILT_ANGLE_100,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_HORIZONTAL_PERCENT,
    CONF_TILT_MODE,
    CONF_TILT_SAFETY_MARGIN,
    CONF_VENETIAN_TILT_TRANSFORM,
    DEFAULT_MAX_TILT,
    DEFAULT_MAX_TILT_SUN_ONLY,
    DEFAULT_MIN_TILT,
    DEFAULT_MIN_TILT_SUN_ONLY,
    DEFAULT_TILT_ANGLE_0,
    DEFAULT_TILT_ANGLE_100,
    DEFAULT_TILT_HORIZONTAL_PERCENT,
    DEFAULT_TILT_SAFETY_MARGIN,
    DEFAULT_VENETIAN_TILT_TRANSFORM,
    MAX_TILT_SAFETY_MARGIN,
    MIN_TILT_SAFETY_MARGIN,
    OPTION_RANGES,
    TILT_HORIZONTAL_DEG,
    VENETIAN_TILT_TRANSFORMS,
)
from ..engine.covers import AdaptiveTiltCover
from ..engine.covers.tilt import clamp_to_percentage_scale, hinge_is_usable
from ..const import TiltMode
from ..unit_system import slat_default, slat_selector
from ._helpers import slat_geometry_parts
from ._summary_labels import COVER_TYPE_LABELS_EN
from .base import (
    CAP_HAS_SET_TILT_POSITION,
    TILT_AXIS_PRIMARY,
    TILT_CAPABLE_ENTITY_FILTER,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.covers import AdaptiveGeneralCover
    from ..pipeline.types import PipelineResult
    from ..services.configuration_service import ConfigurationService


# Keys whose stored value is canonical centimetres — used by config-flow steps
# to convert between stored canonical and display-unit on form load/submit.
TILT_SLAT_KEYS: tuple[str, ...] = (CONF_TILT_DEPTH, CONF_TILT_DISTANCE)


# Default slat dimensions (canonical centimetres).
_DEFAULT_TILT_DEPTH_CM = 3.0
_DEFAULT_TILT_DISTANCE_CM = 2.0


def _as_float(value: Any, default: float) -> float | None:
    """Coerce to float, substituting *default* for ``None``; ``None`` if unusable."""
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def tilt_horizontal_percent_error(config: dict[str, Any]) -> str | None:
    """Reject a mid-point the endpoint calibration cannot carry (issue #1222).

    The single user-facing statement of the three-point rule, shared by both
    surfaces that can store it: ``config_flow._tilt_angle_step_errors`` and
    ``services.options_service``'s cross-field pass. The rule ITSELF is
    :func:`engine.covers.tilt.hinge_is_usable` — the very predicate the engine
    gates its hinged map on — so a combination this accepts is exactly a
    combination that takes effect, and nothing can drift between "stored" and
    "honoured".

    Checked unconditionally rather than only on ``specify_angles``, matching how
    the endpoint-ordering rule already behaves: the endpoints and the mid-point
    are stored on every tilt cover and merely lie dormant on the presets, so the
    stored values are validated whenever they are written. The ``0`` sentinel
    short-circuits, which is what keeps this silent for the covers that never
    opt in.

    Missing endpoints fall back to their defaults (the full 0–180° raw range),
    because that is what the engine reads for them. A non-numeric value is not
    this check's business — the range validator and the selector own type.
    """
    raw_percent = config.get(CONF_TILT_HORIZONTAL_PERCENT)
    percent = _as_float(raw_percent, DEFAULT_TILT_HORIZONTAL_PERCENT)
    if percent is None or percent == 0:
        return None
    angle_0 = _as_float(config.get(CONF_TILT_ANGLE_0), DEFAULT_TILT_ANGLE_0)
    angle_100 = _as_float(config.get(CONF_TILT_ANGLE_100), DEFAULT_TILT_ANGLE_100)
    if angle_0 is None or angle_100 is None:
        return None
    if hinge_is_usable(angle_0, angle_100, percent):
        return None
    return (
        f"tilt_horizontal_percent ({raw_percent}) must be between 1 and 99, and "
        f"tilt_angle_0 ({angle_0:g}) / tilt_angle_100 ({angle_100:g}) must "
        f"straddle {TILT_HORIZONTAL_DEG}° so the slats pass through horizontal. "
        "Use 0 to disable the third calibration point."
    )


def tilt_limits_schema() -> dict:
    """Shared tilt-axis limit/shape controls (issue #964, unit-independent).

    Cover-type-agnostic controls that clamp and shape the sun-derived slat tilt:
    the ``[min_tilt, max_tilt]`` band, the two ``*_sun_only`` enforcement flags,
    the tilt safety margin, and the clamp/proportional output transform. Every
    tilt-axis cover reaches them — ``geometry_tilt_schema`` composes this
    fragment (so tilt-only and, via it, louvered-roof get them), and the
    venetian geometry schema composes ``geometry_tilt_schema`` too. Kept as a
    plain dict so both schemas can spread it inline.
    """
    return {
        vol.Optional(CONF_MIN_TILT, default=DEFAULT_MIN_TILT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(
            CONF_MIN_TILT_SUN_ONLY, default=DEFAULT_MIN_TILT_SUN_ONLY
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MAX_TILT, default=DEFAULT_MAX_TILT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(
            CONF_MAX_TILT_SUN_ONLY, default=DEFAULT_MAX_TILT_SUN_ONLY
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_TILT_SAFETY_MARGIN, default=DEFAULT_TILT_SAFETY_MARGIN
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_TILT_SAFETY_MARGIN,
                max=MAX_TILT_SAFETY_MARGIN,
                step=0.05,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(
            CONF_VENETIAN_TILT_TRANSFORM, default=DEFAULT_VENETIAN_TILT_TRANSFORM
        ): vol.In(VENETIAN_TILT_TRANSFORMS),
    }


def geometry_tilt_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Tilt-only geometry schema. ``hass=None`` → metric labels."""
    depth_lo, depth_hi = OPTION_RANGES[CONF_TILT_DEPTH]
    distance_lo, distance_hi = OPTION_RANGES[CONF_TILT_DISTANCE]
    horizontal_lo, horizontal_hi = OPTION_RANGES[CONF_TILT_HORIZONTAL_PERCENT]
    return vol.Schema(
        {
            vol.Required(
                CONF_TILT_DEPTH, default=slat_default(_DEFAULT_TILT_DEPTH_CM, hass)
            ): slat_selector(hass, min_cm=depth_lo, max_cm=depth_hi),
            vol.Required(
                CONF_TILT_DISTANCE,
                default=slat_default(_DEFAULT_TILT_DISTANCE_CM, hass),
            ): slat_selector(hass, min_cm=distance_lo, max_cm=distance_hi),
            vol.Required(CONF_TILT_MODE, default="mode2"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["mode1", "mode2", "specify_angles"],
                    translation_key="tilt_mode",
                )
            ),
            vol.Required(
                CONF_TILT_ANGLE_0, default=DEFAULT_TILT_ANGLE_0
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180, max=180, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_TILT_ANGLE_100, default=DEFAULT_TILT_ANGLE_100
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=360, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            # Optional third calibration point (#1222). BOX is safe here despite
            # the optional-numeric guideline's SLIDER rule: that rule exists
            # because BOX cannot express "cleared" and saves 0 instead — and 0
            # is precisely this field's disabled state, so the failure mode the
            # rule guards against is the intended one.
            vol.Required(
                CONF_TILT_HORIZONTAL_PERCENT,
                default=DEFAULT_TILT_HORIZONTAL_PERCENT,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=horizontal_lo,
                    max=horizontal_hi,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            **tilt_limits_schema(),
        }
    )


# Module-level constant for backward compatibility with tests / re-exports.
# Built without hass (== metric labels), identical to the historical schema.
GEOMETRY_TILT_SCHEMA = geometry_tilt_schema()


class TiltPolicy(CoverTypePolicy, register=True):
    """Cover that rotates slats only (no vertical movement)."""

    cover_type = "cover_tilt"
    # Tilt is this type's only axis, so it carries primary-axis config
    # semantics (``inverse_state`` + interpolation) — see ``TILT_AXIS_PRIMARY``.
    axes: ClassVar[tuple[CoverAxis, ...]] = (TILT_AXIS_PRIMARY,)

    def wiki_anchor(self) -> str:
        """Slat-tilt geometry page."""
        return "Configuration-Tilt"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for tilt-only covers."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.tilt"]

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Reject vertical-blind and awning geometry fields on a tilt-only cover."""
        return [(vertical_only, "vertical blind"), (awning_only, "awning")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the slat-only geometry schema for the given locale.

        Returns the cached module-level constant when no locale is supplied so
        identity-checking tests keep passing; builds a fresh schema otherwise.
        """
        if hass is None:
            return GEOMETRY_TILT_SCHEMA
        return geometry_tilt_schema(hass)

    def geometry_slat_keys(self) -> tuple[str, ...]:
        """Tilt covers store slat depth and spacing in canonical centimetres."""
        return TILT_SLAT_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position``."""
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render the slat-depth / spacing / mode block."""
        parts = slat_geometry_parts(config, labels)
        return [", ".join(parts)] if parts else []

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Warn when no bound entity advertises ``set_tilt_position``."""
        if not any(
            caps_get(caps, CAP_HAS_SET_TILT_POSITION) for caps in known.values()
        ):
            return [
                "⚠️ Configured as tilt (venetian) but no bound cover "
                "advertises set_tilt_position."
            ]
        return []

    @staticmethod
    def is_mode2(mode: TiltMode | str | None) -> bool:
        """Return True when *mode* is MODE2 (bi-directional 0–180°)."""
        return mode == TiltMode.MODE2 or mode == TiltMode.MODE2.value

    @staticmethod
    def climate_tilt_percentage(
        *,
        angle_deg: float,
        mode: TiltMode | str,
        sun_through: bool = False,
        cover: AdaptiveTiltCover | None = None,
    ) -> int:
        """Convert a target slat angle to a tilt percentage that blocks the sun.

        Single source of truth for the climate handler's angle → percent
        translation.

        Pass *cover* and the answer comes from the engine's own angle→percentage
        map — the very map the solar path uses — which is the only way the two
        paths can agree about a calibrated scale. The mode-based arithmetic
        below is the fallback for callers with no engine in scope (and for a
        degenerate scale the engine cannot map), and it is a fallback with a
        known hole: it tests only ``is_mode2`` and otherwise divides by MODE1's
        90°, so it answers a ``specify_angles`` cover as though its calibration
        did not exist (issue #1222). That hole is why the engine seam exists;
        the arithmetic stays because a partial answer beats none where there is
        no engine, and because MODE1/MODE2 — everything it can actually see —
        it gets right.

        Takes no sun-azimuth argument on purpose. Slat tilt is even in gamma —
        the sun's left/right offset enters the slat geometry only through the
        profile angle, via ``cos(gamma)`` — so an answer that varied with the
        *sign* of gamma could only be wrong on one side. It was (issue #1088).

        Args:
            angle_deg: Target slat angle in degrees (e.g. CLIMATE_SUMMER_TILT_ANGLE).
            mode: Tilt mode — TiltMode enum value or its string ("mode1"/"mode2").
            sun_through: When True, return the OPEN hemisphere instead of closed
                (winter heating: let sun reach the window).  Mirrors the
                ``sun_through`` flag on ``position_for_intent``.
            cover: The tilt engine, when the caller has one. Its scale — mode,
                calibrated endpoints, an optional three-point mid-point, a
                louvered roof's ``max_slat_angle`` — is the authority.

        Returns:
            Tilt percentage (0–100) for the cover entity. Every path pins the
            answer onto that scale via ``clamp_to_percentage_scale`` — the
            engine's map is deliberately unclamped so an off-travel pivot keeps
            ordering correctly, which means the last step before a COMMAND is
            where the range promise has to be kept (#1222 audit).

        """
        if cover is not None:
            engine_answer = cover.climate_tilt_percentage(
                angle_deg, sun_through=sun_through
            )
            if engine_answer is not None:
                return engine_answer

        # Normalise mode (accept enum or string for backward compatibility with
        # call sites that historically compared against both forms).
        if not TiltPolicy.is_mode2(mode):
            # MODE1: 0° → 0%, 90° → 100%.
            return round(
                clamp_to_percentage_scale(
                    (angle_deg / TiltMode.MODE1.max_degrees) * 100
                )
            )

        # MODE2: bi-directional 0–180° scale where 50% is horizontal/open.
        # Intent alone picks the hemisphere — NOT the sun's left/right side.
        # Slats rotate about a horizontal axis parallel to the facade, so the
        # sun's azimuth offset reaches the geometry only through the profile
        # angle beta = arctan(tan(elev)/cos(gamma)), and cos is even. A branch on
        # sign(gamma) is therefore unphysical; it made the answer discontinuous
        # at gamma = 0 and, on the gamma >= 0 side, selected the hemisphere that
        # lets direct sun through (issue #1088).
        max_degrees = TiltMode.MODE2.max_degrees
        if sun_through:
            # Winter heating: mirror the angle across horizontal, which lands the
            # slat parallel to the beam — the exact maximum-transmission angle.
            effective_angle = TILT_HORIZONTAL_DEG + angle_deg
        else:
            # Blocking: the closed hemisphere containing the profile angle.
            effective_angle = angle_deg
        return round(clamp_to_percentage_scale((effective_angle / max_degrees) * 100))

    def targets_full_mechanical_endpoint(
        self,
        result: PipelineResult,  # noqa: ARG002
    ) -> bool:
        """Tilt-only covers have no position axis, so never route open/close.

        ``route_service_call`` only substitutes close_cover/open_cover on the
        position axis; a slat-only cover has none, so it can never target a
        full *mechanical* endpoint in the sense the manager forces (issue #897).
        """
        return False

    def build_calc_engine(
        self,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
    ) -> AdaptiveGeneralCover:
        """Build an ``AdaptiveTiltCover`` for slat-only covers."""
        return AdaptiveTiltCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            tilt_config=config_service.get_tilt_data(options),
        )
