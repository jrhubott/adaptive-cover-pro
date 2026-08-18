"""Command Queue virtual entry-type policy (issue #1189).

A command queue is not a cover and does not control one: it exists so a named
dispatch queue has somewhere to keep its gap, and so the user has a place to see
which covers are on it. Covers join a queue by NAME, never by ``entry_id`` —
which is what lets a name work with no entry behind it at all (at the default
gap) and lets a queue entry be deleted without breaking the covers that
reference it.

Its config entry registers no platforms and builds no coordinator: setup
short-circuits in ``__init__.async_setup_entry`` on ``controls_cover``, then
splits to the queue branch on ``is_command_queue``. The policy exists only so
the registry/menu machinery treats the queue uniformly with real cover types.
"""

from __future__ import annotations

from typing import ClassVar

from ..const import CoverType
from .base import CoverAxis, CoverTypePolicy


class CommandQueuePolicy(CoverTypePolicy, register=True):
    """Virtual entry type owning one named dispatch queue's gap."""

    cover_type = CoverType.COMMAND_QUEUE
    controls_cover: ClassVar[bool] = False
    is_command_queue: ClassVar[bool] = True
    axes: ClassVar[tuple[CoverAxis, ...]] = ()

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        """Never called — queue setup short-circuits before any engine build."""
        raise NotImplementedError  # pragma: no cover
