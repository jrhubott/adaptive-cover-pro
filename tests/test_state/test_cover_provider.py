"""Tests for CoverProvider — reads cover positions and capabilities from HA."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.cover_provider import (
    CoverProvider,
    _DEFAULT_CAPABILITIES,
)
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities


@pytest.fixture
def hass():
    """Return a mock HomeAssistant instance."""
    h = MagicMock()
    h.states.get.return_value = None
    return h


@pytest.fixture
def provider(hass, mock_logger):
    """Return a CoverProvider instance."""
    return CoverProvider(hass=hass, logger=mock_logger)


def _mock_state(entity_id, state, attributes=None):
    """Create a mock HA state object."""
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attributes or {}
    return s


class TestDefaultCapabilities:
    """Tests for the module-level _DEFAULT_CAPABILITIES constant."""

    @pytest.mark.unit
    def test_default_capabilities_type(self):
        """_DEFAULT_CAPABILITIES is a CoverCapabilities instance."""
        assert isinstance(_DEFAULT_CAPABILITIES, CoverCapabilities)

    @pytest.mark.unit
    def test_default_capabilities_values(self):
        """Default capabilities assume position-capable cover."""
        assert _DEFAULT_CAPABILITIES.has_set_position is True
        assert _DEFAULT_CAPABILITIES.has_set_tilt_position is False
        assert _DEFAULT_CAPABILITIES.has_open is True
        assert _DEFAULT_CAPABILITIES.has_close is True


class TestReadSingleCapabilities:
    """Tests for CoverProvider.read_single_capabilities."""

    @pytest.mark.unit
    def test_returns_default_when_entity_not_ready(self, provider, hass):
        """Returns _DEFAULT_CAPABILITIES when check_cover_features returns None."""
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value=None,
        ):
            result = provider.read_single_capabilities("cover.blind_1")
        assert result == _DEFAULT_CAPABILITIES

    @pytest.mark.unit
    def test_full_capabilities_from_features(self, provider, hass):
        """Returns CoverCapabilities built from check_cover_features dict."""
        feature_dict = {
            "has_set_position": True,
            "has_set_tilt_position": True,
            "has_open": True,
            "has_close": True,
        }
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value=feature_dict,
        ):
            result = provider.read_single_capabilities("cover.blind_1")
        assert isinstance(result, CoverCapabilities)
        assert result.has_set_position is True
        assert result.has_set_tilt_position is True
        assert result.has_open is True
        assert result.has_close is True

    @pytest.mark.unit
    def test_open_close_only_cover(self, provider, hass):
        """Covers without SET_POSITION return False for has_set_position."""
        feature_dict = {
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        }
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value=feature_dict,
        ):
            result = provider.read_single_capabilities("cover.blind_1")
        assert result.has_set_position is False
        assert result.has_set_tilt_position is False

    @pytest.mark.unit
    def test_returns_frozen_dataclass(self, provider, hass):
        """Result is a frozen CoverCapabilities dataclass."""
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value={
                "has_set_position": True,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
            },
        ):
            result = provider.read_single_capabilities("cover.blind_1")
        with pytest.raises(Exception):
            result.has_set_position = False  # type: ignore[misc]


class TestReadAllCapabilities:
    """Tests for CoverProvider.read_all_capabilities."""

    @pytest.mark.unit
    def test_empty_list_returns_empty_dict(self, provider):
        """Empty entity list returns empty dict."""
        result = provider.read_all_capabilities([])
        assert result == {}

    @pytest.mark.unit
    def test_multiple_entities(self, provider):
        """Returns capabilities for each entity in the list."""
        feature_dict = {
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        }
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value=feature_dict,
        ):
            result = provider.read_all_capabilities(["cover.a", "cover.b"])
        assert set(result.keys()) == {"cover.a", "cover.b"}
        assert all(isinstance(v, CoverCapabilities) for v in result.values())

    @pytest.mark.unit
    def test_returns_default_for_unavailable_entity(self, provider):
        """Unavailable entities fall back to default capabilities."""
        with patch(
            "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
            return_value=None,
        ):
            result = provider.read_all_capabilities(["cover.unavailable"])
        assert result["cover.unavailable"] == _DEFAULT_CAPABILITIES


class TestReadPositions:
    """Tests for CoverProvider.read_positions."""

    @pytest.mark.unit
    def test_position_cover_reads_current_position(self, provider):
        """Standard blind reads current_position attribute."""
        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value={
                    "has_set_position": True,
                    "has_set_tilt_position": False,
                    "has_open": True,
                    "has_close": True,
                },
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.state_attr",
                return_value=50,
            ) as mock_attr,
        ):
            result = provider.read_positions(["cover.blind_1"], "cover_blind")
        assert result == {"cover.blind_1": 50}
        mock_attr.assert_called_once_with(
            provider._hass, "cover.blind_1", "current_position"
        )

    @pytest.mark.unit
    def test_tilt_cover_reads_tilt_position(self, provider):
        """Tilt cover reads current_tilt_position attribute."""
        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value={
                    "has_set_position": False,
                    "has_set_tilt_position": True,
                    "has_open": True,
                    "has_close": True,
                },
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.state_attr",
                return_value=45,
            ) as mock_attr,
        ):
            result = provider.read_positions(["cover.tilt_1"], "cover_tilt")
        assert result == {"cover.tilt_1": 45}
        mock_attr.assert_called_once_with(
            provider._hass, "cover.tilt_1", "current_tilt_position"
        )

    @pytest.mark.unit
    def test_open_close_cover_uses_get_open_close_state(self, provider):
        """Open/close-only cover uses get_open_close_state."""
        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value={
                    "has_set_position": False,
                    "has_set_tilt_position": False,
                    "has_open": True,
                    "has_close": True,
                },
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.get_open_close_state",
                return_value=100,
            ) as mock_oc,
        ):
            result = provider.read_positions(["cover.simple"], "cover_blind")
        assert result == {"cover.simple": 100}
        mock_oc.assert_called_once_with(provider._hass, "cover.simple")

    @pytest.mark.unit
    def test_tilt_cover_without_tilt_uses_open_close(self, provider):
        """Tilt cover type without set_tilt_position uses get_open_close_state."""
        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value={
                    "has_set_position": False,
                    "has_set_tilt_position": False,
                    "has_open": True,
                    "has_close": True,
                },
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.get_open_close_state",
                return_value=0,
            ) as mock_oc,
        ):
            result = provider.read_positions(["cover.basic_tilt"], "cover_tilt")
        assert result == {"cover.basic_tilt": 0}
        mock_oc.assert_called_once_with(provider._hass, "cover.basic_tilt")

    @pytest.mark.unit
    def test_empty_entity_list_returns_empty_dict(self, provider):
        """Empty entity list returns empty positions dict."""
        result = provider.read_positions([], "cover_blind")
        assert result == {}

    @pytest.mark.unit
    def test_none_position_when_unavailable(self, provider):
        """None is returned when entity is unavailable."""
        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value=None,
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.state_attr",
                return_value=None,
            ),
        ):
            result = provider.read_positions(["cover.unavailable"], "cover_blind")
        assert result == {"cover.unavailable": None}

    @pytest.mark.unit
    def test_multiple_covers(self, provider):
        """Multiple covers return positions keyed by entity id."""
        entities = ["cover.blind_1", "cover.blind_2"]
        caps = {
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        }

        def fake_attr(hass, entity, attr):
            return 30 if entity == "cover.blind_1" else 70

        with (
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
                return_value=caps,
            ),
            patch(
                "custom_components.adaptive_cover_pro.state.cover_provider.state_attr",
                side_effect=fake_attr,
            ),
        ):
            result = provider.read_positions(entities, "cover_blind")
        assert result == {"cover.blind_1": 30, "cover.blind_2": 70}
