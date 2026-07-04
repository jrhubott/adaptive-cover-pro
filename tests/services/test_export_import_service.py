"""Unit tests for export_all_config and import_config services."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    entry_id: str, title: str, options: dict, domain: str = "adaptive_cover_pro"
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.options = options
    entry.domain = domain
    return entry


def _make_hass(entries: list[MagicMock], config_dir: str = "/config") -> MagicMock:
    """Build a minimal hass mock with a config_entries registry and hass.config."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *a, **kw: fn(*a, **kw)
    )

    hass.config.config_dir = config_dir
    hass.config.path.side_effect = lambda *parts: str(
        pathlib.Path(config_dir).joinpath(*parts)
    )

    entry_map = {e.entry_id: e for e in entries}

    def _get_entry(entry_id):
        return entry_map.get(entry_id)

    def _update_entry(entry, *, options):
        entry.options = options

    hass.config_entries.async_get_entry.side_effect = _get_entry
    hass.config_entries.async_update_entry.side_effect = _update_entry
    return hass


# ---------------------------------------------------------------------------
# export_all_config tests
# ---------------------------------------------------------------------------


class TestExportAll:
    """Tests for async_handle_export_all."""

    @pytest.mark.asyncio
    async def test_happy_path_writes_file_and_returns_count(self, tmp_path):
        """All loaded coordinators are exported to the file."""
        from custom_components.adaptive_cover_pro.services.export_service import (
            async_handle_export_all,
        )

        entry1 = _make_entry("id-1", "Blind A", {"azimuth": 180, "fov_left": 30})
        entry2 = _make_entry(
            "id-2", "Blind B", {"azimuth": 270, "_orphan_prune_v1": True}
        )
        hass = _make_hass([entry1, entry2], config_dir=str(tmp_path))

        coord1, coord2 = MagicMock(), MagicMock()
        coordinators = {"id-1": coord1, "id-2": coord2}

        call = MagicMock()
        call.hass = hass

        with patch(
            "custom_components.adaptive_cover_pro.services.loaded_coordinators",
            return_value=coordinators,
        ):
            result = await async_handle_export_all(call)

        export_path = tmp_path / "adaptive_cover_pro_export.json"
        assert result["count"] == 2
        assert result["file"] == str(export_path)

        data = json.loads(export_path.read_text("utf-8"))
        assert data["export_version"] == 1
        assert len(data["entries"]) == 2
        ids = {e["entry_id"] for e in data["entries"]}
        assert ids == {"id-1", "id-2"}

    @pytest.mark.asyncio
    async def test_internal_keys_included_in_export(self, tmp_path):
        """Internal _-prefixed keys are exported verbatim (lossless snapshot)."""
        from custom_components.adaptive_cover_pro.services.export_service import (
            async_handle_export_all,
        )

        opts = {
            "azimuth": 90,
            "_orphan_prune_v1": True,
            "_orphan_prune_sensors_v2": True,
        }
        entry = _make_entry("id-x", "Test", opts)
        hass = _make_hass([entry], config_dir=str(tmp_path))
        call = MagicMock()
        call.hass = hass

        with patch(
            "custom_components.adaptive_cover_pro.services.loaded_coordinators",
            return_value={"id-x": MagicMock()},
        ):
            await async_handle_export_all(call)

        export_path = tmp_path / "adaptive_cover_pro_export.json"
        data = json.loads(export_path.read_text("utf-8"))
        exported_opts = data["entries"][0]["options"]
        assert exported_opts["_orphan_prune_v1"] is True
        assert exported_opts["_orphan_prune_sensors_v2"] is True

    @pytest.mark.asyncio
    async def test_empty_coordinators_writes_empty_entries(self, tmp_path):
        """An empty coordinator map produces a file with an empty entries list."""
        from custom_components.adaptive_cover_pro.services.export_service import (
            async_handle_export_all,
        )

        hass = _make_hass([], config_dir=str(tmp_path))
        call = MagicMock()
        call.hass = hass

        with patch(
            "custom_components.adaptive_cover_pro.services.loaded_coordinators",
            return_value={},
        ):
            result = await async_handle_export_all(call)

        assert result["count"] == 0
        export_path = tmp_path / "adaptive_cover_pro_export.json"
        data = json.loads(export_path.read_text("utf-8"))
        assert data["entries"] == []


# ---------------------------------------------------------------------------
# import_config tests
# ---------------------------------------------------------------------------


