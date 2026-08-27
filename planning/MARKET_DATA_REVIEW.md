# Market Data Backend — Code Review

*Review date: 2026-08-27. Scope: `backend/src/finally_backend/market_data/` and
`backend/tests/market_data/`, reviewed against `planning/PLAN.md` §5-6,
`planning/MARKET_INTERFACE.md`, `planning/MARKET_SIMULATOR.md`, and
`planning/MASSIVE_API.md`. This is a static read-through plus targeted
runtime probes for the specific hazards noted below — not a full fuzzing or
load-testing pass.*

## Summary

The market data layer implements the full design from the three planning
docs: the `MarketDataProvider` interface, `PriceCache`, `SimulatorProvider`
(GBM + correlation + events + deterministic seeding), `MassiveProvider` (REST
polling), and `create_provider()`. Structure and behavior match the
illustrative code in the docs closely, with a handful of deliberate,
documented improvements made during implementation.

**Test run**: `uv run pytest -q` → **86 passed, 0 failed, 0.76s**. No
skipped or xfailed tests.

Beyond re-reading every module, this review specifically probed three
runtime hazards that static reading alone wouldn't surface: a concurrency
race in `MassiveProvider`, an error-handling gap in the same class's poll
loop, and a regex edge case in ticker validation. All three reproduced.
Details below.

| # | Finding | Severity | Reproduced? |
|---|---|---|---|
| 1 | `MassiveProvider`: in-flight poll can resurrect a just-removed ticker's cached price | High | Yes |
| 2 | `MassiveProvider._poll_loop`: malformed/unexpected response body crashes the background task permanently | High | Yes |
| 3 | `is_valid_ticker()`: trailing-newline strings pass validation | Medium | Yes |
| 4 | `derive_seed_state()` still returns a shared, mutable `DEFAULT_SEEDS` instance for curated tickers | Low (latent) | N/A — not currently triggered by any real code path |
| 5 | Minor test-hygiene: an explicitly-passed `httpx.AsyncClient` in the conformance suite's Massive fixture is never closed | Low | N/A |

Findings 1-3 are real defects. Finding 4 is a latent footgun worth
documenting, not a live bug. Finding 5 is a test-suite nit. None of them were
caught by the existing 86 tests — each required either a race-condition
repro or a targeted edge-case input the current suite doesn't exercise; new
regression tests are proposed for all of them below.

---

## Findings

### 1. [High] `MassiveProvider`: removing a ticker mid-poll can resurrect its price

**Where**: `massive_provider.py:57-59` (`remove_ticker`) vs. `massive_provider.py:77-93` (`_poll_once`).

`remove_ticker()` clears both `self._tickers` and the cache entry
synchronously. But `_poll_once()` already captured its own ticker list into
the outgoing request's `params` before the `await self._client.get(...)`
call — it never re-checks `self._tickers` when processing the response. If a
user removes a ticker from the watchlist (e.g. via `DELETE
/api/watchlist/{ticker}`) while a poll for that ticker is still in flight,
the stale response's row for that ticker gets written back into the cache
right after `remove_ticker()` cleared it.

Reproduced directly (slow-response transport, `remove_ticker("AAPL")` fired
while the request awaits):

```
cache right after remove_ticker: None
tickers set right after remove_ticker: set()
cache AFTER the in-flight poll response arrived: PriceUpdate(ticker='AAPL', price=190.0, ...)
```

