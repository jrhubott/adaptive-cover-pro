"""Cover-group virtual orchestrator entry-type policy (issue #790).

A cover group is not a physical cover: it holds a roster of member covers
(ACP config entries + generic ``cover.*`` entities) and fans out scenes and
bulk operations to them at runtime. It *does* control covers
(``controls_cover = True``) so cover-only filters treat it as an actor, but
it is not geometry-driven — ``is_orchestrator = True`` routes its config
entry to a ``GroupCoordinator`` in ``__init__.async_setup_entry`` instead of
the sun/geometry coordinator, and keeps it out of the cover-type dropdown.

Scene resolution never happens on this policy: the group resolves each scene
through the *member's* own policy (``position_for_scene``), which is what
makes mixed blind/awning/venetian groups work.
"""

from __future__ import annotations

from typing import ClassVar

from ..const import CoverType
from .base import CoverAxis, CoverTypePolicy


class GroupPolicy(CoverTypePolicy, register=True):
    """Virtual orchestrator entry type driving a roster of member covers."""

    cover_type = CoverType.GROUP
    controls_cover: ClassVar[bool] = True
    is_orchestrator: ClassVar[bool] = True
    axes: ClassVar[tuple[CoverAxis, ...]] = ()

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        """Never called — group setup builds a ``GroupCoordinator``, no engine."""
        raise NotImplementedError  # pragma: no cover
