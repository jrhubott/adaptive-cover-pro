"""Unit tests for export_all_config and import_config services."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Time values the config flow's TimeSelector can never emit. Each compares
# unequal to BLANK_TIME ("00:00:00") while the runtime still treats it as a
# configured window end — the whole of issue #1049.
#
# They split by whether a parser can rescue them, and import treats the halves
# differently on purpose. An export file predates this validation, so rejecting
# a rescuable value would drop every *other* option for that entry on a restore
# — punishing precisely the users #1049 already bit. So import canonicalises
# what it can and errors only on the rest.
_RESCUABLE_TIMES = [
    ("00:00", "00:00:00"),  # HH:MM — the reported case
    ("0:00:00", "00:00:00"),  # unpadded hour
    ("00:00:00\n", "00:00:00"),  # trailing newline: `$` would let this through
    ("٠٠:٠٠:٠٠", "00:00:00"),  # Arabic-Indic digits: `\d` matches
    ("8:30:00", "08:30:00"),  # unpadded hour, non-midnight
    ("7:30", "07:30:00"),
]

_UNRESCUABLE_TIMES = [
    "not a time",
    "",  # empty string is not the unset sentinel
    "25:00:00",  # shape-valid, impossible clock time
    "24:99:99",  # shape-valid, impossible clock time
]

_GOOD_TIMES = ["00:00:00", "07:00:00", "22:30:00", "23:59:59"]


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
    async def test_max_slat_angle_dead_zone_recorded_as_error(self, tmp_path):
        """Issue #1105: import_config rejects the same dead zone set_option does.

        ``max_slat_angle`` is neither the "0 = use tilt mode" sentinel nor a
        usable physical angle in the open interval (0, 1) — a plain
        ``OPTION_RANGES`` lo/hi check (0, 180) would accept it, since it is
        within bounds. This closes the gap: the two config boundaries
        (``set_option`` via ``FIELD_VALIDATORS`` and ``import_config``) must
        agree, both rejecting it.
        """
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Roof A", {"max_slat_angle": 0})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"max_slat_angle": 0.5}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"].startswith("error:")
        assert "max_slat_angle" in result["id-1"]
        # entry options must not have been modified
        assert entry.options["max_slat_angle"] == 0

    @pytest.mark.asyncio
    async def test_max_slat_angle_sentinel_and_usable_values_import_cleanly(
        self, tmp_path
    ):
        """Issue #1105: the ``0`` sentinel and a usable angle still import fine."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Roof A", {"max_slat_angle": 160})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"max_slat_angle": 0}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"] == "updated"
        assert entry.options["max_slat_angle"] == 0

    @pytest.mark.parametrize("time_key", ["start_time", "end_time"])
    @pytest.mark.parametrize(("raw", "expected"), _RESCUABLE_TIMES)
    @pytest.mark.asyncio
    async def test_rescuable_time_is_canonicalized(
        self, tmp_path, time_key, raw, expected
    ):
        """A parsable non-canonical time is stored canonically, not rejected.

        Import used to skip every key absent from OPTION_RANGES, so a stray
        ``"00:00"`` reached the config entry and defeated the literal
        ``BLANK_TIME`` comparisons the rest of the integration relies on
        (issue #1049). Erroring instead would fail the whole entry, which is
        worse for the one population guaranteed to have such a file: users
        restoring a backup taken before the validation existed.
        """
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {time_key: "22:00:00"})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {time_key: raw}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"] == "updated"
        assert entry.options[time_key] == expected

    @pytest.mark.asyncio
    async def test_rescuable_time_does_not_block_the_rest_of_the_entry(self, tmp_path):
        """The whole point of canonicalising: every other option still restores."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {"azimuth": 90})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [
                {
                    "entry_id": "id-1",
                    "options": {"end_time": "00:00", "azimuth": 270, "fov_left": 45},
                }
            ],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        assert (await async_handle_import_config(call))["id-1"] == "updated"
        assert entry.options["azimuth"] == 270
        assert entry.options["fov_left"] == 45
        assert entry.options["end_time"] == "00:00:00"

    @pytest.mark.parametrize("time_key", ["start_time", "end_time"])
    @pytest.mark.parametrize("bad_time", _UNRESCUABLE_TIMES)
    @pytest.mark.asyncio
    async def test_unrescuable_time_recorded_as_error(
        self, tmp_path, time_key, bad_time
    ):
        """A value no parser can rescue errors and leaves the entry alone.

        Nothing can be inferred from ``"25:00:00"``, and guessing would store a
        time the user never chose — so the import reports it and the caller
        fixes the file.
        """
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {time_key: "22:00:00"})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {time_key: bad_time}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"].startswith("error:")
        assert time_key in result["id-1"]
        assert entry.options[time_key] == "22:00:00"

    @pytest.mark.parametrize("time_key", ["start_time", "end_time"])
    @pytest.mark.parametrize("good_time", [*_GOOD_TIMES, None])
    @pytest.mark.asyncio
    async def test_well_formed_time_imports(self, tmp_path, time_key, good_time):
        """HH:MM:SS values — including the BLANK_TIME sentinel — and None import cleanly."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {time_key: "07:00:00"})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {time_key: good_time}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert result["id-1"] == "updated"
        assert entry.options[time_key] == good_time

    @pytest.mark.asyncio
    async def test_numeric_and_time_errors_reported_together(self, tmp_path):
        """One entry carrying both a bad number and a bad time reports both keys."""
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )

        entry = _make_entry("id-1", "Blind A", {"set_azimuth": 90})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [
                {
                    "entry_id": "id-1",
                    "options": {"set_azimuth": 999, "end_time": "25:00:00"},
                }
            ],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        result = await async_handle_import_config(call)

        assert "set_azimuth" in result["id-1"]
        assert "end_time" in result["id-1"]
        assert entry.options["set_azimuth"] == 90

    @pytest.mark.parametrize(
        "bad_time", [raw for raw, _ in _RESCUABLE_TIMES] + _UNRESCUABLE_TIMES
    )
    def test_set_options_rejects_every_non_canonical_time(self, bad_time):
        """``set_options`` is the strict path: no value outside the format gets in.

        Unlike import it has no file to salvage — the caller wrote this patch by
        hand and can fix it, so a hard error is the useful answer (issue #1049).
        """
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.options_service import (
            validate_options_patch,
        )

        with pytest.raises(ServiceValidationError):
            validate_options_patch({"end_time": bad_time}, {})

    @pytest.mark.parametrize(("raw", "expected"), _RESCUABLE_TIMES)
    @pytest.mark.asyncio
    async def test_import_canonicalizes_what_set_options_rejects(
        self, tmp_path, raw, expected
    ):
        """The two service paths diverge on rescuable values, by design.

        Both end at the same stored format — ``const.TIME_STRING_RE`` — but
        import gets there by canonicalising a file it cannot ask the user to
        edit, while ``set_options`` refuses. Pinning the divergence here keeps
        it a decision rather than a drift.
        """
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )
        from custom_components.adaptive_cover_pro.services.options_service import (
            validate_options_patch,
        )

        with pytest.raises(ServiceValidationError):
            validate_options_patch({"end_time": raw}, {})

        entry = _make_entry("id-1", "Blind A", {})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"end_time": raw}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        assert (await async_handle_import_config(call))["id-1"] == "updated"
        assert entry.options["end_time"] == expected

    @pytest.mark.parametrize("good_time", _GOOD_TIMES)
    @pytest.mark.asyncio
    async def test_time_acceptance_matches_set_options(self, tmp_path, good_time):
        """The accept side of the same parity — neither path is stricter (issue #1049).

        Rejection parity alone would stay green if both paths went on accepting
        a value the config flow can never emit; this is the half that catches
        that.
        """
        from custom_components.adaptive_cover_pro.services.import_service import (
            async_handle_import_config,
        )
        from custom_components.adaptive_cover_pro.services.options_service import (
            validate_options_patch,
        )

        validate_options_patch({"end_time": good_time}, {})

        entry = _make_entry("id-1", "Blind A", {})
        hass = _make_hass([entry], config_dir=str(tmp_path))

        export_path = tmp_path / "import.json"
        self._write_export(
            export_path,
            [{"entry_id": "id-1", "options": {"end_time": good_time}}],
        )

        call = MagicMock()
        call.hass = hass
        call.data = {"filename": str(export_path)}

        assert (await async_handle_import_config(call))["id-1"] == "updated"

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
