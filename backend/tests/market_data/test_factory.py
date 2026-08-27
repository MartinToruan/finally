"""Tests for create_provider — the sole MASSIVE_API_KEY branch point
(planning/MARKET_INTERFACE.md §7)."""

import pytest

from finally_backend.market_data.cache import PriceCache
from finally_backend.market_data.factory import create_provider
from finally_backend.market_data.massive_provider import MassiveProvider
from finally_backend.market_data.simulator import SimulatorProvider


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


def test_returns_simulator_when_env_var_unset(cache: PriceCache, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert isinstance(create_provider(cache), SimulatorProvider)


def test_returns_simulator_when_env_var_empty(cache: PriceCache, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    assert isinstance(create_provider(cache), SimulatorProvider)


def test_returns_simulator_when_env_var_is_only_whitespace(cache: PriceCache, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    assert isinstance(create_provider(cache), SimulatorProvider)


def test_returns_massive_provider_when_env_var_set(cache: PriceCache, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "some-key")
    provider = create_provider(cache)
    assert isinstance(provider, MassiveProvider)
    assert provider._api_key == "some-key"
