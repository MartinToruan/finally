# Market Data Interface — Design

*Designs the unified Python interface behind `planning/PLAN.md` §6: one abstract interface, two implementations (Massive-backed and simulator-backed), selected by whether `MASSIVE_API_KEY` is set. Builds on the endpoint research in `MASSIVE_API.md`; the simulator implementation itself is detailed in `MARKET_SIMULATOR.md`.*

## 1. Goals

- **One interface, two backends.** Every downstream consumer (the SSE endpoint, the trade-execution code that needs a current price, the LLM's portfolio-context loader) talks to the same small interface and never branches on which backend is live.
- **Backend-agnostic price cache.** Per `PLAN.md` §6, a single background task writes into a shared in-memory cache; SSE reads from the cache, not from the provider directly. This decouples "how fast can we get new prices" (provider-specific — 500ms for the simulator, 15s for Massive free tier) from "how fast do we push to the browser" (fixed ~500ms cadence either way, per `PLAN.md` §6).
- **Dynamic ticker set.** The watchlist changes at runtime (manual add/remove, LLM-issued `watchlist_changes`). Both providers must be able to pick up a new ticker or drop one without a restart.
- **Fail soft.** A Massive API hiccup (rate limit, network blip) degrades to "serve the last known price" — it never take down the SSE stream or the app.

## 2. Module Layout

Proposed location within `backend/` (final package name is the Backend Engineer's call — this is illustrative):

```
backend/src/finally_backend/market_data/
├── __init__.py          # exports create_provider(), PriceCache, PriceUpdate
├── base.py               # MarketDataProvider ABC, PriceUpdate dataclass
├── cache.py               # PriceCache
├── simulator.py           # SimulatorProvider (see MARKET_SIMULATOR.md)
├── massive_provider.py    # MassiveProvider
└── factory.py             # create_provider(): reads MASSIVE_API_KEY, picks impl
```

## 3. Core Types

```python
# base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One ticker's current price state, as pushed to the cache and to SSE clients."""
    ticker: str
    price: float
    previous_price: float
    timestamp: datetime  # UTC

    @property
    def change(self) -> float:
        return self.price - self.previous_price

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return (self.change / self.previous_price) * 100

    @property
    def direction(self) -> Direction:
        if self.price > self.previous_price:
            return Direction.UP
        if self.price < self.previous_price:
            return Direction.DOWN
        return Direction.FLAT


class MarketDataProvider(ABC):
    """Abstract interface implemented by both SimulatorProvider and MassiveProvider.
    Consumers (SSE endpoint, trade execution, portfolio context) depend only on this.
    """

    @abstractmethod
    async def start(self, tickers: set[str]) -> None:
        """Begin the background update loop for the given initial ticker set."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background loop cleanly (used on app shutdown)."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start pricing a new ticker (e.g. added to the watchlist). Idempotent."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop pricing a ticker (e.g. removed from the watchlist). Idempotent."""
```

`PriceUpdate` is deliberately provider-agnostic — it's the same shape whether it came from a GBM step or a Massive snapshot row, so the SSE serializer (`PLAN.md` §6: "ticker, price, previous price, timestamp, and change direction") has exactly one code path.

## 4. The Shared Price Cache

The cache is *not* part of the provider — it's a separate object both providers write into, and everything else (SSE, trade execution, chat context) reads from. This matches `PLAN.md` §6's "Shared Price Cache" architecture note and is what makes the interface swap invisible to the rest of the app.

```python
# cache.py
import asyncio
from .base import PriceUpdate


class PriceCache:
    """In-memory latest-price cache. One instance, shared across the app via
    FastAPI's lifespan/dependency-injection, not per-request."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = asyncio.Lock()  # guards concurrent writes from provider + reads

    async def update(self, ticker: str, price: float, timestamp) -> PriceUpdate:
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
```

A single `asyncio.Lock` is sufficient — everything runs on one event loop (no multiprocessing), so this is about ordering writes against concurrent reads within `await` points, not true thread safety. `update()` computes `previous_price` from **the cache's own last value**, not from any "yesterday's close" field a provider might supply — this keeps flash-on-change behavior (`PLAN.md` — prices flash green/red on *each tick*, not vs. yesterday) identical for both backends, even though Massive's snapshot payload also happens to carry a `prevDay` close that means something different.

## 5. `MassiveProvider`

Wraps the REST polling behavior from `MASSIVE_API.md` §5.1 and §6.

```python
# massive_provider.py
import asyncio
import logging
import httpx
from datetime import datetime, timezone
from .base import MarketDataProvider
from .cache import PriceCache

logger = logging.getLogger(__name__)

SNAPSHOT_URL = "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers"


class MassiveProvider(MarketDataProvider):
    def __init__(self, cache: PriceCache, api_key: str, poll_interval_seconds: float = 15.0):
        self._cache = cache
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=10.0)

    async def start(self, tickers: set[str]) -> None:
        self._tickers = set(tickers)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._client.aclose()

    async def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)  # picked up on the next poll cycle

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)

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
            price = row.get("day", {}).get("c")
            if price is None:
                continue  # no trading activity yet today; keep last known price
            await self._cache.update(row["ticker"], price, now)
        # Any ticker in self._tickers that Massive didn't return a row for
        # (see §7 below) is simply left untouched in the cache this cycle.
```

Key design points, each tied back to `MASSIVE_API.md`:

- **One HTTP call per poll cycle covers the whole watchlist** (§5.1) — the provider never makes one call per ticker, which is what makes the free tier's 5 calls/min workable.
- **Poll interval defaults to 15s**, matching the free-tier budget from `MASSIVE_API.md` §4; if `MASSIVE_API_KEY` is paired with a known paid tier in the future, `poll_interval_seconds` is the one knob to change (could become an env var, e.g. `MASSIVE_POLL_INTERVAL_SECONDS`, if that becomes necessary — not required for the initial build).
- **Errors never propagate out of `_poll_loop`** — a bad cycle logs and waits for the next one, per `MASSIVE_API.md` §6's "fail soft" guidance. The cache simply isn't updated that cycle, so the SSE stream keeps serving the last known price.
- **`add_ticker`/`remove_ticker` are cheap, synchronous set mutations** — no need to restart the poll loop or open a new connection, since polling always re-reads `self._tickers` fresh each cycle.

## 6. `SimulatorProvider`

Same interface, driven by a ~500ms GBM update loop instead of a 15s REST poll. Full design — the GBM math, correlation model, event injection, and deterministic seeding for arbitrary tickers — lives in `MARKET_SIMULATOR.md`. From this document's point of view, the only thing that matters is that it satisfies the same `MarketDataProvider` ABC and writes into the same `PriceCache`:

```python
# simulator.py (interface shape only — see MARKET_SIMULATOR.md for the real implementation)
class SimulatorProvider(MarketDataProvider):
    async def start(self, tickers: set[str]) -> None: ...
    async def stop(self) -> None: ...
    async def add_ticker(self, ticker: str) -> None: ...   # seeds a price immediately, see MARKET_SIMULATOR.md §4
    async def remove_ticker(self, ticker: str) -> None: ...
```

## 7. Selection: `create_provider()`

```python
# factory.py
import os
from .base import MarketDataProvider
from .cache import PriceCache
from .simulator import SimulatorProvider
from .massive_provider import MassiveProvider


def create_provider(cache: PriceCache) -> MarketDataProvider:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(cache, api_key=api_key)
    return SimulatorProvider(cache)
```

This is the entire branch point in the whole codebase — called once, at FastAPI startup (app lifespan), with the resulting `MarketDataProvider` stored wherever dependency injection makes it reachable from the SSE route, the trade-execution path (to price a market order at the current cache value), and the chat context loader. No other code checks `MASSIVE_API_KEY` or imports either concrete provider class.

```python
# app startup (illustrative)
cache = PriceCache()
provider = create_provider(cache)
await provider.start(tickers=set(watchlist_tickers_from_db))
# ... store cache and provider on app.state ...
# on shutdown: await provider.stop()
```

## 8. Consuming the Cache: SSE Endpoint

The SSE endpoint (`GET /api/stream/prices`, `PLAN.md` §6) never touches the provider — it only reads `PriceCache`, on its own fixed ~500ms cadence, regardless of which provider is filling it:

```python
async def price_stream(cache: PriceCache):
    while True:
        for update in cache.get_all().values():
            yield format_sse_event(update)  # ticker, price, previous_price, timestamp, direction
        await asyncio.sleep(0.5)
```

This is why the interface split matters: the simulator naturally produces a new value every ~500ms (so SSE's cadence and the provider's cadence happen to match 1:1), while Massive only refreshes the cache every 15s — but SSE still emits every 500ms, just re-serving the same cached value until the next poll lands. The frontend doesn't need to know or care; it just sees a price that's occasionally flat for a few ticks under Massive mode, and updates on (almost) every tick under simulator mode.

## 9. Known Asymmetry: Fictitious Tickers

As documented in `MASSIVE_API.md` §8, `PLAN.md` §6 lets the simulator price *any* well-formed ticker, real or invented, via deterministic hash-based seeding. `MassiveProvider` cannot do this — a fictitious ticker just never appears in the snapshot response, and the cache never gets an entry for it. This is an accepted, intentional gap, not a bug to work around:

- The watchlist and portfolio endpoints don't need special-casing for it — a ticker with no cache entry simply has no live price, and the frontend/positions table should already handle "no price yet" (e.g. right after a ticker is first added, before the first cache write) the same way regardless of cause.
- No validation-time distinction is needed between "will definitely have a real price" and "might not" — format validation (`PLAN.md` §6) is deliberately the only gate, in both modes, so behavior stays consistent for the user experience even though the underlying data availability differs.

## 10. Testing Implications

Per `PLAN.md` §12, both implementations must be tested against the same conformance contract:

- A shared test suite (parametrized over both `SimulatorProvider` and a `MassiveProvider` with `httpx` responses mocked) asserts: `start()` populates the cache, `add_ticker`/`remove_ticker` change what gets updated, `PriceUpdate.direction`/`change_percent` compute correctly, and a failed update cycle doesn't clear existing cache entries.
- `MassiveProvider`-specific tests mock the snapshot HTTP response (using the exact shape from `MASSIVE_API.md` §5.1) to cover: normal update, a ticker missing from the response (§9), a `429`, and a network timeout — each asserting the cache is left in the expected state afterward.
- `LLM_MOCK=true` E2E tests (`PLAN.md` §12) run against `SimulatorProvider` only — there's no reason to hit the real Massive API in CI, and doing so would be flaky against the 5 calls/min budget.
