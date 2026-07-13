"""Tests for CloudSuppressionHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import ControlMethod, ReasonCode
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.reason_i18n import render_en
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot


def _make_readings(
    *,
    is_sunny: bool = True,
    lux_below_threshold: bool = False,
    irradiance_below_threshold: bool = False,
    cloud_coverage_above_threshold: bool = False,
) -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
    )


def _make_options(enabled: bool = True) -> ClimateOptions:
    return ClimateOptions(
        temp_low=None,
        temp_high=None,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=enabled,
        winter_close_insulation=False,
    )


class TestCloudSuppressionHandler:
    """Test CloudSuppressionHandler."""

    handler = CloudSuppressionHandler()

    def test_returns_none_when_feature_disabled(self) -> None:
        """Return None when cloud_suppression_enabled is False."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=False),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_readings(self) -> None:
        """Return None when no climate readings are available."""
        snap = make_snapshot(
            climate_readings=None,
            climate_options=_make_options(enabled=True),
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_no_climate_options(self) -> None:
        """Return None when no climate options are configured."""
        snap = make_snapshot(
            climate_readings=_make_readings(),
            climate_options=None,
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_sunny_and_no_thresholds(self) -> None:
        """Return None when sun is present and all thresholds are fine."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=True),
            climate_options=_make_options(enabled=True),
        )
        assert self.handler.evaluate(snap) is None

    def test_activates_when_not_sunny(self) -> None:
        """Activate when weather state is not sunny and sun is within FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 30

    def test_activates_when_lux_below_threshold(self) -> None:
        """Activate when lux is below the configured threshold and sun is within FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_activates_when_irradiance_below_threshold(self) -> None:
        """Activate when solar irradiance is below threshold and sun is within FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(
                is_sunny=True, irradiance_below_threshold=True
            ),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_activates_when_cloud_coverage_above_threshold(self) -> None:
        """Activate when cloud coverage sensor exceeds threshold and sun is within FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(cloud_coverage_above_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_returns_default_position(self) -> None:
        """Return snapshot.default_position when suppressing (sun within FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=55,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 55

    def test_priority_is_60(self) -> None:
        """CloudSuppressionHandler has priority 60."""
        assert CloudSuppressionHandler.priority == 60

    def test_name(self) -> None:
        """CloudSuppressionHandler name is 'cloud_suppression'."""
        assert CloudSuppressionHandler.name == "cloud_suppression"

    def test_sun_only_max_not_applied_to_default_regression_105(self) -> None:
        """Regression #105: sun-only max limit must NOT clamp the default position.

        User scenario: default=50, max_pos=26 (sun-only), sun geometrically in FOV
        but cloudy. Cloud suppression fires and should return 50, not 26.
        """
        cover = MagicMock()
        cover.direct_sun_valid = True  # sun is geometrically in FOV
        cover.valid = True
        cover.calculate_percentage = MagicMock(return_value=15.0)
        cover.logger = MagicMock()
        config = MagicMock()
        config.min_pos = None
        config.max_pos = 26
        config.min_pos_sun_only = False
        config.max_pos_sun_only = True  # "during sun tracking only"
        cover.config = config

        snap = make_snapshot(
            cover=cover,
            climate_readings=_make_readings(cloud_coverage_above_threshold=True),
            climate_options=_make_options(enabled=True),
            default_position=50,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 50, (
            f"Expected default position 50 but got {result.position}. "
            "Sun-only max limit must not clamp cloud suppression output."
        )


# ---------------------------------------------------------------------------
# Issue #145 — CloudSuppressionHandler must respect in_time_window
# ---------------------------------------------------------------------------


class TestCloudHandlerTimeWindow:
    """CloudSuppressionHandler must return None outside the time window.

    Before the fix, CloudSuppressionHandler ignored ``snapshot.in_time_window``.
    """

    handler = CloudSuppressionHandler()

    def test_returns_none_outside_time_window(self) -> None:
        """Cloud suppression must be inactive outside the time window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None, (
            "CloudSuppressionHandler should return None outside the time window "
            f"but returned {result}"
        )

    def test_returns_result_inside_time_window(self) -> None:
        """Cloud suppression should activate when inside the time window and sun in FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=30,
            in_time_window=True,
        )
        result = self.handler.evaluate(snap)
        assert result is not None

    def test_describe_skip_outside_window(self) -> None:
        """describe_skip() should mention 'time window' when outside window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            in_time_window=False,
        )
        reason = render_en(self.handler.describe_skip(snap))
        assert (
            "time window" in reason.lower()
        ), f"Expected 'time window' in describe_skip reason but got: {reason!r}"

    def test_lux_threshold_outside_window_returns_none(self) -> None:
        """Lux-based suppression must also be gated by time window."""
        snap = make_snapshot(
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
            in_time_window=False,
        )
        result = self.handler.evaluate(snap)
        assert result is None


# ---------------------------------------------------------------------------
# Reason string includes specific trigger labels (Issue #222)
# ---------------------------------------------------------------------------


class TestCloudHandlerReasonString:
    """Reason string must identify which condition(s) triggered suppression."""

    handler = CloudSuppressionHandler()

    def test_reason_includes_weather_not_sunny(self) -> None:
        """Reason must mention 'weather not sunny' when is_sunny is False (sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "weather not sunny" in result.reason

    def test_reason_includes_lux_below_threshold(self) -> None:
        """Reason must mention 'lux below threshold' when lux fires (sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "lux below threshold" in result.reason

    def test_reason_includes_irradiance_below_threshold(self) -> None:
        """Reason must mention 'irradiance below threshold' when irradiance fires (sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(
                is_sunny=True, irradiance_below_threshold=True
            ),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "irradiance below threshold" in result.reason

    def test_reason_includes_cloud_coverage_above_threshold(self) -> None:
        """Reason must mention 'cloud coverage above threshold' when cloud fires (sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(
                is_sunny=True, cloud_coverage_above_threshold=True
            ),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "cloud coverage above threshold" in result.reason

    def test_reason_lists_multiple_triggers(self) -> None:
        """When multiple conditions fire, all should appear in the reason string (sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(
                is_sunny=False,
                lux_below_threshold=True,
                cloud_coverage_above_threshold=True,
            ),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "weather not sunny" in result.reason
        assert "lux below threshold" in result.reason
        assert "cloud coverage above threshold" in result.reason

    def test_reason_payload_code_triggers_and_pos_label(self) -> None:
        """The payload carries a tuple of trigger fragments + a pos_label fragment."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(
                is_sunny=False,
                lux_below_threshold=True,
                cloud_coverage_above_threshold=True,
            ),
            climate_options=_make_options(enabled=True),
            default_position=25,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.CLOUD_SUPPRESSION
        assert result.reason_payload.params["position"] == 25
        trigger_codes = [t.code for t in result.reason_payload.params["triggers"]]
        assert trigger_codes == [
            ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY,
            ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW,
            ReasonCode.FRAGMENT_TRIGGER_CLOUD_ABOVE,
        ]
        assert (
            result.reason_payload.params["pos_label"].code
            == ReasonCode.FRAGMENT_DEFAULT_POSITION
        )

    def test_reason_payload_cloudy_position_fragment(self) -> None:
        """A configured cloudy_position uses the cloudy pos_label fragment."""
        options = ClimateOptions(
            temp_low=None,
            temp_high=None,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=True,
            winter_close_insulation=False,
            cloudy_position=15,
        )
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=options,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.reason_payload is not None
        assert (
            result.reason_payload.params["pos_label"].code
            == ReasonCode.FRAGMENT_CLOUDY_POSITION
        )

    def test_reason_payload_smoothing_hold_fragment(self) -> None:
        """A latch-only fire (no raw trigger) emits the smoothing-hold fragment."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=True),
            climate_options=_make_options(enabled=True),
            cloud_suppression_active=True,
            default_position=40,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.reason_payload is not None
        trigger_codes = [t.code for t in result.reason_payload.params["triggers"]]
        assert trigger_codes == [ReasonCode.FRAGMENT_TRIGGER_SMOOTHING_HOLD]

    def test_reason_does_not_say_no_direct_sun_detected(self) -> None:
        """Old generic phrase 'no direct sun detected' must not appear (Issue #222, sun in FOV)."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert "no direct sun detected" not in result.reason


# ---------------------------------------------------------------------------
# Issue #417 — CloudSuppressionHandler must respect direct_sun_valid (FOV gate)
# ---------------------------------------------------------------------------


class TestCloudHandlerFOVGate:
    """CloudSuppressionHandler must return None when sun is outside the window FOV.

    Before the fix, CloudSuppressionHandler ignored ``snapshot.cover.direct_sun_valid``
    and would override normal pipeline behaviour (sending the cover to default/cloudy
    position) even when the sun was geometrically outside the window's field of view.
    In that scenario, the cloud trigger is irrelevant — the solar handler would already
    have passed, so cloud suppression firing causes incorrect behaviour.
    """

    handler = CloudSuppressionHandler()

    def test_returns_none_when_cloud_trigger_active_but_sun_outside_fov(self) -> None:
        """Cloud-based trigger must not fire when sun is outside the window FOV.

        Regression for issue #417: weather 'not sunny' triggered cloud suppression
        even when direct_sun_valid=False (sun geometrically outside the FOV).
        """
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=80,
        )
        assert self.handler.evaluate(snap) is None

    def test_returns_none_when_lux_trigger_active_but_sun_outside_fov(self) -> None:
        """Lux-based trigger must not fire when sun is outside the window FOV.

        Regression for issue #417: lux below threshold triggered cloud suppression
        even when direct_sun_valid=False (sun geometrically outside the FOV).
        """
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_readings=_make_readings(is_sunny=True, lux_below_threshold=True),
            climate_options=_make_options(enabled=True),
        )
        assert self.handler.evaluate(snap) is None

    def test_still_activates_when_cloud_trigger_active_and_sun_in_fov(self) -> None:
        """Cloud suppression fires normally when sun IS within the window FOV."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            default_position=80,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_describe_skip_mentions_acceptance_angle_when_sun_outside(self) -> None:
        """describe_skip() must mention the acceptance angle when sun is outside it."""
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            in_time_window=True,
        )
        reason = render_en(self.handler.describe_skip(snap))
        assert (
            "acceptance angle" in reason.lower()
        ), f"Expected 'acceptance angle' in describe_skip reason but got: {reason!r}"

    def test_describe_skip_payloads(self) -> None:
        """describe_skip returns the correct stable code for each non-fire path."""
        outside = make_snapshot(
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            in_time_window=False,
        )
        assert (
            self.handler.describe_skip(outside).code == ReasonCode.SKIP_OUTSIDE_WINDOW
        )
        sun_out = make_snapshot(
            direct_sun_valid=False,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            in_time_window=True,
        )
        assert self.handler.describe_skip(sun_out).code == ReasonCode.SKIP_CLOUD_SKIPPED
        inactive = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=True),
            climate_options=_make_options(enabled=True),
            in_time_window=True,
        )
        assert (
            self.handler.describe_skip(inactive).code == ReasonCode.SKIP_CLOUD_INACTIVE
        )


# ---------------------------------------------------------------------------
# Issue #864 — handler gates on the resolved cloud_suppression_active bool
# ---------------------------------------------------------------------------


class TestCloudHandlerResolvedBoolGate:
    """The handler consumes the manager's resolved bool, not the raw readings."""

    handler = CloudSuppressionHandler()

    def test_returns_none_when_bool_false_despite_raw_triggers(self) -> None:
        """A raw trigger is not enough: the resolved latch bool gates firing.

        The manager may be mid-hold (a brief cloud that hasn't persisted long
        enough) — the handler must defer even though ``is_sunny`` is False.
        """
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            cloud_suppression_active=False,
        )
        assert self.handler.evaluate(snap) is None

    def test_fires_on_smoothing_hold_with_no_raw_trigger(self) -> None:
        """Latch held (hysteresis / hold-time) with no raw trigger still fires.

        The manager keeps suppression asserted across the release band; the raw
        readings momentarily show no trigger, so the reason falls back to a
        smoothing-hold label.
        """
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=True),
            climate_options=_make_options(enabled=True),
            cloud_suppression_active=True,
            default_position=40,
        )
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD
        assert result.position == 40
        assert "smoothing hold" in result.reason

    def test_latch_active_but_fov_invalid_returns_none(self) -> None:
        """#417 lock: resolved bool True must NOT fire when sun is outside FOV.

        The FOV guard runs ahead of the resolved-bool gate, so the manager can
        never keep suppression asserted across an FOV exit.
        """
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            cloud_suppression_active=True,
            default_position=80,
        )
        assert self.handler.evaluate(snap) is None

    def test_latch_active_but_outside_time_window_returns_none(self) -> None:
        """Resolved bool True must NOT fire outside the operational time window."""
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_readings=_make_readings(is_sunny=False),
            climate_options=_make_options(enabled=True),
            cloud_suppression_active=True,
            in_time_window=False,
        )
        assert self.handler.evaluate(snap) is None
