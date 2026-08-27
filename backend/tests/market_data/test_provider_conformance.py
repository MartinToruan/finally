"""Shared conformance suite run against BOTH concrete providers.

Per planning/MARKET_INTERFACE.md §10: every consumer of MarketDataProvider
should be able to treat SimulatorProvider and MassiveProvider identically, so
this asserts the same behavioral contract holds for both. Provider-specific
behavior (GBM math, HTTP error handling, etc.) lives in test_steps.py /
test_simulator.py / test_massive_provider.py instead.

Each provider exposes a private "force one update cycle" hook used only by
tests, so these assertions don't depend on sleeping past a real tick/poll
interval: SimulatorProvider._tick() and MassiveProvider._poll_once().
"""

from __future__ import annotations

import httpx
import pytest

from finally_backend.market_data.cache import PriceCache
from finally_backend.market_data.massive_provider import MassiveProvider
from finally_backend.market_data.simulator import SimulatorProvider


def _massive_snapshot_handler(request: httpx.Request) -> httpx.Response:
    """Prices whatever tickers were requested, so generic conformance
    assertions (start/add/remove) behave the same as the simulator."""
    tickers_param = request.url.params.get("tickers", "")
    rows = [
        {"ticker": t, "day": {"c": 100.0 + i}, "prevDay": {"c": 99.0 + i}}
        for i, t in enumerate(tickers_param.split(",")) if t
    ]
    return httpx.Response(200, json={"status": "OK", "count": len(rows), "tickers": rows})


class ProviderHarness:
    """Wraps a provider so tests can force a tick without caring which
    concrete provider (and thus which private method) is under test."""

    def __init__(self, name: str, provider, cache: PriceCache):
        self.name = name
        self.provider = provider
        self.cache = cache

    async def force_tick(self) -> None:
        if isinstance(self.provider, SimulatorProvider):
            await self.provider._tick()
        else:
            await self.provider._poll_once()


@pytest.fixture(params=["simulator", "massive"])
async def harness(request: pytest.FixtureRequest):
    cache = PriceCache()
    if request.param == "simulator":
        provider: object = SimulatorProvider(cache, tick_seconds=3600.0)
    else:
        client = httpx.AsyncClient(transport=httpx.MockTransport(_massive_snapshot_handler))
        provider = MassiveProvider(cache, api_key="test-key", client=client, poll_interval_seconds=3600.0)

    h = ProviderHarness(request.param, provider, cache)
    yield h
    await provider.stop()


async def test_start_populates_the_cache(harness: ProviderHarness):
    await harness.provider.start({"AAPL", "GOOGL"})
    if harness.name == "massive":
        await harness.force_tick()  # Massive only writes on a poll cycle, not on start()
    assert harness.cache.get("AAPL") is not None
    assert harness.cache.get("GOOGL") is not None


async def test_add_ticker_causes_it_to_start_receiving_updates(harness: ProviderHarness):
    await harness.provider.start(set())
    await harness.provider.add_ticker("MSFT")
    await harness.force_tick()
    assert harness.cache.get("MSFT") is not None


async def test_remove_ticker_stops_it_from_receiving_future_updates(harness: ProviderHarness):
    await harness.provider.start({"AAPL", "GOOGL"})
    await harness.force_tick()
    await harness.provider.remove_ticker("AAPL")

    await harness.force_tick()

    assert harness.cache.get("AAPL") is None
    assert harness.cache.get("GOOGL") is not None


async def test_add_and_remove_ticker_are_idempotent(harness: ProviderHarness):
    await harness.provider.start(set())
    await harness.provider.add_ticker("AAPL")
    await harness.provider.add_ticker("AAPL")  # should not raise
    await harness.provider.remove_ticker("AAPL")
    await harness.provider.remove_ticker("AAPL")  # should not raise
    await harness.provider.remove_ticker("NEVER_ADDED")  # should not raise
