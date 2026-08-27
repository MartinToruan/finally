"""Core types for the market data layer.

Defines the provider-agnostic `PriceUpdate` shape and the `MarketDataProvider`
abstract interface that both `SimulatorProvider` and `MassiveProvider` implement.
See planning/MARKET_INTERFACE.md for the full design rationale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
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

    Consumers (SSE endpoint, trade execution, portfolio context) depend only on
    this interface and never branch on which concrete backend is live.
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
