"""Ticker symbol format validation.

Per planning/PLAN.md §6 "Ticker Validation": a ticker is accepted if it
matches 1-5 uppercase letters. This is format validation only, not a
real-symbol lookup — both the simulator and (where the symbol happens to
exist there) Massive will happily price a fictitious-but-well-formed ticker.
"""

from __future__ import annotations

import re

TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")


def is_valid_ticker(ticker: str) -> bool:
    return bool(TICKER_PATTERN.match(ticker))
