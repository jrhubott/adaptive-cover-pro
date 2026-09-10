"""Registry of manual-override detection strategies.

Adding a new detection pattern is a drop-in: create one new detector module
and add one line to ``DETECTOR_REGISTRY`` keyed by the detector's
``strategy_id`` class attribute. Selection is data-driven (the engine reads
``CONF_MANUAL_OVERRIDE_STRATEGY``), mirroring ``cover_types.get_policy``.

``CONF_MANUAL_OVERRIDE_STRATEGY`` has no config-flow selector, service field,
or translation; the strategy is selectable only by hand-editing the entry's
options. The seam is kept because ``PositionDeltaDetector`` is tested in
isolation through it and a future pattern drops in without coordinator
changes — it is internal-only and must not be documented as a user option.
"""

from __future__ import annotations

from .detector import DetectorConfig, OverrideDetector
from .position_delta import PositionDeltaDetector
from .time_window import TimeWindowDetector

DETECTOR_REGISTRY: dict[str, type[OverrideDetector]] = {
    PositionDeltaDetector.strategy_id: PositionDeltaDetector,
    TimeWindowDetector.strategy_id: TimeWindowDetector,
}

DEFAULT_DETECTOR: type[OverrideDetector] = PositionDeltaDetector


def get_detector(strategy_id: str | None, config: DetectorConfig) -> OverrideDetector:
    """Return a detector instance for ``strategy_id`` built from ``config``.

    Unknown or missing ids fall back to the default (position-delta) strategy,
    preserving behaviour for entries configured before a strategy option exists.
    """
    cls = DETECTOR_REGISTRY.get(strategy_id) if strategy_id else None
    return (cls or DEFAULT_DETECTOR).from_config(config)
