"""Standalone demo of the market data backend — NOT the real FastAPI app.

The full app described in planning/PLAN.md (§8 API endpoints, §7 database,
§9 chat, etc.) doesn't exist yet; this is a small, self-contained way to
watch `finally_backend.market_data` run for real: it wires a `PriceCache`
and whichever `MarketDataProvider` `create_provider()` selects (the
simulator by default, or MassiveProvider if MASSIVE_API_KEY is set) up to
the one SSE endpoint the plan specifies (`GET /api/stream/prices`,
PLAN.md §8), plus a minimal HTML page that consumes it with a native
EventSource so prices are visible ticking in a browser.

Run it with:
    uv run uvicorn finally_backend.demo:app --port 8000

Then open http://localhost:8000/ in a browser.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from .market_data import PriceCache, create_provider
from .market_data.simulator_seed import DEFAULT_SEEDS

DEFAULT_TICKERS = set(DEFAULT_SEEDS)  # the 10 tickers from PLAN.md §7
STREAM_INTERVAL_SECONDS = 0.5  # PLAN.md §6: SSE pushes at a fixed ~500ms cadence


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    provider = create_provider(cache)
    await provider.start(DEFAULT_TICKERS)
    app.state.cache = cache
    app.state.provider = provider
    yield
    await provider.stop()


app = FastAPI(lifespan=lifespan, title="FinAlly market data demo")


def _format_sse_event(update) -> str:
    payload = {
        "ticker": update.ticker,
        "price": round(update.price, 2),
        "previous_price": round(update.previous_price, 2),
        "change_percent": round(update.change_percent, 4),
        "direction": update.direction.value,
        "timestamp": update.timestamp.isoformat(),
    }
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/api/stream/prices")
async def stream_prices():
    """SSE stream of live price updates — planning/PLAN.md §6/§8."""
    cache: PriceCache = app.state.cache

    async def event_source():
        import asyncio

        while True:
            for update in cache.get_all().values():
                yield _format_sse_event(update)
            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return _PAGE


_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>FinAlly — Market Data Demo</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --yellow: #ecad0a; --blue: #209dd7; --green: #3fb950; --red: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: ui-monospace, "SF Mono", Consolas, monospace;
    padding: 2rem;
  }
  h1 { color: var(--yellow); font-size: 1.1rem; letter-spacing: 0.05em; text-transform: uppercase; }
  .status { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--red); margin-right: 6px; }
  .dot.live { background: var(--green); }
  table { border-collapse: collapse; width: 100%; max-width: 640px; }
  th { text-align: left; color: var(--muted); font-weight: normal; font-size: 0.8rem; padding: 0.4rem 0.8rem; border-bottom: 1px solid var(--border); }
  td { padding: 0.5rem 0.8rem; border-bottom: 1px solid var(--border); font-size: 0.95rem; transition: background-color 0.5s ease; }
  tr td:first-child { color: var(--blue); font-weight: bold; }
  .up { color: var(--green); }
  .down { color: var(--red); }
  .flash-up { background-color: rgba(63, 185, 80, 0.35) !important; transition: none !important; }
  .flash-down { background-color: rgba(248, 81, 73, 0.35) !important; transition: none !important; }
</style>
</head>
<body>
  <h1>FinAlly — Market Data Demo</h1>
  <div class="status"><span id="dot" class="dot"></span><span id="status-text">connecting…</span></div>
  <table>
    <thead><tr><th>Ticker</th><th>Price</th><th>Change</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>

  <script>
    const rows = document.getElementById("rows");
    const dot = document.getElementById("dot");
    const statusText = document.getElementById("status-text");
    const cells = {};

    function ensureRow(ticker) {
      if (cells[ticker]) return cells[ticker];
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${ticker}</td><td class="price"></td><td class="change"></td>`;
      rows.appendChild(tr);
      const entry = { price: tr.querySelector(".price"), change: tr.querySelector(".change") };
      cells[ticker] = entry;
      return entry;
    }

    const source = new EventSource("/api/stream/prices");
    source.onopen = () => { dot.classList.add("live"); statusText.textContent = "connected"; };
    source.onerror = () => { dot.classList.remove("live"); statusText.textContent = "reconnecting…"; };
    source.onmessage = (e) => {
      const u = JSON.parse(e.data);
      const cell = ensureRow(u.ticker);
      cell.price.textContent = "$" + u.price.toFixed(2);
      cell.change.textContent = (u.change_percent >= 0 ? "+" : "") + u.change_percent.toFixed(2) + "%";
      cell.change.className = "change " + (u.direction === "up" ? "up" : u.direction === "down" ? "down" : "");
      if (u.direction !== "flat") {
        const flashClass = u.direction === "up" ? "flash-up" : "flash-down";
        cell.price.classList.remove("flash-up", "flash-down");
        void cell.price.offsetWidth; // restart the CSS transition
        cell.price.classList.add(flashClass);
        setTimeout(() => cell.price.classList.remove(flashClass), 500);
      }
    };
  </script>
</body>
</html>"""
