"""Companion Lovelace card presence detection (issue #1168).

HACS declined the card's `plugin` submission on policy grounds — a card with a
1:1 dependency on one integration is not catalogued separately — so the card
ships as a HACS *custom repository*. A user therefore cannot find it by
searching HACS; they have to register the repository first. That is the gap the
options-flow "Dashboard Card" screen closes, and this module is the half that
answers "is it already here?".

The add itself is handed to HACS through a My Home Assistant redirect rather
than performed here. HACS registers no services — it is websocket-only, and
`websocket_api` has no in-process invoke — so the only Python route would be
`hass.data["hacs"].async_register_repository()`, an undocumented private call
that does blocking GitHub I/O. The redirect makes HACS show its own
"Add custom repository" confirm dialog instead, which is the supported handoff.

Nothing outside this module knows the card's repository slug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

# ── Card identity ───────────────────────────────────────────────────────────
# These stay here rather than in const.py by the architectural-anchor exception
# in CODING_GUIDELINES.md § No Magic Numbers: this module is the integration's
# only point of contact with the companion card, and these values are what
# define that boundary. Centralising them would spread the card's identity into
# code that has no business knowing it.
CARD_OWNER = "jrhubott"
CARD_REPO = "adaptive-cover-pro-card"
CARD_FULL_NAME = f"{CARD_OWNER}/{CARD_REPO}"
CARD_JS_FILENAME = "adaptive-cover-pro-card.js"

# HACS's wire value for a Lovelace card is "plugin". ``HacsCategory`` has no
# ``lovelace`` member and the add handler rejects anything outside the enum.
CARD_HACS_CATEGORY = "plugin"

HACS_DOMAIN = "hacs"
LOVELACE_DOMAIN = "lovelace"

# HACS reports this stage once its repository list is populated and queryable.
# Any earlier stage — or a disabled HACS (rate limit, bad token) — still leaves
# an object at hass.data["hacs"], but one whose repo list is empty or stale.
_HACS_STAGE_RUNNING = "running"

# The cache-buster users maintain by hand on a manually registered resource.
_VERSION_QUERY = "?v="

# ── Outbound links ──────────────────────────────────────────────────────────
# ``category`` is load-bearing: HACS's repository dashboard only offers its
# "Add custom repository" confirm dialog for an unknown repo when the parameter
# is present. Omit it and the user gets "Repository not found" instead.
CARD_ADD_URL = (
    "https://my.home-assistant.io/redirect/hacs_repository/"
    f"?owner={CARD_OWNER}&repository={CARD_REPO}&category={CARD_HACS_CATEGORY}"
)
CARD_DOWNLOAD_URL = f"https://github.com/{CARD_FULL_NAME}/releases/latest"
CARD_WIKI_URL = "https://github.com/jrhubott/adaptive-cover-pro/wiki/Dashboard-Cards"
HACS_DOWNLOAD_URL = "https://hacs.xyz/docs/use/download/download/"

DETECTED_VIA_HACS = "hacs"
DETECTED_VIA_RESOURCE = "resource"


@dataclass(frozen=True, slots=True)
class CardStatus:
    """What this install knows about the companion card.

    The default — every field falsy — is the honest "we found nothing" answer,
    which is also what every degraded read falls back to.
    """

    hacs_present: bool = False
    hacs_ready: bool = False
    installed: bool = False
    installed_version: str | None = None
    available_version: str | None = None
    detected_via: str | None = None


@callback
def async_get_card_status(hass: HomeAssistant) -> CardStatus:
    """Report whether the companion card is installed, and how it was found.

    Reads only in-memory state, so this is a callback rather than a coroutine.

    Two rungs: HACS when it is running and knows the repository, then the
    registered dashboard resources — which is what catches a card installed by
    hand, so a manual install is never nagged as missing.

    Every read of both is guarded. HACS is itself a custom integration with no
    versioned public API (and two known upstream bugs in the neighbouring
    lookups), and a shape change there must degrade this screen, not take the
    whole options flow down with it.
    """
    hacs = hass.data.get(HACS_DOMAIN)
    hacs_present = hacs is not None
    # Resolved once and reported verbatim, so "HACS is not ready" and "HACS was
    # ready but the read failed" stay distinguishable — inferring readiness
    # from a null result would conflate the two.
    hacs_ready = _hacs_is_ready(hacs)

    from_hacs = _status_from_hacs(hacs) if hacs_ready else None
    if from_hacs is not None and from_hacs.installed:
        return from_hacs

    resource_url = _card_resource_url(hass)
    if resource_url is not None:
        return CardStatus(
            hacs_present=hacs_present,
            hacs_ready=hacs_ready,
            installed=True,
            installed_version=_version_from_url(resource_url),
            detected_via=DETECTED_VIA_RESOURCE,
        )

    # Nothing found. The caller offers the one-click add on the strength of
    # ``hacs_present`` alone, so a HACS that could not be asked still lands on
    # a useful screen instead of a false "not installed" claim.
    return from_hacs or CardStatus(hacs_present=hacs_present, hacs_ready=hacs_ready)


def _status_from_hacs(hacs: object | None) -> CardStatus | None:
    """Return what HACS says about the card, or None if the read failed.

    The caller has already confirmed HACS is ready.
    """
    known = CardStatus(hacs_present=True, hacs_ready=True)
    try:
        # ``get_by_full_name`` lowercases its argument; ``is_registered`` does
        # not, despite storing lowercased — so the latter reports False for a
        # repository it holds. Use this one.
        repository = hacs.repositories.get_by_full_name(CARD_FULL_NAME)  # type: ignore[attr-defined]
        if repository is None:
            return known
        data = repository.data
        if not data.installed:
            return known  # added as a custom repo, but never downloaded
        return CardStatus(
            hacs_present=True,
            hacs_ready=True,
            installed=True,
            installed_version=data.installed_version,
            available_version=data.available_version,
            detected_via=DETECTED_VIA_HACS,
        )
    except Exception:
        _LOGGER.debug("Could not read the card's repository from HACS", exc_info=True)
        return None


def _hacs_is_ready(hacs: object | None) -> bool:
    """Whether HACS is loaded far enough for its repository list to be trusted."""
    if hacs is None:
        return False
    try:
        return (
            getattr(hacs, "stage", None) == _HACS_STAGE_RUNNING
            and not hacs.system.disabled  # type: ignore[attr-defined]
        )
    except Exception:
        _LOGGER.debug("Could not read HACS readiness", exc_info=True)
        return False


def _card_resource_url(hass: HomeAssistant) -> str | None:
    """Return the card's registered dashboard-resource URL, if one exists.

    Matches on the bundle filename so both the HACS path
    (``/hacsfiles/...``) and a hand-registered ``/local/...`` copy are found.
    """
    try:
        resources = getattr(hass.data.get(LOVELACE_DOMAIN), "resources", None)
        items = resources.async_items() if resources is not None else ()
        for item in items:
            url = item.get("url") or ""
            if CARD_JS_FILENAME in url:
                return url
    except Exception:
        _LOGGER.debug("Could not read the dashboard resources", exc_info=True)
    return None


def _version_from_url(url: str) -> str | None:
    """Lift the ``?v=`` stamp a manual install carries, when it has one."""
    return url.partition(_VERSION_QUERY)[2] or None
