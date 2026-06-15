"""PriceCharting client: token gating, query building, pennies->USD, grades."""
from __future__ import annotations

import pytest

from lib.pricecharting_client import PriceChartingClient


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class _FakeSession:
    """Records the last GET and returns a canned payload."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):  # noqa: ANN001
        self.calls.append((url, params or {}))
        return _FakeResponse(self.payload, self.status_code)


# A representative PriceCharting trading-card product (prices are integer pennies).
SAMPLE = {
    "status": "success",
    "id": "12345",
    "product-name": "Charizard #4",
    "console-name": "Pokemon Base Set",
    "loose-price": 32500,        # $325.00 ungraded
    "cib-price": 60000,          # grade 7
    "new-price": 85000,          # grade 8
    "graded-price": 120000,      # grade 9
    "box-only-price": 180000,    # grade 9.5
    "manual-only-price": 350000, # PSA 10
    "bgs-10-price": 900000,      # BGS 10
}


def _client_with(payload, status_code=200, token="x" * 40):
    client = PriceChartingClient(token=token)
    client._session = _FakeSession(payload, status_code)
    return client


def test_disabled_without_token_makes_no_request():
    client = PriceChartingClient(token="")
    # Swap in a session that would explode if touched.
    client._session = _FakeSession({"status": "success"})
    assert client.enabled is False
    assert client.lookup_price(name="Charizard") == (None, None)
    assert client.find_product(name="Charizard") is None
    assert client._session.calls == []  # never hit the network


def test_lookup_price_returns_ungraded_usd():
    client = _client_with(SAMPLE)
    price, product = client.lookup_price(name="Charizard", set_name="Base Set", card_number="4")
    assert price == 325.00
    assert product["id"] == "12345"


def test_query_is_built_and_token_attached():
    client = _client_with(SAMPLE)
    client.lookup_price(name="Charizard", set_name="Base Set", card_number="4")
    url, params = client._session.calls[-1]
    assert url.endswith("/api/product")
    assert params["q"] == "Charizard Base Set 4"
    assert params["t"] == "x" * 40


def test_lookup_by_product_id_uses_id_param():
    client = _client_with(SAMPLE)
    client.find_product(product_id="12345")
    _url, params = client._session.calls[-1]
    assert params["id"] == "12345"
    assert "q" not in params


def test_prices_maps_all_grade_tiers():
    client = _client_with(SAMPLE)
    grades = client.prices(SAMPLE)
    assert grades["ungraded"] == 325.00
    assert grades["grade9"] == 1200.00
    assert grades["psa10"] == 3500.00
    assert grades["bgs10"] == 9000.00
    # Tiers absent from the payload come back as None, not 0.
    assert grades["cgc10"] is None
    assert grades["sgc10"] is None


@pytest.mark.parametrize("raw,expected", [(0, None), (-100, None), (None, None), ("17244", 172.44)])
def test_pennies_to_usd_edge_cases(raw, expected):
    assert PriceChartingClient._pennies_to_usd(raw) == expected


def test_non_success_status_returns_none():
    client = _client_with({"status": "error", "error-message": "no token"})
    assert client.find_product(name="Charizard") is None
    assert client.lookup_price(name="Charizard") == (None, None)


def test_empty_query_makes_no_request():
    client = _client_with(SAMPLE)
    assert client.find_product(name="", set_name=None, card_number=None) is None
    assert client._session.calls == []
