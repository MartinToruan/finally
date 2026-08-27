"""Tests for MassiveProvider, mocking the Massive REST snapshot endpoint via
httpx.MockTransport (no real network calls). Response shapes follow
planning/MASSIVE_API.md §5.1 and §6."""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from finally_backend.market_data.cache import PriceCache
from finally_backend.market_data.massive_provider import SNAPSHOT_URL, MassiveProvider


def snapshot_row(ticker: str, close: float | None) -> dict:
    return {"ticker": ticker, "day": {"c": close}, "prevDay": {"c": (close or 0) - 1}}


def snapshot_body(rows: list[dict]) -> dict:
    return {"status": "OK", "count": len(rows), "tickers": rows}


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


def make_provider(cache: PriceCache, handler, **kwargs) -> MassiveProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MassiveProvider(cache, api_key="test-key", client=client, **kwargs)


async def test_poll_once_updates_cache_for_every_returned_ticker(cache: PriceCache):
    def handler(request: httpx.Request) -> httpx.Response:
        body = snapshot_body([snapshot_row("AAPL", 190.5), snapshot_row("GOOGL", 175.25)])
        return httpx.Response(200, json=body)

    provider = make_provider(cache, handler)
    provider._tickers = {"AAPL", "GOOGL"}
    try:
        await provider._poll_once()
        assert cache.get("AAPL").price == 190.5
        assert cache.get("GOOGL").price == 175.25
    finally:
        await provider.stop()


async def test_poll_once_sends_bearer_auth_and_sorted_ticker_list(cache: PriceCache):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=snapshot_body([]))

    provider = make_provider(cache, handler)
    provider._tickers = {"MSFT", "AAPL"}
    try:
        await provider._poll_once()
        assert captured["auth"] == "Bearer test-key"
        assert str(SNAPSHOT_URL) in captured["url"]
        assert "tickers=AAPL%2CMSFT" in captured["url"] or "tickers=AAPL,MSFT" in captured["url"]
    finally:
        await provider.stop()


async def test_poll_once_does_not_call_api_when_no_tickers_watched(cache: PriceCache):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=snapshot_body([]))

    provider = make_provider(cache, handler)
    try:
        await provider._poll_once()
        assert calls == []
    finally:
        await provider.stop()


async def test_ticker_missing_from_response_leaves_cache_untouched(cache: PriceCache):
    """A ticker Massive doesn't return a row for (e.g. fictitious symbol,
    planning/MASSIVE_API.md §8) simply isn't updated — no error, no entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot_body([snapshot_row("AAPL", 190.5)]))

    provider = make_provider(cache, handler)
    provider._tickers = {"AAPL", "ZZZZZ"}
    try:
        await provider._poll_once()
        assert cache.get("AAPL") is not None
        assert cache.get("ZZZZZ") is None
    finally:
        await provider.stop()


async def test_ticker_with_no_trading_activity_keeps_last_known_price(cache: PriceCache):
    """day.c == None (no trades yet today) must not overwrite an existing
    cached price with None."""
    await cache.update("AAPL", 189.0, datetime.now(timezone.utc))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot_body([snapshot_row("AAPL", None)]))

    provider = make_provider(cache, handler)
    provider._tickers = {"AAPL"}
    try:
        await provider._poll_once()
        assert cache.get("AAPL").price == 189.0
    finally:
        await provider.stop()


async def test_poll_once_raises_on_429(cache: PriceCache):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": "ERROR", "error": "rate limited"})

    provider = make_provider(cache, handler)
    provider._tickers = {"AAPL"}
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await provider._poll_once()
        assert cache.get("AAPL") is None
    finally:
        await provider.stop()


async def test_poll_once_raises_on_network_error(cache: PriceCache):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = make_provider(cache, handler)
    provider._tickers = {"AAPL"}
    try:
        with pytest.raises(httpx.HTTPError):
            await provider._poll_once()
    finally:
        await provider.stop()


async def test_poll_loop_survives_errors_and_keeps_serving_last_known_price(cache: PriceCache):
    """Fail-soft behavior (planning/MASSIVE_API.md §6): a bad cycle logs and
    waits for the next one rather than crashing the loop or blanking the
    cache."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=snapshot_body([snapshot_row("AAPL", 190.0)]))
        return httpx.Response(500, json={"status": "ERROR", "error": "upstream issue"})

    provider = make_provider(cache, handler, poll_interval_seconds=0.02)
    try:
        await provider.start({"AAPL"})
        await asyncio.sleep(0.1)  # several poll cycles, later ones failing
        assert call_count >= 2
        assert cache.get("AAPL").price == 190.0  # still the last good value
        assert not provider._task.done()  # loop kept running, didn't crash
    finally:
        await provider.stop()


async def test_add_and_remove_ticker_mutate_watched_set(cache: PriceCache):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot_body([]))

    provider = make_provider(cache, handler)
    try:
        await provider.add_ticker("AAPL")
        assert "AAPL" in provider._tickers

        await cache.update("AAPL", 190.0, datetime.now(timezone.utc))
        await provider.remove_ticker("AAPL")
        assert "AAPL" not in provider._tickers
        assert cache.get("AAPL") is None  # cache entry cleared too
    finally:
        await provider.stop()


async def test_stop_does_not_close_an_externally_provided_client(cache: PriceCache):
    """A client passed in by the caller is theirs to manage — the provider
    must not close it out from under them on stop()."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot_body([]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MassiveProvider(cache, api_key="test-key", client=client, poll_interval_seconds=0.01)
    await provider.start({"AAPL"})
    await asyncio.sleep(0.02)
    await provider.stop()
    assert provider._task is None
    assert not client.is_closed
    await client.aclose()


async def test_stop_closes_a_client_the_provider_created_itself(cache: PriceCache, monkeypatch):
    """When no client is supplied, the provider creates and owns one, so
    stop() must close it to avoid leaking connections."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=snapshot_body([]))

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "finally_backend.market_data.massive_provider.httpx.AsyncClient",
        lambda *args, **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )
    provider = MassiveProvider(cache, api_key="test-key", poll_interval_seconds=0.01)
    await provider.start({"AAPL"})
    await asyncio.sleep(0.02)
    await provider.stop()
    assert provider._task is None
    assert provider._client.is_closed
