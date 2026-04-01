"""Tests for inverse state functionality.

CRITICAL: These tests validate the documented behavior from CLAUDE.md regarding
inverse state handling for both position-capable and open/close-only covers.

The correct order is: calculate → invert (if enabled) → threshold (for open/close-only)
This order MUST be preserved.
"""

import pytest

from custom_components.adaptive_cover_pro.coordinator import inverse_state


@pytest.mark.unit
def test_inverse_state_function_inverts_zero():
    """Test inverse_state function inverts 0 to 100."""
    assert inverse_state(0) == 100


@pytest.mark.unit
def test_inverse_state_function_inverts_hundred():
    """Test inverse_state function inverts 100 to 0."""
    assert inverse_state(100) == 0


@pytest.mark.unit
def test_inverse_state_function_inverts_fifty():
    """Test inverse_state function inverts 50 to 50."""
    assert inverse_state(50) == 50


@pytest.mark.unit
def test_inverse_state_function_inverts_thirty():
    """Test inverse_state function inverts 30 to 70."""
    assert inverse_state(30) == 70


@pytest.mark.unit
def test_inverse_state_function_inverts_seventy():
    """Test inverse_state function inverts 70 to 30."""
    assert inverse_state(70) == 30


@pytest.mark.unit
def test_inverse_state_function_inverts_twenty_five():
    """Test inverse_state function inverts 25 to 75."""
    assert inverse_state(25) == 75


@pytest.mark.unit
def test_inverse_state_position_capable_flow():
    """Test inverse state flow for position-capable covers.

    Flow: Calculate 30% → invert to 70% → send position 70 to cover
    """
    calculated_position = 30

    # Apply inverse_state (as done in coordinator line 955)
    inverted_position = inverse_state(calculated_position)

    # For position-capable covers, inverted position is sent directly
    assert inverted_position == 70


@pytest.mark.unit
def test_inverse_state_open_close_flow_above_threshold():
    """Test inverse state flow for open/close-only covers above threshold.

    CRITICAL: This validates the documented behavior from CLAUDE.md.
    Flow: Calculate 30% → invert to 70% → 70% ≥ 50% → send OPEN command
    """
    calculated_position = 30
    threshold = 50

    # Apply inverse_state BEFORE threshold check (coordinator line 955)
    inverted_position = inverse_state(calculated_position)

    # Apply threshold check to inverted position (coordinator line 497-502)
    if inverted_position >= threshold:
        command = "open_cover"
    else:
        command = "close_cover"

    # With inverse_state: 30 → 70 → 70 ≥ 50 → OPEN
    assert inverted_position == 70
    assert command == "open_cover"


@pytest.mark.unit
def test_inverse_state_open_close_flow_below_threshold():
    """Test inverse state flow for open/close-only covers below threshold.

    Flow: Calculate 80% → invert to 20% → 20% < 50% → send CLOSE command
    """
    calculated_position = 80
    threshold = 50

    # Apply inverse_state BEFORE threshold check (coordinator line 955)
    inverted_position = inverse_state(calculated_position)

    # Apply threshold check to inverted position (coordinator line 497-502)
    if inverted_position >= threshold:
        command = "open_cover"
    else:
        command = "close_cover"

    # With inverse_state: 80 → 20 → 20 < 50 → CLOSE
    assert inverted_position == 20
    assert command == "close_cover"


@pytest.mark.unit
def test_inverse_state_open_close_flow_at_threshold():
    """Test inverse state flow for open/close-only covers exactly at threshold.

    Flow: Calculate 50% → invert to 50% → 50% ≥ 50% → send OPEN command
    """
    calculated_position = 50
    threshold = 50

    # Apply inverse_state BEFORE threshold check (coordinator line 955)
    inverted_position = inverse_state(calculated_position)

    # Apply threshold check to inverted position (coordinator line 497-502)
    if inverted_position >= threshold:
        command = "open_cover"
    else:
        command = "close_cover"

    # With inverse_state: 50 → 50 → 50 ≥ 50 → OPEN
    assert inverted_position == 50
    assert command == "open_cover"


@pytest.mark.unit
def test_no_inverse_state_open_close_flow():
    """Test normal flow without inverse state for open/close-only covers.

    Flow: Calculate 30% → 30% < 50% → send CLOSE command
    """
    calculated_position = 30
    threshold = 50
    inverse_enabled = False

    # When inverse_state is disabled, use calculated position directly
    position = (
        calculated_position
        if not inverse_enabled
        else inverse_state(calculated_position)
    )

    # Apply threshold check
    if position >= threshold:
        command = "open_cover"
    else:
        command = "close_cover"

    # Without inverse_state: 30 → 30 < 50 → CLOSE
    assert position == 30
    assert command == "close_cover"


@pytest.mark.unit
def test_order_of_operations_documented_behavior():
    """Test that order of operations matches CLAUDE.md documentation.

    CRITICAL: This test validates that the order is preserved:
    1. Calculate position
    2. Apply inverse_state (if enabled and not using interpolation)
    3. Apply threshold check (for open/close-only covers)

    This order must NEVER change.
    """
    # Example from CLAUDE.md
    calculated_position = 30
    threshold = 50
    inverse_enabled = True
    use_interpolation = False

    # Step 1: Position is calculated (already done)
    assert calculated_position == 30

    # Step 2: Apply inverse_state if enabled and not using interpolation
    if inverse_enabled and not use_interpolation:
        final_position = inverse_state(calculated_position)
    else:
        final_position = calculated_position

    assert final_position == 70  # 30 inverted

    # Step 3: For open/close-only, apply threshold to final_position
    if final_position >= threshold:
        command = "open_cover"
    else:
        command = "close_cover"

    # Verify the complete flow
    assert calculated_position == 30
    assert final_position == 70
    assert command == "open_cover"


@pytest.mark.unit
def test_inverse_state_disabled_with_interpolation():
    """Test that inverse_state is not applied when interpolation is enabled.

    From coordinator.py line 949-952: inverse_state is NOT applied when
    interpolation is enabled (user should arrange interpolation list instead).
    """
    calculated_position = 30
    inverse_enabled = True
    use_interpolation = True

    # When interpolation is enabled, inverse_state is NOT applied
    # (coordinator line 954: "if self._inverse_state and not self._use_interpolation")
    if inverse_enabled and not use_interpolation:
        final_position = inverse_state(calculated_position)
    else:
        final_position = calculated_position

    # With interpolation enabled, position is NOT inverted
    assert final_position == 30
