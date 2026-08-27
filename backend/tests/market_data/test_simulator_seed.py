"""Tests for deterministic ticker seeding (planning/MARKET_SIMULATOR.md §4)."""

import pytest

from finally_backend.market_data.simulator_seed import (
    DEFAULT_SEEDS,
    SEED_PRICE_MAX,
    SEED_PRICE_MIN,
    derive_seed_price,
    derive_seed_state,
)


def test_derive_seed_price_is_deterministic():
    assert derive_seed_price("ZZZZZ") == derive_seed_price("ZZZZZ")


def test_derive_seed_price_differs_across_tickers():
    # Not a hard guarantee for arbitrary hashes, but true for these symbols
    # and a good sanity check that the hash is actually being used.
    assert derive_seed_price("AAAAA") != derive_seed_price("BBBBB")


@pytest.mark.parametrize("ticker", ["A", "ZZZZZ", "QQQQ", "XYZAB", "NFLX2"])
def test_derive_seed_price_is_within_bounds(ticker: str):
    price = derive_seed_price(ticker)
    assert SEED_PRICE_MIN <= price <= SEED_PRICE_MAX


def test_derive_seed_state_returns_curated_state_for_default_tickers():
    state = derive_seed_state("AAPL")
    assert state == DEFAULT_SEEDS["AAPL"]


def test_derive_seed_state_returns_a_copy_not_the_shared_default_instance():
    """Regression test: derive_seed_state() must never hand back the live
    DEFAULT_SEEDS object for a curated ticker — TickerState is mutable, and
    a caller mutating the shared instance would corrupt global state for
    every other caller in the process (planning/MARKET_DATA_REVIEW.md
    Finding 4)."""
    state = derive_seed_state("AAPL")
    assert state is not DEFAULT_SEEDS["AAPL"]

    original_price = DEFAULT_SEEDS["AAPL"].price
    state.price = 999999.0
    assert DEFAULT_SEEDS["AAPL"].price == original_price


def test_derive_seed_state_derives_state_for_unknown_ticker():
    state = derive_seed_state("ZZZZZ")
    assert state.ticker == "ZZZZZ"
    assert state.sector == "other"
    assert SEED_PRICE_MIN <= state.price <= SEED_PRICE_MAX
    assert 0.04 <= state.drift <= 0.16
    assert 0.15 <= state.volatility <= 0.50


def test_derive_seed_state_is_deterministic():
    first = derive_seed_state("QQQQQ")
    second = derive_seed_state("QQQQQ")
    assert first.price == second.price
    assert first.drift == second.drift
    assert first.volatility == second.volatility


@pytest.mark.parametrize("ticker,expected_price", [
    ("AAPL", 190.00),
    ("GOOGL", 175.00),
    ("MSFT", 420.00),
    ("AMZN", 185.00),
    ("TSLA", 250.00),
    ("NVDA", 130.00),
    ("META", 510.00),
    ("JPM", 210.00),
    ("V", 280.00),
    ("NFLX", 680.00),
])
def test_default_seeds_have_plausible_prices(ticker: str, expected_price: float):
    """Loose sanity bound on the 10 curated defaults — fixed simulator
    constants, not meant to track the live market (MARKET_SIMULATOR.md §8)."""
    assert DEFAULT_SEEDS[ticker].price == expected_price


def test_default_seeds_cover_the_ten_plan_tickers():
    assert set(DEFAULT_SEEDS) == {
        "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
    }


def test_all_default_seeds_have_positive_price_and_volatility():
    for state in DEFAULT_SEEDS.values():
        assert state.price > 0
        assert state.volatility > 0
