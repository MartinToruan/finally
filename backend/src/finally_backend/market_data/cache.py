"""In-memory shared price cache.

Per planning/PLAN.md §6, both market data providers write into a single shared
cache; the SSE stream, trade execution, and chat context loader all read from
this cache rather than talking to a provider directly. This is what makes the
Massive-vs-simulator swap invisible to the rest of the app.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from .base import PriceUpdate


class PriceCache:
    """In-memory latest-price cache.

    One instance is shared across the app (e.g. via FastAPI's lifespan /
    dependency injection), not created per-request.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = asyncio.Lock()  # guards concurrent writes from provider + reads

    async def update(self, ticker: str, price: float, timestamp: datetime) -> PriceUpdate:
        async with self._lock:
            previous = self._prices.get(ticker)
            previous_price = previous.price if previous else price  # first tick: flat
            update = PriceUpdate(ticker, price, previous_price, timestamp)
            self._prices[ticker] = update
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        return dict(self._prices)  # shallow copy; PriceUpdate is frozen

    def remove(self, ticker: str) -> None:
        """Drop a ticker's cached price entirely (used when a ticker is removed)."""
        self._prices.pop(ticker, None)
