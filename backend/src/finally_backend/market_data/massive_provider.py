"""MassiveProvider — real market data via the Massive (formerly Polygon.io)
REST snapshot endpoint. See planning/MASSIVE_API.md and
planning/MARKET_INTERFACE.md §5.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .base import MarketDataProvider
from .cache import PriceCache

logger = logging.getLogger(__name__)

SNAPSHOT_URL = "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers"
DEFAULT_POLL_INTERVAL_SECONDS = 15.0


class MassiveProvider(MarketDataProvider):
    def __init__(
        self,
        cache: PriceCache,
        api_key: str,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        client: httpx.AsyncClient | None = None,
    ):
        self._cache = cache
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def start(self, tickers: set[str]) -> None:
        self._tickers = set(tickers)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_client:
            await self._client.aclose()

    async def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)  # picked up on the next poll cycle

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Massive API rate limited; will retry next cycle")
                else:
                    logger.warning("Massive API error %s; keeping last known prices", exc)
            except httpx.HTTPError as exc:
                logger.warning("Massive API network error: %s; keeping last known prices", exc)
            except Exception:
                # Anything else - a malformed/unexpected response body (bad
                # JSON, a row missing an expected field, an unexpected shape)
                # - gets the same fail-soft treatment as the HTTP-level
                # errors above: skip this cycle, keep the last known prices,
                # try again next cycle. Without this, a single odd response
                # would kill the polling task permanently and silently.
                # asyncio.CancelledError is a BaseException (not Exception in
                # Python 3.8+), so a real cancellation from stop() always
                # propagates through this untouched.
                logger.exception("Massive API poll cycle failed unexpectedly; keeping last known prices")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        if not self._tickers:
            return
        params = {"tickers": ",".join(sorted(self._tickers)), "include_otc": "false"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = await self._client.get(SNAPSHOT_URL, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        now = datetime.now(timezone.utc)
        for row in body.get("tickers", []):
            ticker = row.get("ticker")
            if ticker is None or ticker not in self._tickers:
                # Missing "ticker" key entirely, or removed from the
                # watchlist while this poll was in flight (remove_ticker()
                # already cleared the cache for it - don't resurrect it with
                # this now-stale response).
                continue
            price = row.get("day", {}).get("c")
            if price is None:
                continue  # no trading activity yet today; keep last known price
            await self._cache.update(ticker, price, now)
        # Any ticker in self._tickers that Massive didn't return a row for
        # (e.g. a fictitious/unlisted symbol - planning/MASSIVE_API.md §8) is
        # simply left untouched in the cache this cycle.
