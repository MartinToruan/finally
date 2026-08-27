"""Tests for SimulatorProvider — orchestration, not the GBM math itself
(covered separately in test_steps.py)."""

import asyncio

import pytest

from finally_backend.market_data.cache import PriceCache
from finally_backend.market_data.simulator import SimulatorProvider
from finally_backend.market_data.simulator_seed import DEFAULT_SEEDS


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


@pytest.fixture
def provider(cache: PriceCache) -> SimulatorProvider:
    # A long tick interval so the background loop never fires mid-test;
    # tests that need a tick call provider._tick() directly.
    return SimulatorProvider(cache, tick_seconds=3600.0)


async def test_start_populates_cache_for_default_tickers(provider: SimulatorProvider, cache: PriceCache):
    await provider.start({"AAPL", "GOOGL"})
    try:
        assert cache.get("AAPL") is not None
        assert cache.get("AAPL").price == DEFAULT_SEEDS["AAPL"].price
        assert cache.get("GOOGL") is not None
    finally:
        await provider.stop()


async def test_start_seeds_unknown_tickers_deterministically(provider: SimulatorProvider, cache: PriceCache):
    await provider.start({"ZZZZZ"})
    try:
        from finally_backend.market_data.simulator_seed import derive_seed_price

        assert cache.get("ZZZZZ").price == derive_seed_price("ZZZZZ")
    finally:
        await provider.stop()


async def test_add_ticker_seeds_and_caches_immediately(provider: SimulatorProvider, cache: PriceCache):
    await provider.start(set())
    try:
        assert cache.get("MSFT") is None
        await provider.add_ticker("MSFT")
        assert cache.get("MSFT") is not None
        assert cache.get("MSFT").price == DEFAULT_SEEDS["MSFT"].price
    finally:
        await provider.stop()


async def test_add_ticker_is_idempotent(provider: SimulatorProvider, cache: PriceCache):
    await provider.start({"AAPL"})
    try:
        first_price = cache.get("AAPL").price
        await provider.add_ticker("AAPL")  # already present — should not reseed
        assert cache.get("AAPL").price == first_price
    finally:
        await provider.stop()


async def test_remove_ticker_stops_future_updates_without_touching_others(
    provider: SimulatorProvider, cache: PriceCache
):
    await provider.start({"AAPL", "GOOGL"})
    try:
        await provider.remove_ticker("AAPL")
        assert cache.get("AAPL") is None  # cache entry cleared too

        await provider._tick()  # force one manual update cycle

        assert cache.get("AAPL") is None  # still not priced
        assert cache.get("GOOGL") is not None  # unaffected
    finally:
        await provider.stop()


async def test_remove_ticker_is_idempotent(provider: SimulatorProvider):
    await provider.start(set())
    try:
        await provider.remove_ticker("NOPE")  # should not raise
    finally:
        await provider.stop()


async def test_tick_updates_every_tracked_ticker(provider: SimulatorProvider, cache: PriceCache):
    await provider.start({"AAPL", "GOOGL"})
    try:
        before = {t: u.price for t, u in cache.get_all().items()}
        await provider._tick()
        after = cache.get_all()
        assert set(after) == set(before)
        for ticker, update in after.items():
            # previous_price should now reflect the pre-tick price
            assert update.previous_price == before[ticker]
    finally:
        await provider.stop()


async def test_stop_cancels_the_background_loop(cache: PriceCache):
    fast_provider = SimulatorProvider(cache, tick_seconds=0.01)
    await fast_provider.start({"AAPL"})
    await asyncio.sleep(0.05)  # let a couple of ticks happen
    await fast_provider.stop()
    assert fast_provider._task is None

    price_after_stop = cache.get("AAPL").price
    await asyncio.sleep(0.05)
    assert cache.get("AAPL").price == price_after_stop  # no further updates
