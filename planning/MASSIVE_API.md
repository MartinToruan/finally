# Massive API — Research Notes

*Research reference for the FinAlly market-data layer. This document covers the parts of the Massive API relevant to fetching real-time-ish and end-of-day prices for multiple tickers. It feeds directly into `MARKET_INTERFACE.md`, which designs the unified provider interface built on top of it.*

## 1. What "Massive" Is

[Massive](https://massive.com) is the rebrand of **Polygon.io**, effective October 30, 2025. It's a market-data vendor providing REST, WebSocket, and flat-file (S3) access to US stocks, options, indices, forex, crypto, and futures data, aggregated from all 19 major exchanges plus dark pools, FINRA TRFs, and OTC markets.

Practically, this means:

- The API host has moved from `api.polygon.io` to `api.massive.com`. Both are live in parallel for an extended transition period; existing API keys and integrations work unchanged against either.
- Documentation lives at [massive.com/docs](https://massive.com/docs); the old `polygon.io/docs/*` URLs 301-redirect to the equivalent `massive.com/docs/*` page.
- The official Python client package was renamed from `polygon-api-client` to **`massive`** (`pip install -U massive`, `from massive import RESTClient`).
- **We use the `api.massive.com` host and `massive` package in this project**, since Massive is the vendor's current identity and the legacy host has no guaranteed lifetime.

Sources: [Polygon.io is Now Massive](https://massive.com/blog/polygon-is-now-massive), [massive-com/client-python](https://github.com/massive-com/client-python).

## 2. Authentication

Every REST call needs an API key, obtained from the [Massive dashboard](https://massive.com/dashboard/keys). Two auth styles are supported:

1. **Query parameter** — append `&apiKey=YOUR_KEY` to any request URL. Simplest, used in most doc examples.
2. **Authorization header** — `Authorization: Bearer YOUR_KEY`. Preferred for real code, since query-param keys are more likely to leak into logs and proxies.

This project stores the key in the `MASSIVE_API_KEY` environment variable (per `planning/PLAN.md` §5), which happens to be the exact variable name Massive's own docs recommend for their SDKs — no translation needed between "our" env var and "their" convention.

```bash
curl "https://api.massive.com/v2/aggs/ticker/AAPL/prev" \
  -H "Authorization: Bearer $MASSIVE_API_KEY"
```

## 3. Access Methods

| Method | Use case | Used by FinAlly? |
|---|---|---|
| REST API | On-demand queries over HTTPS — snapshots, aggregates, previous close | **Yes** — this is our entire integration |
| WebSocket | Push-based real-time streams (`wss://socket.massive.com/stocks`, topics like `T.AAPL`, `Q.AAPL`, `AM.*`) | No — see §7 |
| Flat Files | Bulk historical S3 downloads (day/minute aggregates, trades, quotes) | No — out of scope, no bulk backtesting need |

`planning/PLAN.md` §6 already commits to **REST polling, not WebSocket**, to keep the integration simple and to work on the free tier (WebSocket streaming requires a paid plan). This document only covers REST.

## 4. Plans & Rate Limits

Massive/Polygon prices REST access per asset class (Stocks, Options, Indices, Forex, Crypto, Futures each have their own ladder). For Stocks:

| Plan | Price | Rate limit | Data recency | Historical depth |
|---|---|---|---|---|
| **Basic (free)** | $0, no card required | **5 calls/min** | End-of-day + 15-min delayed | 2 years |
| Starter | paid | higher | 15-min delayed | 5 years |
| Developer | paid | higher | 15-min delayed | 10 years |
| Advanced | paid | highest | **Real-time** | Full history |

The free Basic plan is what students will use by default. At 5 calls/min, a poll interval below 12 seconds risks 429s — this is why `PLAN.md` §6 specifies **polling every 15 seconds** on the free tier (with faster polling, down to 2–15s, available on paid tiers). A single poll can fetch *all* watched tickers at once via the snapshot endpoint (§5.1), so the call budget is per-poll-cycle, not per-ticker — the free tier's 5 calls/min is plenty for one watchlist.

Real-time last-quote/last-trade data (§5.4) requires the Advanced plan; on Basic/Starter/Developer it's 15-minutes-delayed. This is fine for a paper-trading demo app.

Source: [Basic plan / free tier details](https://apicostcalc.com/polygon.html) (5 calls/min, EOD + 15-min delayed, no card).

## 5. Endpoints Relevant to This Project

### 5.1 Full Market Snapshot — *the primary endpoint we use*

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT&apiKey=YOUR_KEY
```

Returns, in one call, the latest day bar, previous-day bar, last quote, last trade, latest minute bar, and computed today's-change for every ticker requested — exactly the multi-ticker "give me current prices for my whole watchlist" shape this project needs.

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `tickers` | comma-separated string | *(all ~10,000+ US tickers)* | **Always pass this** — restrict to the watchlist's tickers, both to control the fictitious-ticker problem (§8) and to keep the payload small |
| `include_otc` | boolean | `false` | Leave `false`; we don't trade OTC symbols |

**Example response** (trimmed to one ticker):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "day":     { "o": 190.10, "h": 191.40, "l": 189.80, "c": 190.85, "v": 41230012, "vw": 190.52 },
      "prevDay": { "o": 188.90, "h": 190.20, "l": 188.30, "c": 189.60, "v": 39812004, "vw": 189.31 },
      "lastQuote": { "p": 190.83, "s": 13, "P": 190.86, "S": 22, "t": 1756000000000000000 },
      "lastTrade": { "p": 190.85, "s": 100, "t": 1756000000000000000, "x": 4 },
      "min": { "o": 190.80, "h": 190.90, "l": 190.75, "c": 190.85, "v": 5000, "t": 1756000000000 },
      "todaysChange": 1.25,
      "todaysChangePerc": 0.66,
      "updated": 1756000000000000000
    }
  ]
}
```

Field cheat-sheet: `day` = today's running OHLCV bar, `prevDay` = yesterday's full bar, `lastQuote` = latest NBBO (`p`/`P` = bid/ask price, `s`/`S` = bid/ask size), `lastTrade` = latest print, `todaysChange`/`todaysChangePerc` = vs. `prevDay.c`, `updated` = nanosecond epoch timestamp of the last update backing this snapshot. **`day.c` (today's latest close-so-far) is the field we treat as "current price."**

**Python (raw HTTP, `httpx`):**

```python
import httpx

async def fetch_snapshot(tickers: list[str], api_key: str) -> dict:
    url = "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers"
    params = {"tickers": ",".join(tickers), "include_otc": "false"}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
```

**Python (official client):**

```python
from massive import RESTClient

client = RESTClient(api_key=MASSIVE_API_KEY)
snapshot = client.get_snapshot_all(market_type="stocks", tickers=["AAPL", "GOOGL", "MSFT"])
for s in snapshot:
    print(s.ticker, s.day.close, s.todays_change_percent)
```

### 5.2 Previous Close (single ticker, end-of-day)

```
GET /v2/aggs/ticker/AAPL/prev?adjusted=true&apiKey=YOUR_KEY
```

Returns yesterday's OHLCV as a one-row `results` array. Useful as a fallback/seed value (e.g., "last known price" before the first snapshot poll completes) but not needed for the main polling loop since the snapshot endpoint already includes `prevDay`.

```json
{
  "ticker": "AAPL",
  "adjusted": true,
  "resultsCount": 1,
  "status": "OK",
  "results": [
    { "T": "AAPL", "o": 188.90, "h": 190.20, "l": 188.30, "c": 189.60, "v": 39812004, "vw": 189.31, "t": 1755954000000, "n": 512004 }
  ]
}
```

`t` is the bar's start timestamp in Unix **milliseconds**; `n` is the transaction count.

### 5.3 Custom Bars / Aggregates (historical range, for charting)

```
GET /v2/aggs/ticker/AAPL/range/1/day/2026-07-01/2026-08-25?adjusted=true&sort=asc&limit=5000&apiKey=YOUR_KEY
```

`{multiplier}/{timespan}` (e.g. `1/minute`, `5/minute`, `1/day`) over a `{from}/{to}` date range (or millisecond timestamps). Returns a `results` array of OHLCV bars in the same `o/h/l/c/v/vw/t/n` shape as §5.2. `limit` caps the number of base aggregates returned (max 50,000, default 5,000).

Not needed for the live-simulator-driven charts described in `PLAN.md` §10 (those charts are built client-side from the SSE stream), but this is the endpoint to reach for if a future feature wants pre-market-open historical chart data.

### 5.4 Grouped Daily Bars (all tickers, one date)

```
GET /v2/aggs/grouped/locale/us/market/stocks/2026-08-24?adjusted=true&include_otc=false&apiKey=YOUR_KEY
```

One EOD OHLCV bar per ticker, for every US stock, on a single given date. Not used by this project (the snapshot endpoint's `prevDay` already covers our EOD need per-watchlist-ticker), but worth knowing about — it's the right tool if a future feature needs a market-wide EOD scan rather than per-ticker EOD.

### 5.5 Last Quote (NBBO)

```
GET /v2/last/nbbo/AAPL?apiKey=YOUR_KEY
```

Single most-recent bid/ask. **Requires the Advanced (or Business) plan for real-time data** — on lower plans this is 15-minutes-delayed like everything else. Not used directly: the snapshot endpoint's `lastQuote` field already surfaces this per-ticker in the same call we're already making, so a separate call here would be redundant.

## 6. Error Handling

Massive/Polygon REST errors follow standard conventions:

- **HTTP status codes**: `200` success, `401`/`403` bad or missing API key, `404` unknown route, `429` rate limit exceeded, `5xx` upstream issue.
- **Body shape on error**: `{"status": "ERROR", "request_id": "...", "error": "<human-readable message>"}` — the same envelope shape (`status`, `request_id`) as a success response, just with `status` set to `"ERROR"` and an `error` field instead of `results`/`tickers`.
- **On `429`**: back off and retry on the next poll cycle rather than retrying immediately — at 5 calls/min on the free tier, an immediate retry just consumes next-window budget.
- **On network failure or 5xx**: treat as "no update this cycle" and keep serving the last known cached price rather than erroring the whole app — a transient miss shouldn't blank out the watchlist. See `MARKET_INTERFACE.md` §5 for how this is applied in the provider implementation.

## 7. Why REST Polling, Not WebSocket

`PLAN.md` §6 already settles this, but for completeness: Massive's WebSocket API (`wss://socket.massive.com/stocks`, topic-based subscriptions like `T.AAPL`/`Q.AAPL`/`AM.*`) delivers true push-based real-time data, but real-time access itself requires a paid Advanced-tier plan — the free tier's WebSocket access, where available, is still delayed. Since this project targets the free tier as the default student experience, and since our own SSE layer to the browser already re-polls an in-memory cache on a fixed cadence (`PLAN.md` §6), there's no benefit to WebSocket's lower latency that the free tier could actually deliver. Plain REST polling (§5.1) is simpler, has a far smaller integration surface, and is what the free tier supports well.

## 8. Known Limitation: Fictitious Tickers

`PLAN.md` §6 allows any 1–5 uppercase-letter ticker to be added to the watchlist (format validation only, not a real-symbol lookup), and the simulator deterministically prices *any* such ticker, real or made up. **The Massive API cannot do this** — it only has data for tickers that actually trade on a real exchange. If a user (or the LLM) adds a well-formed but fictitious ticker (e.g. `ZZZZZ`) while running in Massive mode, the snapshot endpoint simply won't return a row for it, and no price data will ever arrive for that ticker. This is expected and doesn't need special-case error handling — see `MARKET_INTERFACE.md` §7 for how the interface documents this asymmetry.

## Sources

- [Polygon.io is Now Massive](https://massive.com/blog/polygon-is-now-massive)
- [Massive docs index](https://massive.com/docs/llms.txt)
- [Full Market Snapshot](https://massive.com/docs/stocks/get_v2_snapshot_locale_us_markets_stocks_tickers)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Grouped Daily Bars](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Last Quote (NBBO)](https://massive.com/docs/rest/stocks/trades-quotes/last-quote)
- [massive-com/client-python](https://github.com/massive-com/client-python)
- [Basic plan / free tier pricing](https://apicostcalc.com/polygon.html)
