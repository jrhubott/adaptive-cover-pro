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

from ..const import (
    CONF_MAX_SLAT_ANGLE,
    DOMAIN,
    OPTION_RANGES,
    TIME_OPTION_KEYS,
    TIME_STRING_RE,
)
from ..helpers import normalize_time_string
from .export_service import DEFAULT_EXPORT_PATH
from .options_service import FIELD_VALIDATORS

_LOGGER = logging.getLogger(__name__)

IMPORT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("filename", default=DEFAULT_EXPORT_PATH): str,
    }
)


def _out_of_range_error(key: str, value: object) -> str:
    """Format the shared "out of range" message for an ``OPTION_RANGES`` key.

    Both the ``CONF_MAX_SLAT_ANGLE`` bespoke-validator branch and the generic
    ``OPTION_RANGES`` branch below need this exact wording — extracted so the
    two can never drift apart (issue #1105).
    """
    lo, hi = OPTION_RANGES[key]
    return f"{key}={value} out of range [{lo}, {hi}]"


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
    rest of the import. ``CONF_MAX_SLAT_ANGLE`` is special-cased to route
    through ``FIELD_VALIDATORS`` instead (issue #1105) so its sub-degree dead
    zone is rejected the same way here as in ``set_option``.

    Time keys (``TIME_OPTION_KEYS``) are **canonicalised, not rejected**, and
    this deliberately differs from ``set_options``: a value that parses is
    rewritten to the ``const.TIME_STRING_RE`` wire format (``"00:00"`` is
    stored as ``"00:00:00"``) and only a value no parser can rescue records an
    error. An export file predates this validation, so failing the entry would
    drop every *other* option a user is restoring — while a malformed time left
    verbatim defeats the literal ``BLANK_TIME`` comparisons across the
    integration (issue #1049). Remaining keys (booleans, strings, enums, and
    unknown future keys) are accepted as-is.

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

            # Validate numeric keys against their declared OPTION_RANGES bounds
            # and time keys against the one accepted HH:MM:SS wire format.
            validation_errors: list[str] = []
            for key, value in list(imported_public.items()):
                if value is None:
                    continue
                if key == CONF_MAX_SLAT_ANGLE:
                    # Bespoke validator (#1105): the plain OPTION_RANGES lo/hi
                    # check below also admits the sub-degree dead zone between
                    # the "0 = use tilt mode" sentinel and the smallest usable
                    # physical angle that ``services.set_option`` now rejects
                    # via ``FIELD_VALIDATORS``. Routed through the SAME
                    # validator here so both config boundaries agree. This is
                    # deliberately special-cased to this one key rather than
                    # routing every OPTION_RANGES key through FIELD_VALIDATORS
                    # generically — several other keys (e.g.
                    # CONF_OUTSIDE_THRESHOLD) also accept a Jinja2 template via
                    # a *different* bespoke validator, and swapping those over
                    # would flip values import_config currently rejects
                    # outright to silently passing.
                    try:
                        FIELD_VALIDATORS[CONF_MAX_SLAT_ANGLE](value)
                    except vol.MultipleInvalid:
                        # Outside the dead zone, ``_max_slat_angle_v`` falls
                        # through to ``_num()``'s ``vol.Any(None, ...)``
                        # composition, and voluptuous's ``Any`` keeps the
                        # "None"-literal alternative's generic fallback
                        # message ("not a valid value") instead of the real
                        # ``vol.Range`` one — that's what distinguishes this
                        # from the branch below (``vol.Any`` failing raises
                        # ``MultipleInvalid``, a subclass of ``vol.Invalid``).
                        # Every sibling OPTION_RANGES key on this loop still
                        # names its bounds on an out-of-range value, so
                        # reconstruct the same wording here rather than
                        # surfacing the uninformative fallback to the user.
                        validation_errors.append(_out_of_range_error(key, value))
                    except vol.Invalid as exc:
                        # The dead-zone branch (naming the ``0`` sentinel) and
                        # a bad type (via ``vol.Coerce``) both raise directly
                        # with an already-informative message — keep it as-is.
                        validation_errors.append(f"{key}={value!r}: {exc.msg}")
                elif key in OPTION_RANGES:
                    lo, hi = OPTION_RANGES[key]
                    try:
                        num = float(value)
                        if not (lo <= num <= hi):
                            validation_errors.append(_out_of_range_error(key, value))
                    except (TypeError, ValueError):
                        validation_errors.append(
                            f"{key}={value!r} is not a valid number"
                        )
                if key in TIME_OPTION_KEYS:
                    # Canonicalise what parses rather than failing the entry.
                    # An export predates this validation, so the users most
                    # likely to hold a "00:00" are exactly the ones #1049 bit —
                    # rejecting would drop every *other* option they are trying
                    # to restore. ``set_options`` still refuses the same value:
                    # it has a caller who can fix the patch, an import file has
                    # no one to ask.
                    canonical = normalize_time_string(value)
                    if TIME_STRING_RE.match(str(canonical)):
                        if canonical != value:
                            _LOGGER.debug(
                                "import_config: entry '%s' %s=%r stored as %r",
                                entry_id,
                                key,
                                value,
                                canonical,
                            )
                        imported_public[key] = canonical
                    else:
                        validation_errors.append(
                            f"{key}={value!r} is not a valid time (expected HH:MM:SS)"
                        )
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
