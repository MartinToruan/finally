"""Tests for GBM step math, correlated moves, and random events.

See planning/MARKET_SIMULATOR.md §8 for the testing approach these follow.
"""

import math
import random

import pytest

from finally_backend.market_data.simulator_state import TickerState
from finally_backend.market_data.steps import (
    EVENT_MAGNITUDE_RANGE,
    maybe_trigger_event,
    step_all,
)


def test_gbm_log_return_matches_theoretical_mean_and_variance():
    """Monte Carlo check: the ensemble mean/variance of log(S_T/S_0) over
    many independent paths should converge to the standard GBM formulas
    (mu - sigma^2/2)*T and sigma^2*T. Uses a seeded RNG so the test is
    fully deterministic; tolerances are set generously above the expected
    standard error at this sample size.
    """
    mu = 0.10
    sigma = 0.25
    dt = 1.0 / 252  # one trading day
    n_steps = 100
    n_runs = 2000
    horizon = n_steps * dt

    rng = random.Random(20260827)
    log_returns = []
    for _ in range(n_runs):
        state = TickerState("AAPL", price=100.0, drift=mu, volatility=sigma, sector="tech")
        for _ in range(n_steps):
            step_all({"AAPL": state}, dt, rng=rng)
        log_returns.append(math.log(state.price / 100.0))

    sample_mean = sum(log_returns) / n_runs
    sample_var = sum((r - sample_mean) ** 2 for r in log_returns) / (n_runs - 1)

    theoretical_mean = (mu - 0.5 * sigma**2) * horizon
    theoretical_var = sigma**2 * horizon

    assert sample_mean == pytest.approx(theoretical_mean, abs=0.02)
    assert sample_var == pytest.approx(theoretical_var, abs=0.006)


def test_gbm_price_never_goes_negative():
    """The multiplicative form guarantees positivity for any Z (unlike the
    additive Euler form) — planning/MARKET_SIMULATOR.md §1."""
    rng = random.Random(1)
    state = TickerState("TSLA", price=5.0, drift=0.05, volatility=0.9, sector="auto")
    for _ in range(5000):
        step_all({"TSLA": state}, dt=1.0 / 252, rng=rng)
        assert state.price > 0


def test_correlation_same_sector_moves_in_lockstep_when_idiosyncratic_is_zero():
    """With idiosyncratic weight forced to 0, two tickers in the same sector
    share the exact same combined shock z each tick, so their log-returns
    must have the same sign every tick."""
    rng = random.Random(42)
    tiny_dt = 1e-8  # isolates the diffusion term from the tiny variance-drag bias

    same_sector_mismatches = 0
    n_ticks = 200
    for _ in range(n_ticks):
        states = {
            "A1": TickerState("A1", price=100.0, drift=0.0, volatility=0.25, sector="tech"),
            "A2": TickerState("A2", price=100.0, drift=0.0, volatility=0.40, sector="tech"),
        }
        step_all(
            states, tiny_dt,
            market_weight=0.5, sector_weight=0.5, idiosyncratic_weight=0.0,
            rng=rng,
        )
        sign_a1 = math.copysign(1, states["A1"].price - 100.0)
        sign_a2 = math.copysign(1, states["A2"].price - 100.0)
        if sign_a1 != sign_a2:
            same_sector_mismatches += 1

    assert same_sector_mismatches == 0


def test_correlation_different_sectors_do_not_always_move_in_lockstep():
    """Two tickers in different sectors share only the market shock, not the
    sector shock, so across many ticks they should NOT always agree in
    sign — unlike the same-sector case above."""
    rng = random.Random(7)
    tiny_dt = 1e-8

    cross_sector_mismatches = 0
    n_ticks = 200
    for _ in range(n_ticks):
        states = {
            "A1": TickerState("A1", price=100.0, drift=0.0, volatility=0.25, sector="tech"),
            "B1": TickerState("B1", price=100.0, drift=0.0, volatility=0.25, sector="finance"),
        }
        step_all(
            states, tiny_dt,
            market_weight=0.5, sector_weight=0.5, idiosyncratic_weight=0.0,
            rng=rng,
        )
        sign_a1 = math.copysign(1, states["A1"].price - 100.0)
        sign_b1 = math.copysign(1, states["B1"].price - 100.0)
        if sign_a1 != sign_b1:
            cross_sector_mismatches += 1

    assert cross_sector_mismatches > 0


def test_event_forced_probability_produces_move_within_magnitude_range():
    rng = random.Random(3)
    for _ in range(50):
        state = TickerState("AAPL", price=190.0, drift=0.0, volatility=0.0, sector="tech")
        maybe_trigger_event(state, probability=1.0, rng=rng)
        fraction_moved = abs(state.price - 190.0) / 190.0
        assert EVENT_MAGNITUDE_RANGE[0] <= fraction_moved <= EVENT_MAGNITUDE_RANGE[1]


def test_event_never_fires_at_zero_probability():
    rng = random.Random(4)
    for _ in range(1000):
        state = TickerState("AAPL", price=190.0, drift=0.0, volatility=0.0, sector="tech")
        maybe_trigger_event(state, probability=0.0, rng=rng)
        assert state.price == 190.0
