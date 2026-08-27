"""SimulatorProvider — the default market data backend.

Used whenever MASSIVE_API_KEY is unset (planning/PLAN.md §5). Drives prices
with a ~500ms GBM update loop with correlated moves and occasional event
jumps. See planning/MARKET_SIMULATOR.md for the full design.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timezone

from .base import MarketDataProvider
from .cache import PriceCache
from .simulator_seed import DEFAULT_SEEDS, derive_seed_state
from .simulator_state import TickerState
from .steps import TICK_SECONDS, DT, maybe_trigger_event, step_all

logger = logging.getLogger(__name__)


class SimulatorProvider(MarketDataProvider):
    def __init__(self, cache: PriceCache, tick_seconds: float = TICK_SECONDS):
        self._cache = cache
        self._tick_seconds = tick_seconds
        self._states: dict[str, TickerState] = {}
        self._task: asyncio.Task | None = None

    async def start(self, tickers: set[str]) -> None:
        for ticker in tickers:
            self._states[ticker] = self._seed(ticker)
            await self._cache.update(ticker, self._states[ticker].price, datetime.now(timezone.utc))
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._states:
            self._states[ticker] = self._seed(ticker)
            # Write an immediate cache entry so a newly-added ticker has a
            # price right away, instead of waiting up to one tick.
            await self._cache.update(ticker, self._states[ticker].price, datetime.now(timezone.utc))

    async def remove_ticker(self, ticker: str) -> None:
        self._states.pop(ticker, None)
        self._cache.remove(ticker)

    def _seed(self, ticker: str) -> TickerState:
        # dataclasses.replace() copies rather than reusing the DEFAULT_SEEDS
        # instance directly — TickerState is mutable and _tick() updates
        # state.price in place, so returning the shared instance would let
        # one provider's price walk corrupt the global seed table for every
        # other provider (and test) in the process.
        template = DEFAULT_SEEDS.get(ticker)
        if template is not None:
            return dataclasses.replace(template)
        return derive_seed_state(ticker)

    async def _run_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._tick_seconds)
                await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive, matches "fail soft"
            logger.exception("Simulator update loop crashed unexpectedly")
            raise

    async def _tick(self) -> None:
        step_all(self._states, DT)
        for state in self._states.values():
            maybe_trigger_event(state)
        now = datetime.now(timezone.utc)
        for state in self._states.values():
            await self._cache.update(state.ticker, state.price, now)
