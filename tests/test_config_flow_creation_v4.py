"""Config-flow creation tests for the v4 redesign (2-screen flow).

The v4 redesign collapsed creation to two screens — cover-entity picker
(Screen 1) and minimum window geometry (Screen 2). The cover type is
inferred from the entity's HA ``device_class`` and the cover's parent
device is auto-attached unless the user opts out. Older multi-step tests
in ``test_config_flow_integration.py`` are skipped pending Phase 5
rewrite; this file is the canonical home for the new flow's coverage.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_COVER_TYPE,
    CONF_COVERS,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_LENGTH_AWNING,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _register_cover(
    hass: HomeAssistant,
    *,
    object_id: str,
    device_class: str | None,
    device_name: str | None = None,
    original_name: str | None = None,
) -> tuple[str, str | None]:
    """Register a cover entity in the registry (optionally attached to a device).

    Returns ``(entity_id, device_id)``. ``device_id`` is None when no device
    was created.
    """
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)
    device_id: str | None = None
    if device_name is not None:
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        anchor = MockConfigEntry(domain="zha", data={}, title="ZHA anchor")
        anchor.add_to_hass(hass)
        device_entry = device_reg.async_get_or_create(
            config_entry_id=anchor.entry_id,
            identifiers={("zha", object_id)},
            name=device_name,
        )
        device_id = device_entry.id

    entry = entity_reg.async_get_or_create(
        "cover",
        "zha",
        f"unique_{object_id}",
        suggested_object_id=object_id,
        device_id=device_id,
        original_device_class=device_class,
        original_name=original_name,
    )
    return entry.entity_id, device_id


# ---------------------------------------------------------------------------
# Screen 1: cover-entity-first creation
# ---------------------------------------------------------------------------


async def test_creation_shade_device_class_creates_blind_entry(
    hass: HomeAssistant,
) -> None:
    """A device_class=shade entity routes directly to window_basics → entry created."""
    entity_id, device_id = _register_cover(
        hass,
        object_id="patio_shade",
        device_class="shade",
        device_name="Patio Shade Motor",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    assert result["step_id"] == "create_new"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"cover_entity": entity_id, "attach_to_device": True},
    )
    # shade → cover_blind is unambiguous; flow proceeds straight to window_basics.
    assert result["type"] == "form"
    assert result["step_id"] == "window_basics"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    assert result["step_id"] == "summary"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"

    entry = result["result"]
    assert entry.data[CONF_COVER_TYPE] == CoverType.BLIND
    assert entry.options[CONF_COVERS] == [entity_id]
    assert entry.options[CONF_AZIMUTH] == 180
    assert entry.options[CONF_HEIGHT_WIN] == 2.1
    assert entry.options[CONF_DISTANCE] == 0.5
    # Device auto-attach was enabled, so the device id from the entity propagates.
    assert entry.options[CONF_DEVICE_ID] == device_id


async def test_creation_awning_device_class_routes_to_length_field(
    hass: HomeAssistant,
) -> None:
    """device_class=awning → AwningPolicy; window_basics asks for length, not height."""
    entity_id, _ = _register_cover(
        hass, object_id="patio_awning", device_class="awning"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"cover_entity": entity_id, "attach_to_device": False},
    )
    assert result["step_id"] == "window_basics"
    schema_keys = [str(k) for k in result["data_schema"].schema]
    assert CONF_LENGTH_AWNING in schema_keys
    assert CONF_HEIGHT_WIN not in schema_keys


async def test_creation_blind_device_class_prompts_disambiguation(
    hass: HomeAssistant,
) -> None:
    """device_class=blind is ambiguous — flow routes through the disambiguation prompt."""
    entity_id, _ = _register_cover(
        hass, object_id="bedroom_blind", device_class="blind"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id}
    )
    assert result["step_id"] == "disambiguate_cover_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COVER_TYPE: CoverType.VENETIAN}
    )
    assert result["step_id"] == "window_basics"


async def test_creation_unsupported_device_class_aborts(hass: HomeAssistant) -> None:
    """device_class=garage is unsupported — flow aborts before window_basics."""
    entity_id, _ = _register_cover(hass, object_id="my_garage", device_class="garage")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "unsupported_cover_device_class"


# ---------------------------------------------------------------------------
# Device auto-attach behaviour
# ---------------------------------------------------------------------------


async def test_creation_attach_to_device_default_on_populates_linked_device_id(
    hass: HomeAssistant,
) -> None:
    """When attach_to_device is left at True (the default), CONF_DEVICE_ID is set."""
    entity_id, device_id = _register_cover(
        hass,
        object_id="living_blind",
        device_class="shade",
        device_name="Living Blind Motor",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    # Submit without explicit attach_to_device → defaults to True.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].options[CONF_DEVICE_ID] == device_id


async def test_creation_attach_to_device_unchecked_omits_linked_device_id(
    hass: HomeAssistant,
) -> None:
    """Unchecking attach_to_device leaves CONF_DEVICE_ID absent — standalone device path."""
    entity_id, _ = _register_cover(
        hass,
        object_id="standalone_blind",
        device_class="shade",
        device_name="Some Motor",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"cover_entity": entity_id, "attach_to_device": False},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 90, CONF_HEIGHT_WIN: 1.8, CONF_DISTANCE: 0.4},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].options.get(CONF_DEVICE_ID) is None


async def test_creation_no_parent_device_works_with_or_without_attach(
    hass: HomeAssistant,
) -> None:
    """A cover entity without a parent device produces an entry with no CONF_DEVICE_ID."""
    entity_id, _ = _register_cover(
        hass,
        object_id="floating_shade",
        device_class="shade",
        device_name=None,  # no device created
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id, "attach_to_device": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].options.get(CONF_DEVICE_ID) is None


# ---------------------------------------------------------------------------
# Name auto-derivation
# ---------------------------------------------------------------------------


async def test_creation_name_derives_from_device_when_blank(
    hass: HomeAssistant,
) -> None:
    """A blank name auto-derives from the parent device's name (no type prefix)."""
    entity_id, _ = _register_cover(
        hass,
        object_id="kitchen_shade",
        device_class="shade",
        device_name="Kitchen Shade Motor",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].title == "Kitchen Shade Motor"


