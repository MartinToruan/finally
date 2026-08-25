# Market Simulator — Design

*Details the `SimulatorProvider` implementation referenced by `MARKET_INTERFACE.md` §6. This is the default market data backend (used whenever `MASSIVE_API_KEY` is unset, per `PLAN.md` §5) and covers every requirement `PLAN.md` §6 lists for it: GBM price generation, ~500ms updates, correlated moves, occasional event jumps, realistic seed prices for the 10 defaults, and deterministic seeding for arbitrary later tickers.*

## 1. Why GBM

Geometric Brownian Motion is the standard toy model for a stock price: it never goes negative, moves are proportional to the current price (a $500 stock and a $20 stock both move by a plausible *percentage*, not the same dollar amount), and it's cheap to compute at 500ms cadence for a whole watchlist. Discretized per step:

```
S(t + dt) = S(t) * exp( (μ - σ²/2) * dt + σ * sqrt(dt) * Z )
```

where `μ` (drift) and `σ` (volatility) are per-ticker annualized parameters, `dt` is the step size expressed in years, and `Z` is a draw from a standard normal distribution. The multiplicative (`exp(...)`) form is used instead of the simpler additive `S += S*(μ*dt + σ*sqrt(dt)*Z)` because it guarantees `S(t+dt) > 0` for any `Z` — the additive Euler form can (rarely, but non-zero probability) send a low-priced or high-volatility ticker negative in a single unlucky step, which the multiplicative form can't.

## 2. Update Cadence & Step Size

`PLAN.md` §6 specifies ~500ms updates. `μ` and `σ` are expressed as **annualized** figures (the conventional units for these parameters — e.g. "18% annual volatility"), so each tick's `dt` is 500ms expressed as a fraction of a trading year:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 252 trading days, 6.5h/day
STEP_SECONDS = 0.5
DT = STEP_SECONDS / TRADING_SECONDS_PER_YEAR  # ≈ 8.48e-8
```

This keeps the simulator's per-tick moves visually sane (small, price-proportional jitter) without needing to reason about clock-time-vs-trading-time conversions anywhere else in the code — `μ`/`σ` are picked once per ticker in familiar "X% per year" terms, and `DT` absorbs the rest.

## 3. Module Layout

Continuing `MARKET_INTERFACE.md` §2's layout:

```
backend/src/finally_backend/market_data/
├── simulator.py         # SimulatorProvider — the update loop, orchestration
├── simulator_state.py   # TickerState dataclass, per-ticker GBM parameters
└── simulator_seed.py    # DEFAULT_SEEDS, derive_seed_price() for arbitrary tickers
```

## 4. Per-Ticker State & Seed Prices

```python
# simulator_state.py
from dataclasses import dataclass


@dataclass
class TickerState:
    ticker: str
    price: float
    drift: float        # annualized μ, e.g. 0.08 for 8%/yr
    volatility: float    # annualized σ, e.g. 0.25 for 25%/yr
    sector: str          # correlation grouping — see §5
```

Realistic seed prices and parameters for the 10 default tickers (`PLAN.md` §7's seed list), grouped into sectors for correlated movement (§5):

```python
# simulator_seed.py
DEFAULT_SEEDS: dict[str, TickerState] = {
    "AAPL":  TickerState("AAPL",  190.00, drift=0.10, volatility=0.25, sector="tech"),
    "GOOGL": TickerState("GOOGL", 175.00, drift=0.09, volatility=0.27, sector="tech"),
    "MSFT":  TickerState("MSFT",  420.00, drift=0.10, volatility=0.22, sector="tech"),
    "AMZN":  TickerState("AMZN",  185.00, drift=0.11, volatility=0.30, sector="tech"),
    "TSLA":  TickerState("TSLA",  250.00, drift=0.05, volatility=0.55, sector="auto"),
    "NVDA":  TickerState("NVDA",  130.00, drift=0.20, volatility=0.45, sector="tech"),
    "META":  TickerState("META",  510.00, drift=0.12, volatility=0.32, sector="tech"),
    "JPM":   TickerState("JPM",   210.00, drift=0.08, volatility=0.18, sector="finance"),
    "V":     TickerState("V",     280.00, drift=0.09, volatility=0.17, sector="finance"),
    "NFLX":  TickerState("NFLX",  680.00, drift=0.10, volatility=0.35, sector="media"),
}
```

### Deterministic Seeding for Arbitrary Tickers

`PLAN.md` §6 requires that any ticker added later (manually or by the LLM) gets a plausible starting price without a lookup table, deterministically — the same ticker symbol must always seed to the same starting price, both so re-adding a removed ticker behaves consistently and so tests are reproducible.

```python
import hashlib

