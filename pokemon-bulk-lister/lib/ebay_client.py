"""eBay client — OAuth client-credentials + Browse API + Marketplace Insights API.

Marketplace Insights returns sold-item data (last 90 days). We filter to 30 days
and Near Mint English Pokémon TCG singles.
"""
from __future__ import annotations

import base64
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests


PRODUCTION_BASE = "https://api.ebay.com"
SANDBOX_BASE = "https://api.sandbox.ebay.com"

POKEMON_CATEGORY_ID = "183454"  # Collectible Card Games > Pokémon TCG > Individual Cards
SCOPES = " ".join(
    [
        "https://api.ebay.com/oauth/api_scope",
        "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights",
    ]
)


class EbayAuthError(RuntimeError):
    pass


class EbayClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        marketplace_id: Optional[str] = None,
        env: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET", "")
        self.marketplace_id = marketplace_id or os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
        env = (env or os.getenv("EBAY_ENV", "production")).lower()
        self.base_url = SANDBOX_BASE if env == "sandbox" else PRODUCTION_BASE
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._session = requests.Session()

    def _auth_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        if not self.client_id or not self.client_secret:
            raise EbayAuthError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        resp = self._session.post(
            f"{self.base_url}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": SCOPES},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise EbayAuthError(f"OAuth failed {resp.status_code}: {resp.text}")
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + int(payload.get("expires_in", 7200))
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._auth_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Content-Type": "application/json",
        }

    def search_sold(
        self,
        query: str,
        days: int = 30,
        condition: str = "NEW",
        limit: int = 50,
    ) -> list[dict]:
        """Search Marketplace Insights for sold listings.

        eBay condition values: NEW, LIKE_NEW, USED_EXCELLENT, USED_VERY_GOOD, USED_GOOD.
        For ungraded NM cards we use NEW. Graded cards have their own filters.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filters = [
            f"categoryIds:{{{POKEMON_CATEGORY_ID}}}",
            f"conditions:{{{condition}}}",
            f"itemLocationCountry:US",
            f"lastSoldDate:[{cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')}..]",
        ]
        params = {
            "q": query,
            "filter": ",".join(filters),
            "limit": str(min(limit, 200)),
        }
        url = f"{self.base_url}/buy/marketplace_insights/v1_beta/item_sales/search"
        resp = self._session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        if resp.status_code == 401:
            self._token = None
            resp = self._session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        if resp.status_code == 403:
            raise EbayAuthError(
                "Marketplace Insights returned 403 — your app probably isn't approved for "
                "the buy.marketplace.insights scope. Apply at developer.ebay.com."
            )
        resp.raise_for_status()
        return resp.json().get("itemSales") or []

    def sold_stats(
        self,
        query: str,
        days: int = 30,
        condition: str = "NEW",
    ) -> dict:
        """Return median / max / count of sold listings for a query."""
        items = self.search_sold(query=query, days=days, condition=condition)
        prices: list[float] = []
        for it in items:
            price = (it.get("lastSoldPrice") or {}).get("value")
            currency = (it.get("lastSoldPrice") or {}).get("currency", "USD")
            if price is None or currency != "USD":
                continue
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                continue

        if not prices:
            return {"median": None, "max": None, "min": None, "count": 0}

        return {
            "median": round(statistics.median(prices), 2),
            "max": round(max(prices), 2),
            "min": round(min(prices), 2),
            "count": len(prices),
        }

    def build_query(
        self,
        name: str,
        set_name: Optional[str] = None,
        card_number: Optional[str] = None,
    ) -> str:
        parts = [name]
        if set_name:
            parts.append(set_name)
        if card_number:
            parts.append(card_number)
        parts.extend(["pokemon", "english"])
        return " ".join(p for p in parts if p)
