"""Unified market data layer: one interface, two backends (simulator / Massive).

See planning/MARKET_INTERFACE.md, planning/MARKET_SIMULATOR.md, and
planning/MASSIVE_API.md for the full design.
"""

from .base import Direction, MarketDataProvider, PriceUpdate
from .cache import PriceCache
from .factory import create_provider
from .massive_provider import MassiveProvider
from .simulator import SimulatorProvider
from .validation import is_valid_ticker

__all__ = [
    "Direction",
    "MarketDataProvider",
    "PriceUpdate",
    "PriceCache",
    "create_provider",
    "MassiveProvider",
    "SimulatorProvider",
    "is_valid_ticker",
]
