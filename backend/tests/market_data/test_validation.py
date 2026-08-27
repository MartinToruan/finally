"""Tests for ticker symbol format validation (planning/PLAN.md §6)."""

import pytest

from finally_backend.market_data.validation import is_valid_ticker


@pytest.mark.parametrize("ticker", ["A", "AAPL", "GOOGL", "TSLA", "ZZZZZ"])
def test_valid_tickers(ticker: str):
    assert is_valid_ticker(ticker) is True


@pytest.mark.parametrize(
    "ticker",
    [
        "",  # empty
        "AAPLL2",  # too long / has a digit
        "aapl",  # lowercase
        "AA PL",  # whitespace
        "AAPL1",  # digit
        "AAPL!",  # punctuation
        "ABCDEF",  # 6 letters, too long
        "AAPL\n",  # regression: trailing newline (MARKET_DATA_REVIEW.md Finding 3)
        "AAPL\r\n",  # regression: trailing CRLF
        "\nAAPL",  # leading newline
    ],
)
def test_invalid_tickers(ticker: str):
    assert is_valid_ticker(ticker) is False
