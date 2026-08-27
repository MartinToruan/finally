"""Tests for PriceUpdate's derived properties (change, change_percent, direction)."""

from datetime import datetime, timezone

from finally_backend.market_data.base import Direction, PriceUpdate

NOW = datetime.now(timezone.utc)


def test_change_is_price_minus_previous_price():
    update = PriceUpdate("AAPL", 101.5, 100.0, NOW)
    assert update.change == 1.5


def test_change_percent_computed_relative_to_previous_price():
    update = PriceUpdate("AAPL", 110.0, 100.0, NOW)
    assert update.change_percent == 10.0


def test_change_percent_is_zero_when_previous_price_is_zero():
    update = PriceUpdate("AAPL", 5.0, 0.0, NOW)
    assert update.change_percent == 0.0


def test_direction_up():
    assert PriceUpdate("AAPL", 101.0, 100.0, NOW).direction == Direction.UP


def test_direction_down():
    assert PriceUpdate("AAPL", 99.0, 100.0, NOW).direction == Direction.DOWN


def test_direction_flat():
    assert PriceUpdate("AAPL", 100.0, 100.0, NOW).direction == Direction.FLAT


def test_price_update_is_frozen():
    update = PriceUpdate("AAPL", 100.0, 100.0, NOW)
    try:
        update.price = 200.0  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("PriceUpdate should be immutable")
