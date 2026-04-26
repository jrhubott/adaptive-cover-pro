"""Tests for EventBuffer ring buffer."""

from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer


def test_record_and_snapshot():
    buf = EventBuffer(maxlen=10)
    buf.record({"event": "a"})
    buf.record({"event": "b"})
    assert buf.snapshot() == [{"event": "a"}, {"event": "b"}]


def test_snapshot_is_a_copy():
    buf = EventBuffer(maxlen=10)
    buf.record({"event": "a"})
    snap = buf.snapshot()
    snap.clear()
    assert len(buf) == 1


def test_maxlen_evicts_oldest():
    buf = EventBuffer(maxlen=3)
    for i in range(5):
        buf.record({"event": str(i)})
    assert buf.snapshot() == [{"event": "2"}, {"event": "3"}, {"event": "4"}]


def test_len():
    buf = EventBuffer(maxlen=10)
    assert len(buf) == 0
    buf.record({"event": "a"})
    assert len(buf) == 1


def test_maxlen_property():
    buf = EventBuffer(maxlen=42)
    assert buf.maxlen == 42


def test_resize_up_preserves_all_entries():
    buf = EventBuffer(maxlen=3)
    for i in range(3):
        buf.record({"event": str(i)})
    buf.resize(10)
    assert buf.snapshot() == [{"event": "0"}, {"event": "1"}, {"event": "2"}]
    assert buf.maxlen == 10


def test_resize_down_keeps_most_recent():
    buf = EventBuffer(maxlen=5)
    for i in range(5):
        buf.record({"event": str(i)})
    buf.resize(3)
    assert buf.snapshot() == [{"event": "2"}, {"event": "3"}, {"event": "4"}]
    assert buf.maxlen == 3


def test_resize_then_record():
    # fill to [0, 1, 2], resize to 2 → keeps [1, 2], record "new" → evicts 1 → [2, new]
    buf = EventBuffer(maxlen=3)
    for i in range(3):
        buf.record({"event": str(i)})
    buf.resize(2)
    buf.record({"event": "new"})
    assert buf.snapshot() == [{"event": "2"}, {"event": "new"}]


def test_empty_snapshot():
    buf = EventBuffer(maxlen=10)
    assert buf.snapshot() == []


def test_float_maxlen_is_coerced_to_int():
    """HA's NumberSelector(SLIDER) returns floats — must not raise TypeError."""
    buf = EventBuffer(maxlen=50.0)
    assert buf.maxlen == 50


def test_resize_with_float_is_coerced_to_int():
    buf = EventBuffer(maxlen=10)
    buf.resize(20.0)
    assert buf.maxlen == 20
