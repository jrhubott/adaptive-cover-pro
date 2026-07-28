"""Shared predicate behind the `unit` / `integration` marker taxonomy.

Used by the auto-marking hook in ``tests/conftest.py`` and by the guard test in
``tests/test_marker_taxonomy.py``. Per CODING_GUIDELINES § No Duplication +
"cross-file test helpers live in ``tests/_helpers/``", it lives here rather than
being spelled twice.
"""

PHCC_PLUGIN = "pytest_homeassistant_custom_component"


def uses_real_hass(item) -> bool:
    """Return True when ``item`` resolves the real Home Assistant ``hass`` fixture.

    Building a ``HomeAssistant`` is what separates an integration test from a
    unit test — it costs ~8 registry loads and dominates the suite's runtime.

    A bare ``"hass" in item.fixturenames`` check is *not* good enough. Eight test
    modules define their own module-level ``hass`` fixture returning a
    ``MagicMock`` (``tests/test_cover_command_venetian.py``,
    ``tests/test_area_temp_resolution.py``, and six others). That shadows PHCC's
    fixture for those 64 tests, so the naive check would classify them as
    integration tests and hand a ``MagicMock`` to the autouse cleanup fixtures,
    which call ``hass.config_entries.async_entries()`` on it.

    So resolve the fixture and check where it was defined instead.
    ``name2fixturedefs`` lists overrides in definition order with the effective
    one last, so the tail entry is the fixture the item will actually receive.
    """
    if "hass" not in item.fixturenames:
        return False
    defs = item._fixtureinfo.name2fixturedefs.get("hass")
    if not defs:
        return False
    return defs[-1].func.__module__.startswith(PHCC_PLUGIN)