class TestImportConfig:
    """Tests for async_handle_import_config."""

    def _write_export(self, path: pathlib.Path, entries: list[dict]) -> None:
        payload = {
            "export_version": 1,
            "exported_at": "2026-07-02T00:00:00+00:00",
            "entries": entries,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    @pytest.mark.asyncio
    async def test_happy_path_updates_entries(self, tmp_path):
        """Imported public keys are applied; internal keys from live entry preserved."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        live_opts = {"azimuth": 90, "_orphan_prune_v1": True}
        entry = _make_entry("id-1", "Blind A", live_opts)
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"azimuth": 270, "fov_left": 45}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"] == "updated"
        assert entry.options["azimuth"] == 270
        assert entry.options["fov_left"] == 45
        assert entry.options["_orphan_prune_v1"] is True

    @pytest.mark.asyncio
    async def test_internal_keys_in_file_are_ignored(self, tmp_path):
        """_-prefixed keys in the import file are stripped; live entry keeps its own."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry(
            "id-2", "Blind B", {"azimuth": 90, "_orphan_prune_v1": True}
        )
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [
                {
                    "entry_id": "id-2",
                    "options": {"azimuth": 180, "_orphan_prune_v1": False},
                }
            ],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        await async_handle_import_config(call)

        # File had _orphan_prune_v1=False but live entry had True — live wins
        assert entry.options["_orphan_prune_v1"] is True
        assert entry.options["azimuth"] == 180

    @pytest.mark.asyncio
    async def test_unknown_entry_id_is_skipped(self, tmp_path):
        """Entry IDs not present in the live HA registry are recorded as 'skipped'."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        hass = _make_hass([], config_dir=str(tmp_path))
        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "ghost-id", "options": {"azimuth": 90}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["ghost-id"] == "skipped"

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tmp_path):
        """Filename resolving outside the config dir raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        call = MagicMock()
        call.hass = _make_hass([], config_dir=str(tmp_path))
        call.data = {"filename": "/etc/passwd"}

        with pytest.raises(ServiceValidationError, match="not inside the HA config"):
            await async_handle_import_config(call)

    @pytest.mark.asyncio
    async def test_path_traversal_via_dotdot_rejected(self, tmp_path):
        """Path traversal using ../ is resolved and rejected."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        call = MagicMock()
        call.hass = _make_hass([], config_dir=str(tmp_path))
        # Build a path that starts inside tmp_path but escapes via ..
        traversal = str(tmp_path / ".." / "etc" / "shadow")
        call.data = {"filename": traversal}

        with pytest.raises(ServiceValidationError, match="not inside the HA config"):
            await async_handle_import_config(call)

    @pytest.mark.asyncio
    async def test_missing_file_raises_validation_error(self, tmp_path):
        """A filename that does not exist raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        hass = _make_hass([], config_dir=str(tmp_path))
        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(tmp_path / "does_not_exist_xyz.json")}

        with pytest.raises(ServiceValidationError, match="cannot read"):
            await async_handle_import_config(call)

    @pytest.mark.asyncio
    async def test_invalid_json_raises_validation_error(self, tmp_path):
        """A file containing non-JSON text raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("this is not json", encoding="utf-8")

        hass = _make_hass([], config_dir=str(tmp_path))
        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(bad_file)}

        with pytest.raises(ServiceValidationError, match="not valid JSON"):
            await async_handle_import_config(call)

    @pytest.mark.asyncio
    async def test_wrong_shape_json_raises_validation_error(self, tmp_path):
        """Valid JSON that is not an ACP export shape raises ServiceValidationError."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        for bad_payload in [[], "a string", 42, {"no_entries_key": True}]:
            bad_file = tmp_path / "wrong_shape.json"
            bad_file.write_text(json.dumps(bad_payload), encoding="utf-8")

            hass = _make_hass([], config_dir=str(tmp_path))
            call = MagicMock()
            call.hass = hass
            call.data = {"filename": str(bad_file)}

            with pytest.raises(ServiceValidationError, match="not a valid ACP export"):
                await async_handle_import_config(call)

    @pytest.mark.asyncio
    async def test_out_of_range_value_recorded_as_error(self, tmp_path):
        """A numeric value outside OPTION_RANGES bounds records an error for that entry."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {"set_azimuth": 90})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        # set_azimuth valid range is [0, 359]; 999 is out of range
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"set_azimuth": 999}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"].startswith("error:")
        assert "set_azimuth" in result["id-1"]
        # entry options must not have been modified
        assert entry.options["set_azimuth"] == 90

    @pytest.mark.asyncio
    async def test_mixed_results(self, tmp_path):
        """One valid entry and one unknown entry produce mixed results dict."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-good", "Blind Good", {"azimuth": 90})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [
                {"entry_id": "id-good", "options": {"azimuth": 180}},
                {"entry_id": "id-ghost", "options": {"azimuth": 45}},
            ],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-good"] == "updated"
        assert result["id-ghost"] == "skipped"
