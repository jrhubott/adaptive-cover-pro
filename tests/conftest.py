"""Pytest fixtures for Adaptive Cover Pro tests."""

# This fixture is provided by pytest-homeassistant-custom-component.
# We auto-use it globally so the real ``hass`` fixture can discover and
# load our custom integration from the local ``custom_components/`` directory.
pytest_plugins = ["pytest_homeassistant_custom_component"]

# --- HA / pytest-homeassistant-custom-component compatibility shim ---
# PHCC's ``disable_http_server`` fixture runs
# ``patch("homeassistant.components.http.start_http_server_and_save_config")``
# with no ``create=True``. That symbol only exists in a narrow band of HA
# releases, so on the HA versions our CI matrix installs (min / stable / dev)
# the patch raises AttributeError and every test using the ``hass`` fixture
# errors at setup. Pre-create a harmless stub when HA lacks the symbol so the
# patch target exists; when HA already defines it we leave the real one alone.
import homeassistant.components.http as _ha_http

if not hasattr(_ha_http, "start_http_server_and_save_config"):
    _ha_http.start_http_server_and_save_config = lambda *args, **kwargs: None

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from homeassistant.core import State

from custom_components.adaptive_cover_pro.config_context_adapter import (
    ConfigContextAdapter,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy


def make_snapshot_for_cover(
    cover, default_position: int = 0, cover_type: str = "cover_blind"
) -> SimpleNamespace:
    """Create a minimal PipelineSnapshot-compatible namespace for ClimateCoverState tests.

    ``ClimateCoverState`` now takes a full ``PipelineSnapshot`` instead of
    separate ``cover`` + ``default_position`` arguments.  This helper wraps
    a real cover engine object in a lightweight ``SimpleNamespace`` that
    satisfies all attribute accesses made by ``ClimateCoverState`` and the
    shared pipeline helpers (``compute_solar_position``, ``apply_snapshot_limits``).

    Args:
        cover:            An ``AdaptiveGeneralCover`` (or mock) instance.
        default_position: Effective default position (sunset-aware int).
        cover_type:       Cover type string (default ``"cover_blind"``).

    Returns:
        SimpleNamespace with ``cover``, ``config``, ``cover_type``,
        ``default_position``, and ``is_sunset_active`` set.

    """
    return SimpleNamespace(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        default_position=default_position,
        is_sunset_active=False,
    )


from .cover_helpers import (  # noqa: F401 — re-exported for convenience
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
    make_cover_config,
    make_horizontal_config,
    make_tilt_config,
    make_vertical_config,
)


@pytest.fixture(autouse=True)
async def _auto_unload_config_entries(request, verify_cleanup):
    """Unload config entries created during integration tests to prevent lingering timers.

    Explicitly depends on verify_cleanup so this fixture is set up AFTER it and
    therefore tears down BEFORE it — guaranteeing entries are unloaded before
    verify_cleanup checks for lingering timer handles.
    """
    if not request.node.get_closest_marker("integration"):
        yield
        return

    hass = request.getfixturevalue("hass")
    entries_before = {e.entry_id for e in hass.config_entries.async_entries()}
    yield

    from homeassistant.config_entries import ConfigEntryState

    new_entries = [
        e
        for e in hass.config_entries.async_entries()
        if e.entry_id not in entries_before and e.state == ConfigEntryState.LOADED
    ]
    for entry in new_entries:
        await hass.config_entries.async_unload(entry.entry_id)
    if new_entries:
        await hass.async_block_till_done()


@pytest.fixture
def expected_lingering_timers(request) -> bool:
    """Tolerate the EntityPlatform polling-timer leak for integration tests.

    HA's EntityPlatform schedules its polling timer with raw `loop.call_later`
    (entity_platform.py:748), not via a HassJob, so verify_cleanup cannot
    distinguish a safe-to-leak timer from a real leak. HA core works around
    this for its own non-base-platform tests; we follow the same pattern for
    our @pytest.mark.integration tests. Overrides the plugin's same-named
    autouse fixture by name resolution.
    """
    if request.node.get_closest_marker("integration"):
        return True
    return False


@pytest.fixture
def neutralize_venetian_delays(monkeypatch):
    """Zero the venetian sequencer's real-motor sleep delays for unit tests.

    The dual-axis sequencer waits on several real ``asyncio.sleep`` timers
    that model physical actuator lag (post-settle hold, post-tilt rebase,
    drift-retry, tilt-verify poll). In production these are seconds; in tests
    they add up to ~50 s of pure idle-waiting across the venetian suites.

    Single source of truth for that neutralization — venetian behavioral test
    files opt in with
    ``pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")``
    instead of each re-declaring its own delay-zeroing fixture.

    Only the *pure-delay* constants are touched. The settle-loop constants
    (``..._POLL_SECONDS`` / ``..._STARTUP_GRACE_SECONDS`` / ``..._TIMEOUT_SECONDS``)
    drive branch logic and are asserted on by some tests, so they are left at
    production values; the few settle-loop tests patch them locally.
    """
    seq = "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
    monkeypatch.setattr(seq + "VENETIAN_POST_TILT_REBASE_DELAY_SECONDS", 0)
    monkeypatch.setattr(seq + "VENETIAN_TILT_VERIFY_POLL_SECONDS", 0)
    monkeypatch.setattr(seq + "VENETIAN_DRIFT_RETRY_DELAY_SECONDS", 0)
    # The 3.0 s post-settle hold is an ``attach()`` default, not a sleep-site
    # constant; zero the default so policies built without an explicit value
    # don't pay it. Patched in the policy namespace only — the config-summary
    # path reads the const namespace, so summary tests keep the 3.0 s default.
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.cover_types.venetian.policy."
        "DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS",
        0,
    )


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(request):
    """Auto-enable custom integration discovery for real HA integration tests.

    Only activates when the test is marked @pytest.mark.integration,
    avoiding overhead for the fast mock-hass unit tests.
    """
    if request.node.get_closest_marker("integration"):
        # Request the plugin's fixture by name via indirect call
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance (MagicMock, not real HA).

    Named mock_hass to avoid collision with the real ``hass`` fixture
    provided by pytest-homeassistant-custom-component.
    """
    hass_mock = MagicMock()
    hass_mock.states.get.return_value = None
    hass_mock.config.units.temperature_unit = "°C"
    return hass_mock


@pytest.fixture
def mock_logger():
    """Return a mock ConfigContextAdapter logger."""
    logger = MagicMock(spec=ConfigContextAdapter)
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def mock_sun_data():
    """Return a mock SunData instance."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return sun_data


