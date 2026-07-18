"""Dual-axis venetian-blind cover policy.

Venetian covers drive both ``set_cover_position`` and ``set_cover_tilt_position``
on a single HA entity. Position is resolved by the same pipeline handlers as
``cover_blind`` (using a vertical calculation engine); tilt is filled
post-pipeline by ``VenetianCoverCalculation`` and threaded through the
position-context so ``CoverCommandService`` can run the dual-axis sequence.

The sibling ``sequencer.py`` owns the per-entity dual-axis state and the
position-settle / tilt-verify polling. This module owns the policy decisions
(when to send a pre-position tilt, what tilt to compute, how to thread it
into the pipeline result).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.const import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.helpers import selector

from ...const import (
    CONF_INVERSE_TILT,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_MIN_TILT,
    CONF_MIN_TILT_SUN_ONLY,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    CONF_VENETIAN_MODE,
    CONF_VENETIAN_POST_SETTLE_HOLD,
    CONF_VENETIAN_POST_SETTLE_MODE,
    CONF_VENETIAN_TILT_RESET_DIRECTION,
    CONF_VENETIAN_TILT_RESET_SCOPE,
    CONF_VENETIAN_TILT_RESET_THRESHOLD,
    CONF_VENETIAN_TILT_SAFETY_MARGIN,
    CONF_VENETIAN_TILT_SKIP_ABOVE,
    CONF_VENETIAN_TILT_SKIP_MODE,
    ControlMethod,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MAX_TILT,
    DEFAULT_MAX_TILT_SUN_ONLY,
    DEFAULT_MIN_TILT,
    DEFAULT_MIN_TILT_SUN_ONLY,
    DEFAULT_MINIMIZE_MOVEMENTS,
    DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    DEFAULT_VENETIAN_MODE,
    DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
    DEFAULT_VENETIAN_POST_SETTLE_MODE,
    DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
    DEFAULT_VENETIAN_TILT_RESET_SCOPE,
    DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
    DEFAULT_VENETIAN_TILT_SAFETY_MARGIN,
    DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
    DEFAULT_VENETIAN_TILT_SKIP_MODE,
    MAX_VENETIAN_BACKROTATE_PUBLISH_LAG,
    MAX_VENETIAN_TILT_RESET_THRESHOLD,
    MAX_VENETIAN_TILT_SAFETY_MARGIN,
    MAX_VENETIAN_TILT_SKIP_ABOVE,
    MIN_VENETIAN_BACKROTATE_PUBLISH_LAG,
    MIN_VENETIAN_TILT_RESET_THRESHOLD,
    MIN_VENETIAN_TILT_SAFETY_MARGIN,
    MIN_VENETIAN_TILT_SKIP_ABOVE,
    POSITION_CLOSED,
    POSITION_OPEN,
    TRACE_KEY_TILT,
    VENETIAN_MODE_POSITION_AND_TILT,
    VENETIAN_MODE_TILT_ONLY,
    VENETIAN_MODES,
    VENETIAN_POST_SETTLE_MODES,
    VENETIAN_TILT_RESET_DIRECTIONS,
    VENETIAN_TILT_RESET_SCOPE_ALL,
    VENETIAN_TILT_RESET_SCOPE_SOLAR,
    VENETIAN_TILT_RESET_SCOPES,
    VENETIAN_TILT_SKIP_MODES,
    VENETIAN_TILT_SKIP_SUPPRESS,
)
from ...engine.covers import AdaptiveVerticalCover, VenetianCoverCalculation
from ...managers.manual_override import SecondaryAxisCheck
from ...pipeline.axis_constraints import clamp_to_bounds, tilt_clamp_step
from ...pipeline.types import DecisionStep
from ...position_utils import PositionConverter
from .._helpers import window_dimensions_lines
from .._summary_labels import COVER_TYPE_LABELS_EN, GEOMETRY_LABELS_EN
from ..base import (
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    POSITION_AXIS,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
    caps_get,
)
from ..blind import geometry_vertical_schema
from ..tilt import TILT_CAPABLE_ENTITY_FILTER, geometry_tilt_schema
from .sequencer import DualAxisSequencer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ...engine.covers import AdaptiveGeneralCover
    from ...pipeline.types import PipelineResult
    from ...services.configuration_service import ConfigurationService


# Position-axis services the dual-axis sequencer must treat as a carriage move.
# The endpoint open/close substitution (issue #697) routes a target of 100 to
# ``open_cover`` and 0 to ``close_cover``; both drive the carriage to an
# endpoint exactly like ``set_cover_position`` does, so the tilt-first /
# post-settle tilt sequence must still run for them.
_POSITION_AXIS_SERVICES = frozenset(
    {SERVICE_SET_COVER_POSITION, SERVICE_OPEN_COVER, SERVICE_CLOSE_COVER}
)


# Re-exported for callers that want the unit-independent venetian-only keys.
_VENETIAN_EXTRA_KEYS = (
    CONF_VENETIAN_TILT_SKIP_ABOVE,
    CONF_VENETIAN_TILT_SKIP_MODE,
    CONF_VENETIAN_MODE,
    CONF_VENETIAN_POST_SETTLE_HOLD,
    CONF_VENETIAN_POST_SETTLE_MODE,
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    CONF_INVERSE_TILT,
    CONF_MAX_TILT,
    CONF_MAX_TILT_SUN_ONLY,
    CONF_MIN_TILT,
    CONF_MIN_TILT_SUN_ONLY,
)

# Control methods that carry an explicit, user-specified position.
# The tilt-only carriage-close rewrite does not apply to these — the
# user's position wins over "close carriage, let tilt filter".
_EXPLICIT_USER_POSITION_METHODS = frozenset(
    {
        ControlMethod.CUSTOM_POSITION,
        ControlMethod.FORCE,
        ControlMethod.WEATHER,
        ControlMethod.MANUAL,
        ControlMethod.MOTION,
    }
)


def _venetian_select(
    options: tuple[str, ...], translation_key: str
) -> selector.SelectSelector:
    """Build a translated dropdown for one of the venetian enum fields.

    Mirrors the working pattern in ``config_dynamic.py``
    (``blind_spot_elevation_mode``) / ``config_fields._select()`` — a bare
    ``vol.In(...)`` renders raw enum values in the frontend with no
    translation attempted at all.
    """
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            mode=selector.SelectSelectorMode.LIST,
            translation_key=translation_key,
        )
    )


def _venetian_extras_schema() -> dict:
    """Return the venetian-only schema dict (unit-independent fields)."""
    return {
        vol.Optional(
            CONF_VENETIAN_TILT_SKIP_ABOVE, default=DEFAULT_VENETIAN_TILT_SKIP_ABOVE
        ): vol.All(
            vol.Coerce(int),
            vol.Range(
                min=MIN_VENETIAN_TILT_SKIP_ABOVE, max=MAX_VENETIAN_TILT_SKIP_ABOVE
            ),
        ),
        vol.Optional(
            CONF_VENETIAN_TILT_SKIP_MODE, default=DEFAULT_VENETIAN_TILT_SKIP_MODE
        ): _venetian_select(VENETIAN_TILT_SKIP_MODES, "venetian_tilt_skip_mode"),
        vol.Optional(
            CONF_VENETIAN_TILT_RESET_THRESHOLD,
            default=DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
        ): vol.All(
            vol.Coerce(int),
            vol.Range(
                min=MIN_VENETIAN_TILT_RESET_THRESHOLD,
                max=MAX_VENETIAN_TILT_RESET_THRESHOLD,
            ),
        ),
        vol.Optional(
            CONF_VENETIAN_TILT_RESET_DIRECTION,
            default=DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
        ): _venetian_select(
            VENETIAN_TILT_RESET_DIRECTIONS, "venetian_tilt_reset_direction"
        ),
        vol.Optional(
            CONF_VENETIAN_TILT_RESET_SCOPE,
            default=DEFAULT_VENETIAN_TILT_RESET_SCOPE,
        ): _venetian_select(VENETIAN_TILT_RESET_SCOPES, "venetian_tilt_reset_scope"),
        vol.Optional(
            CONF_VENETIAN_MODE, default=DEFAULT_VENETIAN_MODE
        ): _venetian_select(VENETIAN_MODES, "venetian_mode"),
        vol.Optional(
            CONF_VENETIAN_POST_SETTLE_HOLD,
            default=DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=10.0)),
        vol.Optional(
            CONF_VENETIAN_POST_SETTLE_MODE, default=DEFAULT_VENETIAN_POST_SETTLE_MODE
        ): _venetian_select(VENETIAN_POST_SETTLE_MODES, "venetian_post_settle_mode"),
        vol.Optional(
            CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
            default=DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
        ): vol.All(
            vol.Coerce(float),
            vol.Range(
                min=MIN_VENETIAN_BACKROTATE_PUBLISH_LAG,
                max=MAX_VENETIAN_BACKROTATE_PUBLISH_LAG,
            ),
        ),
        vol.Optional(CONF_INVERSE_TILT, default=False): bool,
        vol.Optional(CONF_MAX_TILT, default=DEFAULT_MAX_TILT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(
            CONF_MAX_TILT_SUN_ONLY, default=DEFAULT_MAX_TILT_SUN_ONLY
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MIN_TILT, default=DEFAULT_MIN_TILT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(
            CONF_MIN_TILT_SUN_ONLY, default=DEFAULT_MIN_TILT_SUN_ONLY
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_VENETIAN_TILT_SAFETY_MARGIN,
            default=DEFAULT_VENETIAN_TILT_SAFETY_MARGIN,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_VENETIAN_TILT_SAFETY_MARGIN,
                max=MAX_VENETIAN_TILT_SAFETY_MARGIN,
                step=0.05,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
    }


def geometry_venetian_schema(hass: HomeAssistant | None = None) -> vol.Schema:
    """Dual-axis venetian geometry schema. ``hass=None`` → metric labels."""
    return geometry_vertical_schema(hass).extend(
        {
            **geometry_tilt_schema(hass).schema,
            **_venetian_extras_schema(),
        }
    )


# Module-level constant for backward compatibility with tests / re-exports.
GEOMETRY_VENETIAN_SCHEMA = geometry_venetian_schema()


class VenetianPolicy(CoverTypePolicy, register=True):
    """Dual-axis cover (single HA entity, position + tilt)."""

    cover_type = "cover_venetian"
    # Position drives the carriage; tilt drives the slats. Order matters —
    # ``select_default_axis`` returns the first entry by default, so a venetian
    # entity with full capabilities routes ``set_cover_position`` calls through
    # the position axis. The tilt axis is filled in by ``post_pipeline_resolve``
    # and dispatched separately by the ``DualAxisSequencer``.
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS, TILT_AXIS)
    exposes_dual_axis_sensor: ClassVar[bool] = True
    custom_position_includes_tilt: ClassVar[bool] = True
    # Venetians carry the same window geometry (width + reveal depth) and fov
    # sliders as vertical blinds, so they get the "Generate FOV from
    # measurements" button too (#565). The toggle is inserted by the shared
    # ``fov_compute_schema`` on the base policy.
    supports_fov_compute: ClassVar[bool] = True

    def extra_field_keys(self, section: str) -> tuple[str, ...]:
        """Venetians add per-slot + global tilt fields to custom position."""
        from ... import config_fields as cf

        if section == cf.SECTION_CUSTOM_POSITION:
            return cf.CUSTOM_POSITION_TILT_KEYS
        return ()

    def wiki_anchor(self) -> str:
        """Dual-axis venetian wiki page."""
        return "Venetian-Blinds"

    def display_label(self, labels: dict[str, str] | None = None) -> str:
        """User-facing label for dual-axis venetians."""
        L = {**COVER_TYPE_LABELS_EN, **(labels or {})}
        return L["cover_types.venetian"]

    def __init__(self) -> None:
        """Initialise without a sequencer; ``attach()`` wires one up later."""
        self._sequencer: DualAxisSequencer | None = None
        self._grace_mgr = None
        self._tilt_skip_above: int = DEFAULT_VENETIAN_TILT_SKIP_ABOVE
        self._tilt_skip_mode: str = DEFAULT_VENETIAN_TILT_SKIP_MODE
        self._venetian_mode: str = DEFAULT_VENETIAN_MODE
        self._last_tilt: int | None = None
        # Drift-reset scope gate (issue #808); replaced by the live lambda in
        # attach(). Defaults to the back-compat "count every tilt send" scope.
        self._get_tilt_reset_scope = lambda: DEFAULT_VENETIAN_TILT_RESET_SCOPE
        # Coordinator callback to schedule a single refresh after N seconds,
        # wired in attach(). Used to wake the update cycle at suppression expiry
        # so a deferred tilt-only update fires promptly (issue #756).
        self._schedule_refresh_after: Any | None = None

    def disallowed_geometry_fields(
        self,
        *,
        vertical_only: set[str],
        awning_only: set[str],
        tilt_only: set[str],
    ) -> list[tuple[set[str], str]]:
        """Accept both vertical and tilt geometry; reject awning-only fields."""
        return [(awning_only, "awning")]

    def geometry_schema(
        self,
        hass: HomeAssistant | None = None,
        options: dict | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """Return the dual-axis geometry schema for the given locale.

        Returns the cached module-level constant when no locale is supplied so
        identity-checking tests keep passing; builds a fresh schema otherwise.
        """
        if hass is None:
            return GEOMETRY_VENETIAN_SCHEMA
        return geometry_venetian_schema(hass)

    def geometry_length_keys(self) -> tuple[str, ...]:
        """Venetians carry the vertical-blind window dimensions in metres."""
        from ..blind import VERTICAL_LENGTH_KEYS

        return VERTICAL_LENGTH_KEYS

    def geometry_slat_keys(self) -> tuple[str, ...]:
        """Venetians also carry slat depth and spacing in centimetres."""
        from ..tilt import TILT_SLAT_KEYS

        return TILT_SLAT_KEYS

    def entity_selector_filter(self) -> selector.EntityFilterSelectorConfig:
        """Require entities that advertise ``set_tilt_position``.

        HA's ``supported_features`` filter is OR-of-listed-features, so we
        filter on the rarer capability and surface the missing-set_position
        case via ``cover_capability_warnings``.
        """
        return TILT_CAPABLE_ENTITY_FILTER

    def summary_geometry_lines(
        self, config: dict[str, Any], labels: dict[str, str] | None = None
    ) -> list[str]:
        """Render window dimensions plus the slat-config block."""
        from ...const import (
            CONF_TILT_ANGLE_0,
            CONF_TILT_ANGLE_100,
            CONF_TILT_DEPTH,
            CONF_TILT_DISTANCE,
            CONF_TILT_MODE,
            TiltMode,
        )

        L = {**GEOMETRY_LABELS_EN, **(labels or {})}
        tilt_parts: list[str] = []
        if (v := config.get(CONF_TILT_DEPTH)) is not None:
            tilt_parts.append(L["geometry.slat.depth"].format(v=v))
        if (v := config.get(CONF_TILT_DISTANCE)) is not None:
            tilt_parts.append(L["geometry.slat.spacing"].format(v=v))
        if (v := config.get(CONF_TILT_MODE)) is not None:
            tilt_parts.append(L["geometry.slat.mode"].format(v=v))
        if config.get(CONF_TILT_MODE) == TiltMode.SPECIFY_ANGLES.value:
            if (v := config.get(CONF_TILT_ANGLE_0)) is not None:
                tilt_parts.append(L["geometry.slat.angle_0"].format(v=v))
            if (v := config.get(CONF_TILT_ANGLE_100)) is not None:
                tilt_parts.append(L["geometry.slat.angle_100"].format(v=v))
        slat_line = [", ".join(tilt_parts)] if tilt_parts else []
        skip_above = config.get(
            CONF_VENETIAN_TILT_SKIP_ABOVE, DEFAULT_VENETIAN_TILT_SKIP_ABOVE
        )
        retract_line = [L["geometry.venetian.skip_tilt"].format(skip_above=skip_above)]
        # Suppress mode (issue #748) gets an extra line — rendered only when
        # opted in, like the drift-reset line.
        skip_mode = config.get(
            CONF_VENETIAN_TILT_SKIP_MODE, DEFAULT_VENETIAN_TILT_SKIP_MODE
        )
        skip_suppress_line = (
            [L["geometry.venetian.skip_tilt_suppress"].format(skip_above=skip_above)]
            if skip_mode == VENETIAN_TILT_SKIP_SUPPRESS
            else []
        )
        venetian_mode = config.get(CONF_VENETIAN_MODE, DEFAULT_VENETIAN_MODE)
        _mode_label = {
            VENETIAN_MODE_POSITION_AND_TILT: L[
                "geometry.venetian.mode_position_and_tilt"
            ],
            VENETIAN_MODE_TILT_ONLY: L["geometry.venetian.mode_tilt_only"],
        }.get(venetian_mode, venetian_mode)
        mode_line = [L["geometry.slat.mode"].format(v=_mode_label)]
        inverse_tilt_line = (
            [L["geometry.venetian.inverse_tilt"]]
            if config.get(CONF_INVERSE_TILT)
            else []
        )
        max_tilt = config.get(CONF_MAX_TILT, DEFAULT_MAX_TILT)
        max_tilt_line = [L["geometry.venetian.max_tilt"].format(max_tilt=max_tilt)]
        min_tilt = config.get(CONF_MIN_TILT, DEFAULT_MIN_TILT)
        min_tilt_line = [L["geometry.venetian.min_tilt"].format(min_tilt=min_tilt)]
        hold = config.get(
            CONF_VENETIAN_POST_SETTLE_HOLD, DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS
        )
        post_settle_line = [
            L["geometry.venetian.post_settle_hold"].format(hold=round(hold, 1))
        ]
        lag = config.get(
            CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
            DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
        )
        backrotate_line = [
            L["geometry.venetian.backrotate_lag"].format(lag=round(lag, 1))
        ]
        # Drift-reset is opt-in: render the line only when a non-zero threshold
        # is configured (0 disables the feature entirely).
        reset_threshold = config.get(
            CONF_VENETIAN_TILT_RESET_THRESHOLD,
            DEFAULT_VENETIAN_TILT_RESET_THRESHOLD,
        )
        reset_direction = config.get(
            CONF_VENETIAN_TILT_RESET_DIRECTION,
            DEFAULT_VENETIAN_TILT_RESET_DIRECTION,
        )
        # Scope suffix appears only when narrowed to sun-tracking (#808); the
        # default all_tilt_commands keeps the original single-line phrasing.
        reset_scope = config.get(
            CONF_VENETIAN_TILT_RESET_SCOPE,
            DEFAULT_VENETIAN_TILT_RESET_SCOPE,
        )
        if reset_threshold:
            drift_reset_text = L["geometry.venetian.drift_reset"].format(
                threshold=reset_threshold, direction=reset_direction
            )
            if reset_scope == VENETIAN_TILT_RESET_SCOPE_SOLAR:
                drift_reset_text += (
                    " — " + L["geometry.venetian.drift_reset_scope_solar"]
                )
            drift_reset_line = [drift_reset_text]
        else:
            drift_reset_line = []
        # Tilt safety margin is opt-in (issue #783): render only when non-zero,
        # matching the drift-reset line's zero-disables convention.
        safety_margin = config.get(
            CONF_VENETIAN_TILT_SAFETY_MARGIN, DEFAULT_VENETIAN_TILT_SAFETY_MARGIN
        )
        safety_margin_line = (
            [
                L["geometry.venetian.tilt_safety_margin"].format(
                    pct=round(safety_margin * 100)
                )
            ]
            if safety_margin
            else []
        )
        return (
            window_dimensions_lines(config, labels)
            + slat_line
            + retract_line
            + skip_suppress_line
            + mode_line
            + inverse_tilt_line
            + min_tilt_line
            + max_tilt_line
            + safety_margin_line
            + post_settle_line
            + backrotate_line
            + drift_reset_line
        )

    def cover_capability_warnings(self, known: dict[str, dict]) -> list[str]:
        """Require both ``set_position`` and ``set_tilt_position`` on every entity."""
        warnings: list[str] = []
        missing_pos = [
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_POSITION)
        ]
        missing_tilt = [
            eid
            for eid, caps in known.items()
            if not caps_get(caps, CAP_HAS_SET_TILT_POSITION)
        ]
        if missing_pos:
            warnings.append(
                "⚠️ Configured as venetian but "
                f"{', '.join(missing_pos)} does not support set_position — "
                "venetian requires both set_position and set_tilt_position."
            )
        if missing_tilt:
            warnings.append(
                "⚠️ Configured as venetian but "
                f"{', '.join(missing_tilt)} does not support "
                "set_tilt_position — venetian requires both set_position "
                "and set_tilt_position."
            )
        return warnings

    def lift_travel_metres(
        self,
        config_service: ConfigurationService,
        options: dict,
    ) -> float | None:
        """Venetian lift axis travels the configured window height."""
        return config_service.get_vertical_data(options).h_win

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
        """Build a vertical calc engine; tilt is filled in ``post_pipeline_resolve``."""
        return AdaptiveVerticalCover(
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            vert_config=config_service.get_vertical_data(options),
        )

    def _engine_tilt_suppressed(self, result: PipelineResult, cover) -> bool:
        """Return True when the solar engine should NOT compute a tilt.

        Applies only to the engine-fallback path; handler-supplied tilts
        (``result.tilt is not None`` on entry to ``post_pipeline_resolve``)
        bypass this gate entirely.

        Engine tilt is meaningful only when (a) the pipeline emitted
        ControlMethod.SOLAR AND (b) the cover engine confirms direct sun is
        hitting the window. The climate handler can emit SOLAR on its low-light
        branch even when the sun is below the horizon (issue #33), so
        direct_sun_valid is the authoritative signal.
        """
        if result.control_method != ControlMethod.SOLAR:
            return True
        return cover is None or not cover.direct_sun_valid

    def _compose_tilt(
        self,
        position: int,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        minimize_movements: bool,
        max_coverage_steps: int,
    ) -> tuple[int, VenetianCoverCalculation]:
        """Single source of truth for the engine slat angle + minimize quantize.

        Builds the tilt engine, computes the slat angle for ``position``, and
        applies the movement-minimization quantize (the tilt axis closes at 0%,
        so full coverage is at zero). Returns ``(tilt, venetian_calc)`` so the
        live ``post_pipeline_resolve`` can still merge the tilt engine's raw
        trace (#682) and the forecast hook can take just the scalar. BOTH the
        live path and the projected forecast flow through this one method, so
        the tilt geometry never diverges between them (CODING_GUIDELINES.md
        "No Code Duplication").
        """
        venetian_calc = VenetianCoverCalculation(
            config=config,
            vert_config=config_service.get_vertical_data(options),
            tilt_config=config_service.get_tilt_data(options),
            sun_data=sun_data,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            logger=logger,
        )
        tilt = venetian_calc.tilt_for_position(position)
        if minimize_movements:
            tilt = PositionConverter.quantize_to_coverage_steps(
                tilt,
                max_coverage_steps,
                full_coverage_at_zero=not self.axes[1].open_blocks_sun,
            )
        return tilt, venetian_calc

    def forecast_secondary_axes(
        self,
        *,
        position: int,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        minimize_movements: bool,
        max_coverage_steps: int,
    ) -> dict[str, int]:
        """Project the slat tilt that pairs with ``position`` (#724).

        Delegates to the shared :meth:`_compose_tilt` seam so the forecast
        strip's tilt track matches the live cover exactly. Returns the tilt
        keyed by the tilt axis's ``name`` (``self.axes[1].name`` — no hardcoded
        ``"tilt"`` literal), which the forecast serializer spreads additively.
        """
        tilt, _ = self._compose_tilt(
            position,
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            config_service=config_service,
            options=options,
            minimize_movements=minimize_movements,
            max_coverage_steps=max_coverage_steps,
        )
        return {self.axes[1].name: tilt}

    def post_pipeline_resolve(
        self,
        result: PipelineResult,
        *,
        logger,
        sol_azi: float,
        sol_elev: float,
        sun_data,
        config,
        config_service: ConfigurationService,
        options: dict,
        cover: AdaptiveGeneralCover | None = None,
    ) -> PipelineResult:
        """Fill the tilt that pairs with the pipeline-resolved position.

        The pipeline picks position using the same vertical math as
        ``cover_blind``; this hook composes the matching slat angle from
        ``VenetianCoverCalculation`` and appends a synthetic terminal
        ``"venetian_engine"`` decision step so diagnostics show exactly how
        tilt was derived.
        """
        if result is None:
            return result

        # Handler-supplied tilt is explicit user intent — honor it unconditionally.
        if result.tilt is not None:
            handler_tilt = result.tilt
            position = result.position
            trace = list(result.decision_trace)
            if (
                self._venetian_mode == VENETIAN_MODE_TILT_ONLY
                and result.control_method not in _EXPLICIT_USER_POSITION_METHODS
                and not result.tilt_only_contribution_active
            ):
                trace.append(
                    DecisionStep(
                        handler="venetian_mode",
                        matched=True,
                        reason=(
                            f"tilt-only mode: position {position}% → {POSITION_CLOSED}% "
                            "(closed); tilt drives the slats"
                        ),
                        position=POSITION_CLOSED,
                        tilt=handler_tilt,
                    )
                )
                position = POSITION_CLOSED
            trace.append(
                DecisionStep(
                    handler="venetian_handler_tilt",
                    matched=True,
                    reason=f"handler-supplied tilt {handler_tilt}% honored",
                    position=position,
                    tilt=handler_tilt,
                )
            )
            self._last_tilt = handler_tilt
            return replace(
                result, position=position, tilt=handler_tilt, decision_trace=trace
            )

        # No handler tilt: engine fallback runs only when SOLAR + direct sun.
        if self._engine_tilt_suppressed(result, cover):
            self._clear_last_tilt()
            return replace(result, tilt=None)

        # Compose the engine slat angle (+ movement-minimization quantize) via
        # the shared seam the forecast hook also uses, so the projected tilt
        # track and the live tilt never diverge. The returned ``venetian_calc``
        # carries the transient tilt trace the live-only #682 merge needs.
        tilt, venetian_calc = self._compose_tilt(
            result.position,
            logger=logger,
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            sun_data=sun_data,
            config=config,
            config_service=config_service,
            options=options,
            minimize_movements=bool(
                options.get(CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS)
            ),
            max_coverage_steps=int(
                options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
            ),
        )
        # Issue #682: merge the (otherwise transient) tilt engine's raw trace into
        # the position engine's _last_calc_details under a `tilt` sub-key so the
        # live solar_calculation sensor and the diagnostics download surface BOTH
        # axes. Guarded: only when the position engine recorded a dict trace and
        # the tilt engine produced one (this branch is the non-suppressed path).
        tilt_trace = getattr(
            venetian_calc._tilt, "_last_calc_details", None
        )  # noqa: SLF001
        cover_trace = getattr(cover, "_last_calc_details", None)
        if isinstance(cover_trace, dict) and tilt_trace is not None:
            cover_trace[TRACE_KEY_TILT] = tilt_trace
        position = result.position
        trace = list(result.decision_trace)

        # Axis constraints (issue #943). The registry could not clamp this tilt
        # — it did not exist yet, the engine just produced it — so it carried
        # the composed bounds on the result instead. Apply them here, through
        # the same shared clamp the registry uses: the arithmetic stays in the
        # one pipeline helper, while the knowledge that a venetian resolves its
        # tilt after the pipeline stays inside the venetian policy.
        bounded = clamp_to_bounds(tilt, result.tilt_low, result.tilt_high)
        if bounded != tilt:
            trace.append(
                tilt_clamp_step(
                    from_tilt=tilt,
                    to_tilt=bounded,
                    label=result.tilt_bound_label or "constraint",
                    source="tilt_clamp",
                )
            )
            tilt = bounded

        if (
            self._venetian_mode == VENETIAN_MODE_TILT_ONLY
            and not result.tilt_only_contribution_active
        ):
            trace.append(
                DecisionStep(
                    handler="venetian_mode",
                    matched=True,
                    reason=(
                        f"tilt-only mode: position {position}% → {POSITION_CLOSED}% "
                        "(closed); tilt drives the slats"
                    ),
                    position=POSITION_CLOSED,
                    tilt=tilt,
                )
            )
            position = POSITION_CLOSED

        trace.append(
            DecisionStep(
                handler="venetian_engine",
                matched=True,
                reason=(f"slat angle for position {position}% — tilt {tilt}%"),
                position=position,
                tilt=tilt,
            )
        )
        self._last_tilt = tilt
        return replace(result, position=position, tilt=tilt, decision_trace=trace)

    def targets_full_mechanical_endpoint(self, result: PipelineResult) -> bool:
        """Report a full mechanical endpoint only when BOTH axes reach it.

        Narrows the base single-axis predicate (issue #897): a venetian is at a
        true 0/0 or 100/100 stop only when carriage and slats target the same
        endpoint. Position 0 with tilt 100 (a legitimate solar state) is NOT a
        mechanical stop. Only the venetian policy knows the paired tilt, so this
        dual-axis decision stays here (issue #755).
        """
        return (
            result is not None
            and result.tilt is not None
            and result.position is not None
            and result.position == result.tilt
            and result.position in (POSITION_CLOSED, POSITION_OPEN)
        )

    def position_context_overrides(self, result: PipelineResult) -> dict[str, Any]:
        """Thread the resolved tilt into ``PositionContext.tilt``.

        When BOTH axes target the same full mechanical endpoint (0/0 or
        100/100) also set the cover-type-agnostic ``full_endpoint_target`` flag
        (via :meth:`targets_full_mechanical_endpoint`) so the command manager
        forces close_cover/open_cover instead of dropping the move as
        same_position (issue #755).
        """
        if result is None or result.tilt is None:
            return {}
        overrides: dict[str, Any] = {"tilt": result.tilt}
        if self.targets_full_mechanical_endpoint(result):
            overrides["full_endpoint_target"] = True
        return overrides

    def attach(self, **kwargs: Any) -> None:  # noqa: D401
        """Construct the dual-axis sequencer once cmd_svc is available."""
        self._grace_mgr = kwargs.get("grace_mgr")
        self._sequencer = DualAxisSequencer(
            hass=kwargs["hass"],
            logger=kwargs["logger"],
            grace_mgr=kwargs["grace_mgr"],
            get_current_position=kwargs["get_current_position"],
            set_commanded_position=kwargs["set_commanded_position"],
            position_tolerance=kwargs["position_tolerance"],
            is_dry_run=kwargs["is_dry_run"],
            get_state=kwargs.get("get_state"),
            get_current_tilt_position=kwargs.get("get_current_tilt_position"),
            event_buffer=kwargs.get("event_buffer"),
            invert_tilt=kwargs.get("invert_tilt"),
            get_min_change=kwargs.get("get_min_change"),
            get_enforce_delta_at_endpoints=kwargs.get("get_enforce_delta_at_endpoints"),
            get_tilt_reset_threshold=kwargs.get("get_tilt_reset_threshold"),
            get_tilt_reset_direction=kwargs.get("get_tilt_reset_direction"),
            post_settle_hold_seconds=kwargs.get(
                "post_settle_hold_seconds", DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS
            ),
            post_settle_mode=kwargs.get(
                "post_settle_mode", DEFAULT_VENETIAN_POST_SETTLE_MODE
            ),
            backrotate_publish_lag_seconds=kwargs.get(
                "backrotate_publish_lag_seconds",
                DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
            ),
        )
        # Drift-reset scope (issue #808) is a policy-level gate: the policy owns
        # the ControlMethod == SOLAR decision (cover-type knowledge stays inside
        # cover_types/) and passes a neutral ``drift_reset_eligible`` bool to the
        # sequencer. Default to ``all_tilt_commands`` (back-compat) when unset.
        self._get_tilt_reset_scope = kwargs.get("get_tilt_reset_scope") or (
            lambda: DEFAULT_VENETIAN_TILT_RESET_SCOPE
        )
        if "tilt_skip_above" in kwargs:
            self._tilt_skip_above = int(kwargs["tilt_skip_above"])
        if "venetian_tilt_skip_mode" in kwargs:
            self._tilt_skip_mode = str(kwargs["venetian_tilt_skip_mode"])
        if "venetian_mode" in kwargs:
            self._venetian_mode = str(kwargs["venetian_mode"])
        # Coordinator wake callback for deferred-tilt flushing (issue #756).
        self._schedule_refresh_after = kwargs.get("schedule_refresh_after")

    @property
    def sequencer(self) -> DualAxisSequencer | None:
        """Expose the sequencer for diagnostics / tests."""
        return self._sequencer

    def is_in_tilt_suppression(self, entity_id: str, delta: float = 0.0) -> bool:
        """Suppress back-rotate drift only when ``delta`` is plausibly motor drift.

        Delegates to the sequencer's delta-aware gate. Large deltas inside the
        window are user moves, not motor drift, and fall through to the
        manual-override numeric path (issue #33 follow-on). The ``delta``
        default matches the base signature so the method is interchangeable
        with other policies when passed as a ``SecondaryAxisCheck.suppression``
        callback.
        """
        if self._sequencer is None:
            return False
        return self._sequencer.is_in_suppression_with_cap(entity_id, delta)

    def primary_axis_suppression(self, entity_id: str, delta: float = 0.0) -> bool:
        """Apply the tilt-axis publish-lag window to the position axis too.

        Issue #33 Phase 5 (cross-axis): the user's 2026-05-26 diagnostic on
        ``cover.wohnzimmerjalousie_links`` shows the position axis hit the
        same defect that PR #408 fixed on the tilt axis — a slow-bus
        actuator publishes a stale ``current_position`` ~60 s after the
        cover has physically stopped, and the position-axis branch of
        ``handle_state_change`` reads it as a 100 % user-initiated touch.

        Both axes share one predicate
        (``DualAxisSequencer.is_in_suppression_with_cap``) plus the
        command-grace tail, so the position-axis path consults exactly the
        same window the tilt-axis ``SecondaryAxisCheck.suppression`` does.
        Per CODING_GUIDELINES.md § "No Code Duplication" the shared
        callback inside :meth:`secondary_axis_check` now delegates here
        instead of inlining its own OR-composition.

        The drift-reset endpoint excursion guard (issue #927) is deliberately
        NOT folded in here: it is a value-based, tilt-axis-only predicate wired
        as ``SecondaryAxisCheck.excursion_match`` on the tilt axis. The position
        axis must not consult it, or a position move whose delta coincidentally
        matched a stale tilt excursion would be suppressed and the excursion's
        one-shot record burned before the real tilt publish arrives.
        """
        if self._sequencer is None:
            return False
        return self._sequencer.is_in_suppression_with_cap(
            entity_id, delta
        ) or self._is_in_tilt_command_grace(entity_id, delta)

    def _clear_last_tilt(self) -> None:
        """Forget the last resolved tilt so tilt-only cycles don't replay it.

        Called on every suppressed branch of ``post_pipeline_resolve``
        (non-SOLAR control method, or SOLAR with no direct sun). Without this
        reset, ``maybe_update_tilt_only`` keeps re-firing the prior solar
        tilt against an actuator that the user thinks should be neutral —
        producing the chronic position/tilt state divergence in issue #33.
        """
        self._last_tilt = None

    def _resolve_skip_above_tilt(
        self, position: int | None, fallback_tilt: int | None
    ) -> int | None:
        """Apply the ``tilt_skip_above`` guard to a tilt decision.

        Above the skip threshold the behaviour depends on
        ``venetian_tilt_skip_mode`` (issue #748):

        * ``neutral`` (default) returns ``POSITION_OPEN`` so the actuator's
          tilt cache is overwritten with a benign value rather than the prior
          solar-cycle tilt (the #33 behaviour KNX/Shelly need).
        * ``suppress`` returns ``None`` so NO tilt command is emitted at the
          open endpoint — mechanically-coupled exterior venetians otherwise
          get dragged off 100 by any tilt send.

        Returns ``fallback_tilt`` when at or below the threshold. Shared by
        ``before_position_command``, ``after_position_command`` and
        ``maybe_update_tilt_only`` so the threshold rule lives in one place.
        """
        if position is not None and position > self._tilt_skip_above:
            if self._tilt_skip_mode == VENETIAN_TILT_SKIP_SUPPRESS:
                return None
            return POSITION_OPEN
        return fallback_tilt

    async def maybe_update_tilt_only(
        self,
        entity_id: str,
        *,
        current_position: int | None,
        context: Any,
        reason: str,
    ) -> None:
        """Send a tilt-only update when the position axis won't fire this cycle.

        Routine tracking cycles that land inside the prior sequence's
        back-rotate suppression window defer the tilt (issue #756). A forced
        handler transition (``context.force`` — e.g. ``custom_position_released``)
        is new handler intent rather than motor back-rotate drift, so it
        bypasses that deferral and sends the full new tilt immediately — but
        only once the carriage has stopped moving, preserving the mid-travel
        suppression guard (issue #770).
        """
        if self._sequencer is None:
            return
        if self._last_tilt is None:
            return
        tilt_target = self._resolve_skip_above_tilt(current_position, self._last_tilt)
        if tilt_target is None:
            # Suppress mode above the skip threshold: emit no tilt command so
            # coupled-axis covers are not nudged off the open endpoint (#748).
            return
        if self._sequencer.is_in_suppression(entity_id):
            if context.force and not self._sequencer.is_carriage_moving(entity_id):
                # Issue #770: a forced handler transition (e.g.
                # custom_position_released) is new handler intent, not motor
                # back-rotate drift. Send the full new tilt immediately rather
                # than deferring it — but only once the carriage has stopped
                # moving, so tier (a) of the suppression cap still holds.
                await self._sequencer.update_tilt_only(
                    entity_id,
                    tilt_target=tilt_target,
                    current_position=current_position,
                    reason=reason,
                    force=True,
                    drift_reset_eligible=self._drift_reset_eligible(context),
                )
                return
            # Issue #756: a tilt-only update that lands inside the prior
            # sequence's back-rotate suppression window cannot send yet. Record
            # the deferred tilt and schedule a single wake at suppression expiry
            # so it fires promptly — instead of being dropped until the next
            # unrelated tracked-entity change. has_pending_secondary_axis keeps
            # the coordinator re-attempting dispatch in the meantime.
            self._sequencer.record_pending_tilt(
                entity_id,
                tilt_target=tilt_target,
                current_position=current_position,
                reason=reason,
            )
            if self._schedule_refresh_after is not None:
                remaining = self._sequencer.suppression_remaining_seconds(entity_id)
                if remaining is not None:
                    self._schedule_refresh_after(remaining)
            return
        await self._sequencer.update_tilt_only(
            entity_id,
            tilt_target=tilt_target,
            current_position=current_position,
            reason=reason,
            drift_reset_eligible=self._drift_reset_eligible(context),
        )

    def has_pending_secondary_axis(self, entity_id: str) -> bool:
        """Return True while a deferred tilt-only update is queued (issue #756)."""
        if self._sequencer is None:
            return False
        return self._sequencer.has_pending_tilt(entity_id)

    async def apply_user_tilt(
        self,
        entity_id: str,
        *,
        tilt: int,
        reason: str,
    ) -> bool:
        """Drive a user-requested tilt on the tilt axis ONLY (issue #684).

        A venetian is dual-axis: the proxy/user must be able to set slat tilt
        without moving the carriage. We read the *current* carriage position
        purely as a reference for the sequencer's pairing/rebase logic — it is
        never commanded — and force the tilt send so a user re-requesting the
        current angle still fires. ``update_tilt_only`` →
        ``_send_tilt_command`` applies ``_to_wire`` internally, so inverse-tilt
        is handled for free.
        """
        if self._sequencer is None:
            return False
        current_position = self._sequencer._get_current_position(entity_id)
        await self._sequencer.update_tilt_only(
            entity_id,
            tilt_target=tilt,
            current_position=current_position,
            reason=reason,
            force=True,
        )
        return True

    def _is_in_tilt_command_grace(self, entity_id: str, delta: float = 0.0) -> bool:
        """Return True when the entity is inside the command-grace window.

        Delegates to the coordinator-supplied ``GracePeriodManager``; returns
        False when no manager is available (non-attached policy, tests that
        construct the policy without attach()).

        The ``delta`` parameter is accepted to match the
        ``Callable[[str, float], bool]`` signature expected by
        ``SecondaryAxisCheck.suppression`` — it is not used here because the
        grace period is time-based, not delta-based.
        """
        if self._grace_mgr is None:
            return False
        return self._grace_mgr.is_in_command_grace_period(entity_id)

    def secondary_axis_check(
        self, result: PipelineResult, cmd_svc
    ) -> SecondaryAxisCheck | None:
        """Build the per-cycle tilt-axis manual-override check.

        Returns ``None`` when no tilt has been resolved (e.g. on a refresh
        where the engine couldn't compute one); otherwise carries the
        expected tilt and the suppression callback into manual_override.

        The suppression callback is the same predicate
        :meth:`primary_axis_suppression` exposes for the position axis
        (issue #33 Phase 5 cross-axis): the motor back-rotate window
        OR'd with the command-grace period. Sharing one callback across
        both axes keeps the publish-lag and grace logic from drifting per
        CODING_GUIDELINES.md § "No Code Duplication".

        ``excursion_match`` wires the drift-reset endpoint guard (issue #927)
        onto the TILT axis only. It is value-based (matches the raw published
        wire tilt against the recorded endpoint) and consulted before the grace
        check; it tracks the full out-and-back trajectory (#930), so a stale
        endpoint publish landing after the command grace closes is recognised as
        ACP's own move — without the position axis ever consulting it.
        """
        if result is None or result.tilt is None:
            return None

        return SecondaryAxisCheck(
            expected=result.tilt,
            attribute="current_tilt_position",
            label="tilt",
            suppression=self.primary_axis_suppression,
            excursion_match=(
                self._sequencer.is_reset_excursion_publish
                if self._sequencer is not None
                else None
            ),
        )

    def _drift_reset_eligible(self, context: Any) -> bool:
        """Whether this tilt send may accumulate drift and trigger a reset (#808).

        ``all_tilt_commands`` (default) always eligible; ``sun_tracking_only``
        restricts eligibility to solar-tracking wins (``ControlMethod.SOLAR``).
        The cover-type-specific ``== SOLAR`` decision lives here so the shared
        managers/sequencer stay cover-type-agnostic: the policy reads the
        neutral ``control_method`` off the context and hands the sequencer a
        plain bool.
        """
        return (
            self._get_tilt_reset_scope() == VENETIAN_TILT_RESET_SCOPE_ALL
            or getattr(context, "control_method", None) == ControlMethod.SOLAR
        )

    async def before_position_command(
        self,
        cmd_svc,  # noqa: ARG002
        entity_id: str,
        *,
        service: str,
        position: int,
        context,
        reason: str,
    ) -> None:
        """Send tilt FIRST on opening transitions, before set_cover_position fires.

        KNX/Shelly venetian actuators briefly reassert their cached tilt
        against partially-closed slats during open travel — a visible "slats
        close then open" flicker. Sending the new tilt before the carriage
        starts moving lets the open absorb any back-rotation into the
        already-targeted angle (issue #33).

        Closing transitions keep the existing position-then-tilt order in
        ``after_position_command`` (slats must close after the carriage has
        finished travelling). The post-settle tilt resend in
        ``run_sequence`` short-circuits on the target-unchanged dedup added
        to ``_send_tilt_command``, so total service-call count for an
        opening transition remains 2 (tilt + position).
        """
        if service not in _POSITION_AXIS_SERVICES:
            return
        seq = self._sequencer
        if seq is None:
            return
        tilt_target = self._resolve_skip_above_tilt(
            position, getattr(context, "tilt", None)
        )
        if tilt_target is None:
            return
        current = seq._get_current_position(entity_id)
        if current is None or position <= current:
            return
        await seq._send_tilt_command(
            entity_id,
            tilt_target=tilt_target,
            position_target=position,
            reason=reason,
            force=True,
            verify=False,
            drift_reset_eligible=self._drift_reset_eligible(context),
        )

    async def after_position_command(
        self,
        cmd_svc,
        entity_id: str,
        *,
        service: str,
        position: int,
        context,
        reason: str,
    ) -> None:
        """Run the dual-axis sequence after a successful ``set_cover_position``.

        When the carriage is commanded above ``tilt_skip_above`` we still
        sequence a tilt — but to ``POSITION_OPEN`` (neutral). KNX and Shelly
        venetian actuators retain their last commanded tilt internally and
        re-apply it ~1-2 s after the carriage settles. Without overwriting
        the cache here, the prior solar-cycle tilt reasserts and closes the
        slats on a fully-retracted blind (issue #33).
        """
        # Only chain a tilt after the position axis fired — direct tilt
        # commands skip the sequence entirely. The endpoint open_cover /
        # close_cover substitution (issue #697) counts as a position-axis
        # move, so it still drives the dual-axis sequence.
        if service not in _POSITION_AXIS_SERVICES:
            return
        seq = self._sequencer
        if seq is None:
            return
        tilt_target = self._resolve_skip_above_tilt(
            position, getattr(context, "tilt", None)
        )
        if tilt_target is None:
            return
        # Open suppression early — covers position-axis settle events that
        # fire before _send_tilt_command runs (which itself stamps again).
        seq.stamp_position_command(entity_id)
        await seq.run_sequence(
            entity_id,
            position_target=position,
            tilt_target=tilt_target,
            reason=reason,
            drift_reset_eligible=self._drift_reset_eligible(context),
        )
