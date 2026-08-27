"""GBM step math, correlated moves, and random events for the simulator.

See planning/MARKET_SIMULATOR.md §1-2 (GBM), §5 (correlation), §6 (events).
"""

from __future__ import annotations

import math
import random

from .simulator_state import TickerState

# --- Update cadence / step size (planning/MARKET_SIMULATOR.md §2) ---------

TICK_SECONDS = 0.5
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 252 trading days, 6.5h/day
DT = TICK_SECONDS / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

# --- Correlated moves (planning/MARKET_SIMULATOR.md §5) --------------------

SECTOR_WEIGHT = 0.35  # how much of a sector's tickers move together
MARKET_WEIGHT = 0.20  # how much the whole market moves together
IDIOSYNCRATIC_WEIGHT = 1.0 - SECTOR_WEIGHT - MARKET_WEIGHT  # 0.45, own-name noise

# --- Random events (planning/MARKET_SIMULATOR.md §6) ------------------------

EVENT_PROBABILITY_PER_TICK = 0.0005  # ~once every ~33 min per ticker at 500ms ticks
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)  # 2%-5% move


def step_all(
    states: dict[str, TickerState],
    dt: float = DT,
    *,
    market_weight: float = MARKET_WEIGHT,
    sector_weight: float = SECTOR_WEIGHT,
    idiosyncratic_weight: float = IDIOSYNCRATIC_WEIGHT,
    rng: random.Random | None = None,
) -> None:
    """Advance every ticker in `states` by one GBM tick, in place.

    Each tick draws one market-wide shock and one shock per sector, then
    blends them with each ticker's own idiosyncratic draw — this is what
    produces correlated co-movement within (and, more weakly, across)
    sectors while still letting each name jitter independently.

    `market_weight`/`sector_weight`/`idiosyncratic_weight` and `rng` are
    overridable (defaulting to the module constants and the global `random`
    module) purely to make the correlation behavior deterministically
    testable in isolation — production code should rely on the defaults.
    """
    r = rng or random
    market_shock = r.gauss(0, 1)
    sector_shocks: dict[str, float] = {}
    norm = (market_weight**2 + sector_weight**2 + idiosyncratic_weight**2) ** 0.5

    for state in states.values():
        if state.sector not in sector_shocks:
            sector_shocks[state.sector] = r.gauss(0, 1)
        idiosyncratic = r.gauss(0, 1)

        z = (
            market_weight * market_shock
            + sector_weight * sector_shocks[state.sector]
            + idiosyncratic_weight * idiosyncratic
        )
        # z is a weighted sum of independent standard normals, so it isn't
        # itself unit-variance; normalize so the effective vol still matches
        # `state.volatility` as configured.
        if norm > 0:
            z /= norm

        drift_term = (state.drift - 0.5 * state.volatility**2) * dt
        diffusion_term = state.volatility * (dt**0.5) * z
        state.price *= math.exp(drift_term + diffusion_term)


def maybe_trigger_event(
    state: TickerState,
    *,
    probability: float = EVENT_PROBABILITY_PER_TICK,
    magnitude_range: tuple[float, float] = EVENT_MAGNITUDE_RANGE,
    rng: random.Random | None = None,
) -> None:
    """With small probability, apply a sudden 2-5% jump to `state`, in place.

    Applied in addition to the regular GBM step, not instead of it.
    """
    r = rng or random
    if r.random() >= probability:
        return
    magnitude = r.uniform(*magnitude_range)
    direction = r.choice([1, -1])
    state.price *= 1 + direction * magnitude
