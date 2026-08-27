"""Tests for PriceCache."""

from datetime import datetime, timezone

import pytest

from finally_backend.market_data.cache import PriceCache

NOW = datetime.now(timezone.utc)


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


async def test_get_returns_none_for_unknown_ticker(cache: PriceCache):
    assert cache.get("AAPL") is None


async def test_first_update_is_flat(cache: PriceCache):
    """The very first tick for a ticker has no prior price to compare
    against, so previous_price == price (flat), per MARKET_INTERFACE.md §4."""
    update = await cache.update("AAPL", 190.0, NOW)
    assert update.price == 190.0
    assert update.previous_price == 190.0
    assert update.direction.value == "flat"


async def test_second_update_uses_cache_own_last_value_as_previous(cache: PriceCache):
    await cache.update("AAPL", 190.0, NOW)
    second = await cache.update("AAPL", 192.0, NOW)
    assert second.price == 192.0
    assert second.previous_price == 190.0


async def test_get_returns_latest_update(cache: PriceCache):
    await cache.update("AAPL", 190.0, NOW)
    await cache.update("AAPL", 195.0, NOW)
    assert cache.get("AAPL").price == 195.0


async def test_get_all_returns_every_cached_ticker(cache: PriceCache):
    await cache.update("AAPL", 190.0, NOW)
    await cache.update("GOOGL", 175.0, NOW)
    all_prices = cache.get_all()
    assert set(all_prices) == {"AAPL", "GOOGL"}
    assert all_prices["AAPL"].price == 190.0


async def test_get_all_returns_a_copy_not_a_live_view(cache: PriceCache):
    await cache.update("AAPL", 190.0, NOW)
    snapshot = cache.get_all()
    await cache.update("GOOGL", 175.0, NOW)
    assert "GOOGL" not in snapshot


async def test_remove_drops_the_ticker(cache: PriceCache):
    await cache.update("AAPL", 190.0, NOW)
    cache.remove("AAPL")
    assert cache.get("AAPL") is None
    assert "AAPL" not in cache.get_all()


async def test_remove_is_idempotent_for_unknown_ticker(cache: PriceCache):
    cache.remove("NOPE")  # should not raise
