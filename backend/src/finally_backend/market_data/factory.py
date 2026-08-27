"""Provider selection — the entire MASSIVE_API_KEY branch point in the app.

See planning/MARKET_INTERFACE.md §7. Called once, at FastAPI startup; no
other code should check MASSIVE_API_KEY or import a concrete provider class.
"""

from __future__ import annotations

import os

from .base import MarketDataProvider
from .cache import PriceCache
from .massive_provider import MassiveProvider
from .simulator import SimulatorProvider


def create_provider(cache: PriceCache) -> MarketDataProvider:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(cache, api_key=api_key)
    return SimulatorProvider(cache)
