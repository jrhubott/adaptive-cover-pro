"""Import service for Adaptive Cover Pro — applies a JSON config file to live entries."""

from __future__ import annotations

import json
import logging
import pathlib
from collections import Counter
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

from ..const import DOMAIN, OPTION_RANGES
from .export_service import DEFAULT_EXPORT_PATH

_LOGGER = logging.getLogger(__name__)

IMPORT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("filename", default=DEFAULT_EXPORT_PATH): str,
    }
)


async def async_handle_import_config(call: ServiceCall) -> dict:
    """Handle the import_config service call.

    Reads a JSON file previously written by ``export_all_config`` and applies the
    options of each entry (matched by ``entry_id``) to the live config entries.

    Internal ``_``-prefixed keys (migration markers such as ``_orphan_prune_v1``)
    are preserved from the current live entry so that version-migration state is
    never overwritten by an older export.

    Numeric keys present in ``OPTION_RANGES`` are validated against their
    declared bounds before the entry is updated; a failed check records
    ``"error: ..."`` for that entry in the result dict without aborting the
    rest of the import. Keys absent from ``OPTION_RANGES`` (booleans, strings,
    enums, and unknown future keys) are accepted as-is.

    Returns a per-entry result dict:
        ``{entry_id: "updated" | "skipped" | "error: <msg>"}``

    Raises:
        ServiceValidationError: if the filename resolves outside the HA config
            directory, if the file cannot be read, if the file is not valid JSON,
            or if the file is not a valid ACP export shape.

    """
    hass: HomeAssistant = call.hass
    filename: str = call.data["filename"]

    # Resolve relative filenames against the HA config directory.
    p = pathlib.Path(filename)
    if not p.is_absolute():
        p = pathlib.Path(hass.config.config_dir) / p

    # Path safety: reject traversal outside the HA config directory.
    config_root = pathlib.Path(hass.config.config_dir).resolve()
    path = p.resolve()
    try:
        path.relative_to(config_root)
    except ValueError as exc:
        raise ServiceValidationError(
            f"import_config: filename '{filename}' is not inside the HA config "
            f"directory ('{config_root}') — only files under the config directory "
            "may be imported"
        ) from exc

    # Read file in executor
    def _read() -> str:
        return path.read_text(encoding="utf-8")

    try:
        raw = await hass.async_add_executor_job(_read)
    except OSError as exc:
        raise ServiceValidationError(
            f"import_config: cannot read '{path}': {exc}"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServiceValidationError(
            f"import_config: '{path}' is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ServiceValidationError(
            f"import_config: '{path}' is not a valid ACP export "
            '(expected {{"export_version": 1, "entries": [...]}})'
        )

    file_entries: list[dict] = data["entries"]
    results: dict[str, str] = {}

    for item in file_entries:
        entry_id: str = item.get("entry_id", "")
        file_opts: dict = item.get("options", {})

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            _LOGGER.warning(
                "import_config: entry_id '%s' not found — skipping", entry_id
            )
            results[entry_id] = "skipped"
            continue

        try:
            # Preserve current internal (_-prefixed) migration markers
            current_internal = {
                k: v for k, v in entry.options.items() if k.startswith("_")
            }
            imported_public = {
                k: v for k, v in file_opts.items() if not k.startswith("_")
            }

            # Validate numeric keys against their declared OPTION_RANGES bounds.
            validation_errors: list[str] = []
            for key, value in imported_public.items():
                if key not in OPTION_RANGES or value is None:
                    continue
                lo, hi = OPTION_RANGES[key]
                try:
                    num = float(value)
                    if not (lo <= num <= hi):
                        validation_errors.append(
                            f"{key}={value} out of range [{lo}, {hi}]"
                        )
                except (TypeError, ValueError):
                    validation_errors.append(f"{key}={value!r} is not a valid number")
            if validation_errors:
                raise ServiceValidationError(
                    f"import_config: invalid values for entry '{entry_id}': "
                    + "; ".join(validation_errors)
                )

            new_options = {**current_internal, **imported_public}

            hass.config_entries.async_update_entry(entry, options=new_options)
            _LOGGER.debug(
                "import_config: updated entry '%s' (%s)", entry_id, entry.title
            )
            results[entry_id] = "updated"
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("import_config: error updating entry '%s'", entry_id)
            results[entry_id] = f"error: {exc}"

    _LOGGER.info(
        "import_config: processed %d entries from '%s': %s",
        len(file_entries),
        path,
        dict(Counter(results.values())),
    )
    return results
