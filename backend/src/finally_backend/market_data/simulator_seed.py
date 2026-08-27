"""Seed prices and parameters for the simulator.

Ten default tickers get curated, realistic seed prices/parameters
(planning/PLAN.md §7's seed list). Any other well-formed ticker (added
manually or by the LLM) gets a deterministic, hash-derived starting price and
plausible-looking drift/volatility — see planning/MARKET_SIMULATOR.md §4.
"""

from __future__ import annotations

import hashlib

from .simulator_state import TickerState

# Realistic seed prices and parameters for the 10 default tickers
# (planning/PLAN.md §7), grouped into sectors for correlated movement
# (planning/MARKET_SIMULATOR.md §5).
DEFAULT_SEEDS: dict[str, TickerState] = {
    "AAPL": TickerState("AAPL", 190.00, drift=0.10, volatility=0.25, sector="tech"),
    "GOOGL": TickerState("GOOGL", 175.00, drift=0.09, volatility=0.27, sector="tech"),
    "MSFT": TickerState("MSFT", 420.00, drift=0.10, volatility=0.22, sector="tech"),
    "AMZN": TickerState("AMZN", 185.00, drift=0.11, volatility=0.30, sector="tech"),
    "TSLA": TickerState("TSLA", 250.00, drift=0.05, volatility=0.55, sector="auto"),
    "NVDA": TickerState("NVDA", 130.00, drift=0.20, volatility=0.45, sector="tech"),
    "META": TickerState("META", 510.00, drift=0.12, volatility=0.32, sector="tech"),
    "JPM": TickerState("JPM", 210.00, drift=0.08, volatility=0.18, sector="finance"),
    "V": TickerState("V", 280.00, drift=0.09, volatility=0.17, sector="finance"),
    "NFLX": TickerState("NFLX", 680.00, drift=0.10, volatility=0.35, sector="media"),
}

SEED_PRICE_MIN = 10.0
SEED_PRICE_MAX = 500.0


def derive_seed_price(ticker: str) -> float:
    """Deterministically map a ticker symbol to a starting price in $10-$500,
    via a stable hash — same ticker always seeds to the same price.

    hashlib.sha256 is used deliberately over random.seed(ticker): Python's
    random module's hash-to-seed behavior for non-integer seeds isn't
    guaranteed stable across versions or PYTHONHASHSEED settings, while
    slicing a sha256 hex digest into int(..., 16) and normalizing is simple,
    dependency-free, and stable forever for a given input string.
    """
    digest = hashlib.sha256(ticker.encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF  # 0.0-1.0
    return round(SEED_PRICE_MIN + fraction * (SEED_PRICE_MAX - SEED_PRICE_MIN), 2)


def derive_seed_state(ticker: str) -> TickerState:
    """Deterministic seed state for a ticker outside DEFAULT_SEEDS."""
    if ticker in DEFAULT_SEEDS:
        return DEFAULT_SEEDS[ticker]
    digest = hashlib.sha256(ticker.encode("utf-8")).hexdigest()
    price = derive_seed_price(ticker)
    # Also derive plausible-looking drift/volatility from the hash, in
    # reasonable ranges, so unlisted tickers aren't all identically volatile.
    drift = 0.04 + (int(digest[8:16], 16) / 0xFFFFFFFF) * 0.12  # 4%-16%/yr
    volatility = 0.15 + (int(digest[16:24], 16) / 0xFFFFFFFF) * 0.35  # 15%-50%/yr
    return TickerState(ticker, price, drift, volatility, sector="other")