def derive_seed_price(ticker: str) -> float:
    """Deterministically map a ticker symbol to a starting price in $10-$500,
    via a stable hash — same ticker always seeds to the same price."""
    digest = hashlib.sha256(ticker.encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF  # 0.0-1.0
    return round(10.0 + fraction * (500.0 - 10.0), 2)


def derive_seed_state(ticker: str) -> TickerState:
    if ticker in DEFAULT_SEEDS:
        return DEFAULT_SEEDS[ticker]
    digest = hashlib.sha256(ticker.encode("utf-8")).hexdigest()
    price = derive_seed_price(ticker)
    # Also derive plausible-looking drift/volatility from the hash, in
    # reasonable ranges, so unlisted tickers aren't all identically volatile.
    drift = 0.04 + (int(digest[8:16], 16) / 0xFFFFFFFF) * 0.12       # 4%-16%/yr
    volatility = 0.15 + (int(digest[16:24], 16) / 0xFFFFFFFF) * 0.35  # 15%-50%/yr
    return TickerState(ticker, price, drift, volatility, sector="other")
```

`hashlib.sha256` (over `random.seed(ticker)`) is used deliberately: Python's `random` module's hash-to-seed behavior isn't guaranteed stable across versions or `PYTHONHASHSEED` settings for non-integer seeds in every context, while slicing a `sha256` hex digest into `int(..., 16)` and normalizing is simple, dependency-free, and stable forever for a given input string.

## 5. Correlated Moves

`PLAN.md` §6 asks for correlated moves across tickers (e.g. tech stocks moving together) — a market where every ticker jitters totally independently doesn't look or feel like a real market. This is implemented with a **shared factor model**: each tick, draw one market-wide shock and one shock per sector, and blend them with each ticker's own idiosyncratic draw.

```python
import random

SECTOR_WEIGHT = 0.35   # how much of a sector's tickers move together
MARKET_WEIGHT = 0.20   # how much the whole market moves together
IDIOSYNCRATIC_WEIGHT = 1.0 - SECTOR_WEIGHT - MARKET_WEIGHT  # 0.45, own-name noise

def step_all(states: dict[str, TickerState], dt: float) -> None:
    market_shock = random.gauss(0, 1)
    sector_shocks: dict[str, float] = {}

    for state in states.values():
        if state.sector not in sector_shocks:
            sector_shocks[state.sector] = random.gauss(0, 1)
        idiosyncratic = random.gauss(0, 1)

        z = (
            MARKET_WEIGHT * market_shock
            + SECTOR_WEIGHT * sector_shocks[state.sector]
            + IDIOSYNCRATIC_WEIGHT * idiosyncratic
        )
        # z is a weighted sum of independent standard normals, so it isn't
        # itself unit-variance; normalize so the effective vol still matches
        # `state.volatility` as configured.
        norm = (MARKET_WEIGHT**2 + SECTOR_WEIGHT**2 + IDIOSYNCRATIC_WEIGHT**2) ** 0.5
        z /= norm

        drift_term = (state.drift - 0.5 * state.volatility**2) * dt
        diffusion_term = state.volatility * (dt ** 0.5) * z
        state.price *= _exp(drift_term + diffusion_term)
```

(`_exp` = `math.exp`.) One `market_shock` draw and one draw per distinct sector are made *once per tick*, then reused across every ticker in that sector — this is what actually produces the correlation; each ticker's own `idiosyncratic` draw is what keeps them from moving in lockstep. Tickers seeded via `derive_seed_state` (§4) fall into a shared `"other"` sector bucket, so ad-hoc tickers added at runtime still get *some* correlated co-movement with each other, just not with the curated sectors.

## 6. Random Events

`PLAN.md` §6 asks for "occasional random events — sudden 2-5% moves on a ticker for drama." This is a small independent check per tick, applied *in addition to* the regular GBM step, not instead of it:

```python
EVENT_PROBABILITY_PER_TICK = 0.0005  # ~once every ~33 min per ticker at 500ms ticks
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)  # 2%-5% move

