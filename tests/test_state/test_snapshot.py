"""Tests for unified state snapshot dataclasses."""

import pytest

from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from custom_components.adaptive_cover_pro.state.snapshot import (
    CoverCapabilities,
    CoverStateSnapshot,
    SunSnapshot,
)


class TestSunSnapshot:
    """Tests for SunSnapshot frozen dataclass."""

    @pytest.mark.unit
    def test_construction(self):
        """SunSnapshot can be constructed with azimuth and elevation."""
        snap = SunSnapshot(azimuth=180.0, elevation=45.0)
        assert snap.azimuth == 180.0
        assert snap.elevation == 45.0

    @pytest.mark.unit
    def test_frozen(self):
        """SunSnapshot is immutable."""
        snap = SunSnapshot(azimuth=180.0, elevation=45.0)
        with pytest.raises(Exception):
            snap.azimuth = 90.0  # type: ignore[misc]

    @pytest.mark.unit
    def test_equality(self):
        """Two SunSnapshots with same values are equal."""
        a = SunSnapshot(azimuth=180.0, elevation=45.0)
        b = SunSnapshot(azimuth=180.0, elevation=45.0)
        assert a == b

    @pytest.mark.unit
    def test_inequality(self):
        """SunSnapshots with different values are not equal."""
        a = SunSnapshot(azimuth=180.0, elevation=45.0)
        b = SunSnapshot(azimuth=270.0, elevation=45.0)
        assert a != b

    @pytest.mark.unit
    def test_zero_values(self):
        """SunSnapshot accepts zero values."""
        snap = SunSnapshot(azimuth=0.0, elevation=0.0)
        assert snap.azimuth == 0.0
        assert snap.elevation == 0.0

    @pytest.mark.unit
    def test_negative_elevation(self):
        """SunSnapshot accepts negative elevation (below horizon)."""
        snap = SunSnapshot(azimuth=180.0, elevation=-5.0)
        assert snap.elevation == -5.0


class TestCoverCapabilities:
    """Tests for CoverCapabilities frozen dataclass."""

    @pytest.mark.unit
    def test_full_capabilities(self):
        """CoverCapabilities with all features enabled."""
        caps = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=True,
            has_open=True,
            has_close=True,
        )
        assert caps.has_set_position is True
        assert caps.has_set_tilt_position is True
        assert caps.has_open is True
        assert caps.has_close is True

    @pytest.mark.unit
    def test_minimal_capabilities(self):
        """CoverCapabilities for open/close-only cover."""
        caps = CoverCapabilities(
            has_set_position=False,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        assert caps.has_set_position is False
        assert caps.has_set_tilt_position is False

    @pytest.mark.unit
    def test_frozen(self):
        """CoverCapabilities is immutable."""
        caps = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        with pytest.raises(Exception):
            caps.has_set_position = False  # type: ignore[misc]

    @pytest.mark.unit
    def test_equality(self):
        """Two CoverCapabilities with same values are equal."""
        a = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        b = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        assert a == b

    @pytest.mark.unit
    def test_hashable(self):
        """CoverCapabilities can be used as a dict key (frozen dataclass)."""
        caps = CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        d = {caps: "value"}
        assert d[caps] == "value"


class TestCoverStateSnapshot:
    """Tests for CoverStateSnapshot frozen dataclass."""

    def _make_sun(self):
        return SunSnapshot(azimuth=180.0, elevation=30.0)

    def _make_caps(self):
        return CoverCapabilities(
            has_set_position=True,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )

    @pytest.mark.unit
    def test_construction_no_climate(self):
        """CoverStateSnapshot constructs correctly without climate data."""
        caps = self._make_caps()
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=None,
            cover_positions={"cover.blind_1": 50},
            cover_capabilities={"cover.blind_1": caps},
            motion_detected=False,
            force_override_active=False,
        )
        assert snap.sun.azimuth == 180.0
        assert snap.sun.elevation == 30.0
        assert snap.climate is None
        assert snap.cover_positions == {"cover.blind_1": 50}
        assert snap.cover_capabilities == {"cover.blind_1": caps}
        assert snap.motion_detected is False
        assert snap.force_override_active is False

    @pytest.mark.unit
    def test_construction_with_climate(self):
        """CoverStateSnapshot stores ClimateReadings when provided."""
        climate = ClimateReadings(
            outside_temperature=22.0,
            inside_temperature=21.0,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=climate,
            cover_positions={},
            cover_capabilities={},
            motion_detected=False,
            force_override_active=False,
        )
        assert snap.climate is climate
        assert snap.climate.outside_temperature == 22.0

    @pytest.mark.unit
    def test_frozen(self):
        """CoverStateSnapshot is immutable."""
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=None,
            cover_positions={},
            cover_capabilities={},
            motion_detected=False,
            force_override_active=False,
        )
        with pytest.raises(Exception):
            snap.motion_detected = True  # type: ignore[misc]

    @pytest.mark.unit
    def test_motion_and_force_override_flags(self):
        """Flags are stored accurately."""
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=None,
            cover_positions={},
            cover_capabilities={},
            motion_detected=True,
            force_override_active=True,
        )
        assert snap.motion_detected is True
        assert snap.force_override_active is True

    @pytest.mark.unit
    def test_multiple_covers(self):
        """Snapshot can hold multiple cover positions and capabilities."""
        caps1 = self._make_caps()
        caps2 = CoverCapabilities(
            has_set_position=False,
            has_set_tilt_position=False,
            has_open=True,
            has_close=True,
        )
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=None,
            cover_positions={"cover.blind_1": 50, "cover.blind_2": None},
            cover_capabilities={"cover.blind_1": caps1, "cover.blind_2": caps2},
            motion_detected=False,
            force_override_active=False,
        )
        assert len(snap.cover_positions) == 2
        assert snap.cover_positions["cover.blind_2"] is None
        assert snap.cover_capabilities["cover.blind_2"].has_set_position is False

    @pytest.mark.unit
    def test_empty_cover_lists(self):
        """Snapshot is valid with no cover entities."""
        snap = CoverStateSnapshot(
            sun=self._make_sun(),
            climate=None,
            cover_positions={},
            cover_capabilities={},
            motion_detected=False,
            force_override_active=False,
        )
        assert snap.cover_positions == {}
        assert snap.cover_capabilities == {}