async def test_creation_user_typed_name_overrides_auto_derivation(
    hass: HomeAssistant,
) -> None:
    """A user-typed name wins over device-name auto-derivation."""
    entity_id, _ = _register_cover(
        hass,
        object_id="office_shade",
        device_class="shade",
        device_name="Office Shade Motor",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"cover_entity": entity_id, "name": "My Custom Name"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert "My Custom Name" in result["result"].title


async def test_creation_no_device_name_falls_back_to_adaptive_prefix(
    hass: HomeAssistant,
) -> None:
    """Without a device, the auto-derived name uses the 'Adaptive {entity}' fallback."""
    entity_id, _ = _register_cover(
        hass,
        object_id="orphan_cover",
        device_class="shade",
        device_name=None,
        original_name="Orphan Cover",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity": entity_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1, CONF_DISTANCE: 0.5},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    # Type prefix is added at title-build time when the name wasn't a device name.
    assert "Adaptive Orphan Cover" in result["result"].title


# ---------------------------------------------------------------------------
# Cover-type inference unit checks
# ---------------------------------------------------------------------------


def test_infer_cover_type_unambiguous_classes() -> None:
    """Awning → AWNING; shutter/curtain/shade → BLIND."""
    from custom_components.adaptive_cover_pro.cover_types._helpers import (
        infer_cover_type_from_device_class,
    )

    assert infer_cover_type_from_device_class("awning") == CoverType.AWNING
    assert infer_cover_type_from_device_class("shutter") == CoverType.BLIND
    assert infer_cover_type_from_device_class("curtain") == CoverType.BLIND
    assert infer_cover_type_from_device_class("shade") == CoverType.BLIND


def test_infer_cover_type_ambiguous_blind_returns_none() -> None:
    """device_class=blind is ambiguous (roller vs venetian)."""
    from custom_components.adaptive_cover_pro.cover_types._helpers import (
        infer_cover_type_from_device_class,
    )

    assert infer_cover_type_from_device_class("blind") is None


def test_infer_cover_type_unsupported_classes_return_sentinel() -> None:
    """garage/gate/door/damper map to the explicit 'unsupported' sentinel."""
    from custom_components.adaptive_cover_pro.cover_types._helpers import (
        INFERENCE_UNSUPPORTED,
        infer_cover_type_from_device_class,
    )

    for dc in ("garage", "gate", "door", "damper"):
        assert infer_cover_type_from_device_class(dc) == INFERENCE_UNSUPPORTED


def test_infer_cover_type_missing_or_unknown_returns_none() -> None:
    """Missing or unknown device_class returns None (caller asks the user)."""
    from custom_components.adaptive_cover_pro.cover_types._helpers import (
        infer_cover_type_from_device_class,
    )

    assert infer_cover_type_from_device_class(None) is None
    assert infer_cover_type_from_device_class("weird_custom_class") is None