def maybe_trigger_event(state: TickerState) -> None:
    if random.random() >= EVENT_PROBABILITY_PER_TICK:
        return
    magnitude = random.uniform(*EVENT_MAGNITUDE_RANGE)
    direction = random.choice([1, -1])
    state.price *= 1 + direction * magnitude
```

`EVENT_PROBABILITY_PER_TICK` is tuned so that, across a 10-ticker watchlist, a visible "jump" happens every few minutes somewhere on screen — frequent enough to be a noticeable demo moment, rare enough that it still reads as an event rather than the normal texture of price movement. This is a tunable constant, not a derived value — nudge it directly if playtesting wants events more or less often.

## 7. The Update Loop / `SimulatorProvider`

```python
# simulator.py
import asyncio
from datetime import datetime, timezone
from ..market_data.base import MarketDataProvider
from ..market_data.cache import PriceCache
from .simulator_state import TickerState
from .simulator_seed import DEFAULT_SEEDS, derive_seed_state
from .steps import step_all, maybe_trigger_event, DT  # §5, §6, §2

TICK_SECONDS = 0.5


class SimulatorProvider(MarketDataProvider):
    def __init__(self, cache: PriceCache):
        self._cache = cache
        self._states: dict[str, TickerState] = {}
        self._task: asyncio.Task | None = None

    async def start(self, tickers: set[str]) -> None:
        for ticker in tickers:
            self._states[ticker] = self._seed(ticker)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._states:
            self._states[ticker] = self._seed(ticker)
            # Write an immediate cache entry so a newly-added ticker has a
            # price right away, instead of waiting up to one tick.
            await self._cache.update(ticker, self._states[ticker].price, datetime.now(timezone.utc))

    async def remove_ticker(self, ticker: str) -> None:
        self._states.pop(ticker, None)

    def _seed(self, ticker: str) -> TickerState:
        return DEFAULT_SEEDS.get(ticker) or derive_seed_state(ticker)

    async def _run_loop(self) -> None:
        while True:
            step_all(self._states, DT)
            for state in self._states.values():
                maybe_trigger_event(state)
            now = datetime.now(timezone.utc)
            for state in self._states.values():
                await self._cache.update(state.ticker, state.price, now)
            await asyncio.sleep(TICK_SECONDS)
```

This satisfies the same `MarketDataProvider` ABC as `MassiveProvider` (`MARKET_INTERFACE.md` §3) with no special-casing anywhere else in the app — the SSE endpoint, trade execution, and chat context loader all read `PriceCache` exactly as they would in Massive mode.

## 8. Testing

Per `PLAN.md` §12 ("Market data: simulator generates valid prices, GBM math is correct... both implementations conform to the abstract interface"):

- **GBM correctness**: run `step_all` over many ticks for a single ticker with `EVENT_PROBABILITY_PER_TICK` and correlation weights set to isolate pure GBM, and check the empirical mean/variance of `log(S(t)/S(0))` converge to the theoretical `(μ - σ²/2)*t` / `σ²*t` as `t` grows — the standard way to unit-test a GBM implementation without asserting on any single random draw.
- **Determinism of seeding**: `derive_seed_price("ZZZZZ")` called twice (including across a fresh process) returns the same value; `derive_seed_price` output always falls in `[10, 500]`.
- **Correlation sanity**: with `IDIOSYNCRATIC_WEIGHT` forced to 0, two tickers in the same sector should move in lockstep every tick (same sign, proportional magnitude); two tickers in different sectors should not.
- **Event bounds**: force `EVENT_PROBABILITY_PER_TICK = 1.0` for a test tick and assert the resulting move is within `EVENT_MAGNITUDE_RANGE`.
- **Interface conformance**: the shared `MarketDataProvider` contract test suite described in `MARKET_INTERFACE.md` §10 runs against `SimulatorProvider` too — `start()` populates the cache, `add_ticker` seeds and caches immediately, `remove_ticker` stops future updates for that ticker without touching others.
- **Seed sanity for the 10 defaults**: each `DEFAULT_SEEDS` entry's price is within a plausible band of its real-world counterpart at time of writing (a loose sanity bound, not a live-data assertion — these are fixed simulator constants, not meant to track the real market).
