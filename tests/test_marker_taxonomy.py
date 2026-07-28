"""Locks on the `unit` / `integration` marker taxonomy.

`./scripts/test unit` and CI's selectors are only trustworthy if every test
carries exactly one of the two markers and the markers mean what they say.
`tests/conftest.py::pytest_collection_modifyitems` establishes that; these tests
stop it from silently regressing — e.g. if a future hook edit skips a class of
items, or someone hand-marks a `hass` test as `unit`.

`request.session.items` is the full collected list even under xdist, because
every worker collects the whole suite before running its assigned share. It is
*not* full when the run itself is filtered — under `-m unit` or `-k foo` these
checks only see what survived selection. That is fine: the gate that matters is
the unfiltered run CI and `./scripts/test` both perform.
"""

import pytest

from ._helpers.markers import uses_real_hass

pytestmark = pytest.mark.unit

TAXONOMY = {"unit", "integration"}


def test_every_test_carries_exactly_one_taxonomy_marker(request):
    """No test may be unmarked, and none may claim to be both."""
    offenders = [
        (item.nodeid, sorted({m.name for m in item.iter_markers()} & TAXONOMY))
        for item in request.session.items
        if len({m.name for m in item.iter_markers()} & TAXONOMY) != 1
    ]
    assert not offenders, (
        f"{len(offenders)} test(s) lack exactly one of {sorted(TAXONOMY)}: "
        f"{offenders[:10]}"
    )


def test_unit_marker_never_builds_a_home_assistant(request):
    """`unit` must mean "no HomeAssistant" — that is the whole point of the split.

    A test marked `unit` that resolves the real `hass` fixture makes
    `./scripts/test unit` slower than it claims and hides an integration test in
    the fast lane. Mark it `integration` instead.
    """
    offenders = [
        item.nodeid
        for item in request.session.items
        if item.get_closest_marker("unit") and uses_real_hass(item)
    ]
    assert not offenders, (
        f"{len(offenders)} test(s) marked `unit` build a real HomeAssistant: "
        f"{offenders[:10]}"
    )


def test_taxonomy_markers_are_registered(pytestconfig):
    """`--strict-markers` is on, so an unregistered marker fails collection."""
    registered = {
        line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")
    }
    assert (
        registered >= TAXONOMY
    ), f"missing from pyproject.toml: {TAXONOMY - registered}"
