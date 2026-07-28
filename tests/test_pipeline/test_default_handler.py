"""Tests for DefaultHandler — reason payloads (issue #882) + byte-identical prose."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import ControlMethod, ReasonCode
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from tests.test_pipeline.conftest import make_snapshot


class TestDefaultHandlerReasonPayload:
    """DefaultHandler emits stable Reason payloads for every reason it renders."""

    handler = DefaultHandler()

    def test_no_condition_default_position(self) -> None:
        """Non-sunset fallback carries a default.no_condition payload (default label)."""
        snap = make_snapshot(default_position=100, is_sunset_active=False)
        result = self.handler.evaluate(snap)
        assert result.control_method == ControlMethod.DEFAULT
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.DEFAULT_NO_CONDITION
        pos_label = result.reason_payload.params["pos_label"]
        assert pos_label.code == ReasonCode.FRAGMENT_DEFAULT_POSITION
        assert result.reason_payload.params["position"] == result.position
        assert (
            result.reason
            == f"no active condition — default position {result.position}%"
        )

    def test_no_condition_sunset_position(self) -> None:
        """Sunset fallback carries a default.no_condition payload (sunset label)."""
        snap = make_snapshot(default_position=20, is_sunset_active=True)
        result = self.handler.evaluate(snap)
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.DEFAULT_NO_CONDITION
        pos_label = result.reason_payload.params["pos_label"]
        assert pos_label.code == ReasonCode.FRAGMENT_SUNSET_POSITION
        assert (
            result.reason == f"no active condition — sunset position {result.position}%"
        )

    def test_sunset_use_my(self) -> None:
        """The sunset use-My path carries a default.sunset_use_my payload."""
        snap = make_snapshot(
            default_position=20,
            is_sunset_active=True,
            sunset_use_my=True,
            my_position_value=55,
        )
        result = self.handler.evaluate(snap)
        assert result.use_my_position is True
        assert result.reason_payload is not None
        assert result.reason_payload.code == ReasonCode.DEFAULT_SUNSET_USE_MY
        assert result.reason_payload.params["position"] == 55
        assert result.reason == "sunset position — use My position (55%)"

    def test_describe_skip_payload(self) -> None:
        """describe_skip returns a skip.always_matches payload."""
        payload = self.handler.describe_skip(make_snapshot())
        assert payload.code == ReasonCode.SKIP_ALWAYS_MATCHES
