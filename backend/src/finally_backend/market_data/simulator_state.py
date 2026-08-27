"""Per-ticker simulator state — the mutable GBM parameters tracked for each
ticker the simulator is currently pricing. See planning/MARKET_SIMULATOR.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TickerState:
    ticker: str
    price: float
    drift: float  # annualized mu, e.g. 0.08 for 8%/yr
    volatility: float  # annualized sigma, e.g. 0.25 for 25%/yr
    sector: str  # correlation grouping — see planning/MARKET_SIMULATOR.md §5