**Impact**: `PLAN.md` §6 relies on the SSE-priced ticker set being
equivalent to the watchlist ("a priced ticker never has to keep streaming on
behalf of an orphaned position"). This race breaks that invariant — a
removed ticker can keep appearing in `GET /api/stream/prices` output for at
least one more (very stale) tick after removal, in Massive mode specifically
(the simulator isn't affected — see the note at the end of this finding).

**Why the simulator doesn't have this problem**: `SimulatorProvider._tick()`
iterates `self._states` directly, and none of its `await` points are genuine
suspension points in practice (see Finding-adjacent note below on
`asyncio.Lock`'s uncontended fast path), so a concurrent `remove_ticker()`
can't interleave mid-tick. `MassiveProvider._poll_once()` is different
because `await self._client.get(...)` is a real I/O suspension point.

**Suggested fix**: in `_poll_once()`, filter the response against a
fresh read of `self._tickers` before writing to the cache:
```python
for row in body.get("tickers", []):
    ticker = row.get("ticker")
    if ticker not in self._tickers:  # dropped from the watchlist mid-poll
        continue
    ...
```

### 2. [High] `MassiveProvider._poll_loop` doesn't fail soft against malformed responses

**Where**: `massive_provider.py:61-93`.

`_poll_loop`'s exception handling only catches `httpx.HTTPStatusError` and
`httpx.HTTPError` — both raised by the HTTP transport layer. It does not
guard `_poll_once()`'s response-parsing code (`resp.json()`, `row["ticker"]`,
`body.get("tickers", [])`). A response that is valid HTTP 200 but has an
unexpected body shape — a row missing the `"ticker"` key, a non-JSON body, a
`"tickers"` field that isn't a list — raises an exception that is *not*
caught, which kills the polling `asyncio.Task` outright.

Reproduced with a row missing `"ticker"`:

```
task done? True
task exception: 'ticker'
stop() raised: KeyError('ticker')
```

**Impact**: this is exactly the class of failure `MARKET_INTERFACE.md` §1
and `MASSIVE_API.md` §6 call out as the reason for fail-soft handling — "a
Massive API hiccup... degrades to 'serve the last known price' — it never
take[s] down the SSE stream." An HTTP-level hiccup (429, 5xx, network error)
is handled correctly and matches that goal (covered by
`test_poll_loop_survives_errors_and_keeps_serving_last_known_price`). A
response-shape hiccup is not: the cache freezes at its last value **forever**
with no further poll attempts, silently (nothing awaits the task under
normal operation, so nothing surfaces the failure at the time it happens).
The swallowed exception then resurfaces unexpectedly later, from
`await provider.stop()` during a clean app shutdown — turning a data hiccup
into a shutdown-path crash.

**Suggested fix**: widen `_poll_once()`'s own error surface, or add a
catch-all in `_poll_loop` for `(ValueError, KeyError, TypeError)` in addition
to the httpx exceptions, logged the same way as the existing branches. Since
Massive is a third-party API whose response shape isn't contractually
guaranteed to a caller, treating any parsing failure the same as an HTTP
failure (skip this cycle, keep last known prices, try again next cycle) is
consistent with the rest of the design.

### 3. [Medium] `is_valid_ticker()` accepts a trailing newline

**Where**: `validation.py:13-17`.

```python
TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")
def is_valid_ticker(ticker: str) -> bool:
    return bool(TICKER_PATTERN.match(ticker))
```

In Python's `re` module, `$` matches both at the absolute end of the string
*and* immediately before a single trailing `\n` — this is standard `re`
behavior, independent of `re.MULTILINE`. Reproduced:

```python
>>> is_valid_ticker("AAPL\n")
True
>>> is_valid_ticker("AAPL")
True
```

**Impact**: `PLAN.md` §6 specifies this exact regex for `POST
/api/watchlist` and LLM-issued `watchlist_changes` validation gates. Once a
consuming API route is built on top of `is_valid_ticker()`, a ticker
string with a trailing newline (plausible from copy-paste in a form field,
or a trailing newline in LLM-generated JSON string content) would pass
validation and then propagate downstream — as a SQLite `watchlist.ticker`
value, a dict key in `PriceCache`/`SimulatorProvider._states`, and an input
to `derive_seed_price()`'s hash — creating a ticker that's visually
indistinguishable from the clean version but functionally a different key
everywhere (a different cache entry, a different seeded price, and a row a
user could never re-select to remove by typing "AAPL" normally).

**Suggested fix**: anchor with `\Z` instead of `$` (`^[A-Z]{1,5}\Z`), or use
`re.fullmatch` instead of `.match()` — both avoid the trailing-newline
special case.

### 4. [Low, latent] `derive_seed_state()` returns the live `DEFAULT_SEEDS` object for curated tickers

**Where**: `simulator_seed.py:50-53`.

```python
def derive_seed_state(ticker: str) -> TickerState:
    if ticker in DEFAULT_SEEDS:
        return DEFAULT_SEEDS[ticker]
    ...
```

This matches `MARKET_SIMULATOR.md` §4's illustrative code exactly, and
`TickerState` is a mutable dataclass. During initial implementation, this
exact pattern caused a real bug: `SimulatorProvider._seed()` originally
returned this shared instance directly, and since `SimulatorProvider._tick()`
mutates `state.price` in place, one simulator's price walk permanently
corrupted the global `DEFAULT_SEEDS` table for every other `SimulatorProvider`
instance (and test) in the process. **This was caught and fixed** —
`simulator.py:57-66`'s `_seed()` now calls `dataclasses.replace(template)` to
copy before use, verified by `test_default_seeds_have_plausible_prices`
passing deterministically regardless of test execution order.

The residual risk is narrower but not eliminated: `derive_seed_state()`
itself is exported from the package and still hands back the shared
reference for any of the 10 curated tickers. No current call site mutates
that return value directly (only `SimulatorProvider._seed()` calls it, and
only for the *non*-curated branch, since it checks `DEFAULT_SEEDS` itself
first and copies before falling through). But it's a footgun for any future
caller — inside this codebase or reused elsewhere — who calls
`derive_seed_state("AAPL")` and mutates the result expecting an independent
copy, per the general contract implied by a function named `derive_*`.

**Suggested fix**: have `derive_seed_state()` return `dataclasses.replace(DEFAULT_SEEDS[ticker])` in the curated-ticker branch too, so the function is safe by construction rather than safe-by-caller-discipline. `test_derive_seed_state_returns_curated_state_for_default_tickers` currently asserts identity (`is`) with the shared object; that assertion would need to change to an equality check if this fix is applied.

### 5. [Low] Test-suite nit: an externally-supplied client isn't closed in the conformance fixture

**Where**: `tests/market_data/test_provider_conformance.py`, `harness` fixture.

The Massive branch of the parametrized `harness` fixture builds its own
`httpx.AsyncClient` and passes it explicitly to `MassiveProvider(...,
client=client)`. Per the (correct) ownership contract in `massive_provider.py`
(`_owns_client = client is None`), `provider.stop()` therefore never closes
it. Since it's backed by `httpx.MockTransport` (no real socket), this leaks
nothing observable, but it's inconsistent with the fixture's intent to clean
up after itself. Minor; worth an explicit `await client.aclose()` in the
fixture's teardown for hygiene.

---

## Conformance vs. the planning docs

| Requirement | Source | Status |
|---|---|---|
| One `MarketDataProvider` ABC, two implementations | `MARKET_INTERFACE.md` §3 | ✅ Matches |
| `PriceUpdate` frozen dataclass w/ `change`/`change_percent`/`direction` | `MARKET_INTERFACE.md` §3 | ✅ Matches |
| Shared `PriceCache`, `previous_price` from cache's own last value (not `prevDay`) | `MARKET_INTERFACE.md` §4 | ✅ Matches |
| `MassiveProvider`: one HTTP call/cycle, 15s default poll, Bearer auth, fail-soft on HTTP errors | `MARKET_INTERFACE.md` §5, `MASSIVE_API.md` §4-6 | ✅ HTTP-level fail-soft matches; ⚠️ response-parsing fail-soft has a gap (Finding 2) |
| `SimulatorProvider`: ~500ms GBM, multiplicative form, correlated moves, events, deterministic seeding | `MARKET_SIMULATOR.md` §1-7 | ✅ Matches (weights, constants, and formulas verified identical to the doc) |
| Ticker validation `^[A-Z]{1,5}$` | `PLAN.md` §6 | ⚠️ Off-by-newline gap (Finding 3) |
| `create_provider()` as sole `MASSIVE_API_KEY` branch point | `MARKET_INTERFACE.md` §7 | ✅ Matches |
| Module layout under `market_data/` | `MARKET_INTERFACE.md` §2, `MARKET_SIMULATOR.md` §3 | ✅ All proposed files present, plus `validation.py` and `steps.py` (reasonable additions, not contradicting the doc) |
| Testing per `MARKET_INTERFACE.md` §10 / `MARKET_SIMULATOR.md` §8 | both docs | ✅ All listed scenarios covered (GBM Monte Carlo, determinism, correlation sanity, event bounds, shared conformance suite, Massive mocked HTTP scenarios) |

### Deliberate, beneficial deviations from the docs' illustrative code

These are intentional and improve on the docs' example code rather than
depart from its intent:

- **`PriceCache.remove()`** (not in the original design) — added and wired
  into both providers' `remove_ticker()`. Without it, a removed ticker's
  price would linger in the cache forever and the SSE endpoint (which
  iterates `cache.get_all()`) would keep broadcasting it indefinitely,
  contradicting `PLAN.md` §6's "SSE streams tickers known to the system"
  equivalence-to-watchlist claim. (Finding 1 shows this mechanism can still
  be raced in `MassiveProvider` specifically — the fix belongs in
  `_poll_once`, not in reverting this addition.)
- **`SimulatorProvider.start()` writes an immediate cache entry per ticker**
  (the doc's `start()` only seeds `self._states` and waits for the first
  tick). This means the watchlist has prices the instant the app starts,
  rather than up to `TICK_SECONDS` later.
- **`step_all()`'s `norm > 0` guard** before dividing — the doc's version
  divides unconditionally. Only matters if a caller passes all three
  correlation weights as zero (none of the current tests or production
  defaults do), but it avoids a `ZeroDivisionError` for that edge case.
- **`step_all()`/`maybe_trigger_event()` accept overridable weights/probability/rng** —
  the doc's version reads module-level constants directly, which would make
  the "force `IDIOSYNCRATIC_WEIGHT` to 0" and "force `EVENT_PROBABILITY_PER_TICK`
  to 1.0" test scenarios from `MARKET_SIMULATOR.md` §8 awkward to isolate
  without mutating shared module state. Defaults still match the doc exactly.

### Verified-safe design property (not a finding)

This review specifically checked whether `SimulatorProvider._tick()`'s
iteration over `self._states` could be raced by a concurrent `add_ticker()`/
`remove_ticker()` call the same way `MassiveProvider` can (Finding 1) — e.g.
a `RuntimeError: dictionary changed size during iteration` if a ticker is
removed mid-tick. Reproduced experimentally with a forced interleave: **no
crash, no incorrect state**. The reason is that `PriceCache.update()`'s
`async with self._lock` never actually suspends when uncontended
(`asyncio.Lock`'s fast path returns without yielding), so
`SimulatorProvider._tick()` has no genuine suspension point and runs
start-to-finish atomically with respect to other coroutines under normal
conditions — unlike `MassiveProvider._poll_once()`, whose
`await self._client.get(...)` is real I/O and a genuine yield point. This is
correct today but is an implementation-detail dependency (on `Lock`'s
uncontended fast path never yielding) rather than an explicit guarantee, so
it's worth keeping in mind if `PriceCache.update()` ever gains a real
`await` internally (e.g. if it started doing async I/O of its own).

---

## Test suite assessment

- **Coverage**: all scenarios `PLAN.md` §12, `MARKET_INTERFACE.md` §10, and
  `MARKET_SIMULATOR.md` §8 call for are present: GBM correctness via Monte
  Carlo mean/variance convergence, positivity guarantee, deterministic
  seeding (value + bounds), correlation sanity (same-sector lockstep,
  cross-sector non-lockstep), event magnitude bounds, the shared
  provider-conformance suite parametrized over both concrete providers, and
  Massive-specific HTTP scenarios (normal update, missing ticker, no-trading
  ticker, 429, network error, sustained fail-soft recovery).
- **Determinism**: all statistical tests (GBM convergence, correlation) use
  seeded `random.Random` instances passed explicitly rather than the global
  `random` module, so they're fully reproducible — no flaky statistical
  assertions.
- **Timing-based tests**: a few tests (`test_stop_cancels_the_background_loop`,
  `test_poll_loop_survives_errors_and_keeps_serving_last_known_price`,
  `test_stop_closes_a_client_the_provider_created_itself`) rely on real
  `asyncio.sleep()` calls with generous margins (e.g. asserting `>= 2` polls
  over a window sized for ~5). Low flakiness risk, but worth knowing these
  are the only tests with any dependency on wall-clock scheduling.
- **Gaps**: none of the three real defects above (Findings 1-3) are caught
  by the existing suite — each needs a new test:
  - a regression test for Finding 1 (slow-response transport + concurrent
    `remove_ticker()`, along the lines of the repro used in this review)
  - a regression test for Finding 2 (malformed row shape fed through
    `_poll_once()`/`_poll_loop()`, asserting the task survives and the cache
    keeps its last value)
  - a parametrized case for Finding 3 (`is_valid_ticker("AAPL\n")` →
    `False`)

## Recommendation

Two of the three real findings (1 and 2) sit in `MassiveProvider`, which
only activates when `MASSIVE_API_KEY` is set — the simulator path most
students will run is unaffected. All three have small, localized fixes (a
membership re-check, a wider except clause, a regex anchor change) that
don't require any structural changes to the interface or module layout. None
of them block moving forward with the rest of the backend, but Findings 1
and 2 should be fixed before this provider is exercised against a real
Massive API key in anything resembling a longer-running deployment, since
Finding 2 in particular causes a silent, permanent, un-self-healing loss of
live pricing.