@pytest.fixture
def sample_vertical_config():
    """Return standard vertical cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 50,
        "d_top": 0.0,
        "d_bottom": 2.0,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_horizontal_config():
    """Return standard horizontal cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 100,
        "length": 2.0,
        "awning_angle": 0,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_tilt_config():
    """Return standard tilt cover configuration for testing."""
    return {
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "win_elev": 90,
        "distance": 0.5,
        "h_def": 50,
        "slat_depth": 0.02,
        "slat_distance": 0.03,
        "tilt_mode": "mode1",
        "tilt_distance": 0.5,
        "max_pos": 100,
        "min_pos": 0,
        "blind_spot_config": {},
        "sunset_pos": 0,
        "sunset_off": 0,
    }


@pytest.fixture
def sample_climate_config():
    """Return standard climate mode configuration for testing."""
    return {
        "temp_entity": "sensor.outside_temperature",
        "temp_low": 20.0,
        "temp_high": 25.0,
        "presence_entity": "binary_sensor.presence",
        "weather_entity": "weather.home",
        "weather_state": ["sunny", "partlycloudy"],
        "lux_entity": None,
        "lux_threshold": None,
        "irradiance_entity": None,
        "irradiance_threshold": None,
    }


@pytest.fixture
def mock_state():
    """Return a mock Home Assistant state object."""

    def _create_state(entity_id: str, state: str, attributes: dict | None = None):
        state_obj = MagicMock(spec=State)
        state_obj.entity_id = entity_id
        state_obj.state = state
        state_obj.attributes = attributes or {}
        return state_obj

    return _create_state


@pytest.fixture
def vertical_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveVerticalCover instance for testing."""
    return build_vertical_cover(
        logger=mock_logger,
        sol_azi=180.0,
        sol_elev=45.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=mock_sun_data,
        fov_left=45,
        fov_right=45,
        win_azi=180,
        h_def=50,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=0.5,
        h_win=2.0,
    )


@pytest.fixture
def horizontal_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveHorizontalCover instance for testing."""
    return build_horizontal_cover(
        logger=mock_logger,
        sol_azi=180.0,
        sol_elev=45.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=mock_sun_data,
        fov_left=45,
        fov_right=45,
        win_azi=180,
        h_def=100,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        distance=0.5,
        h_win=2.0,
        awn_length=2.0,
        awn_angle=0,
    )


@pytest.fixture
def tilt_cover_instance(mock_sun_data, mock_logger):
    """Real AdaptiveTiltCover instance for testing."""
    return build_tilt_cover(
        logger=mock_logger,
        sol_azi=180.0,
        sol_elev=45.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=mock_sun_data,
        fov_left=45,
        fov_right=45,
        win_azi=180,
        h_def=50,
        max_pos=100,
        min_pos=0,
        max_pos_bool=False,
        min_pos_bool=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
    )


@pytest.fixture
def climate_data_instance():
    """ClimateCoverData instance with pre-read values."""
    from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
        ClimateCoverData,
    )

    return ClimateCoverData(
        temp_low=20.0,
        temp_high=25.0,
        temp_switch=True,
        policy=get_policy("cover_blind"),
        transparent_blind=False,
        temp_summer_outside=22.0,
        outside_temperature="22.5",
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        winter_close_insulation=False,
    )
