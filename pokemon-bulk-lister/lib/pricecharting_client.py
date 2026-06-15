"""PriceCharting API client — an ungraded + graded price source.

PriceCharting's number is an eBay-sold-median (a 2-week median, with dynamic
1/3/7/14-day windows for high-volume items and outlier filtering), so it
complements our TCGPlayer / Cardmarket / eBay-30d mix rather than duplicating
any single one of them. It also exposes graded tiers (PSA/BGS/CGC/SGC) that none
of the other sources give us — useful later for premium, slabbed cards.

Access requires a paid (Legendary-tier) subscription. Put the 40-character API
token in ``PRICECHARTING_API_TOKEN``; with no token the client is inert
(``enabled`` is False and lookups return ``None``) so the pipeline keeps working
unchanged until you opt in.

⚠️  DATA LICENSE: PriceCharting price data may be used for *internal business
purposes* only and must NOT be displayed to third parties or the general public
without express written permission from PriceCharting. Keep this source out of
any public-facing UI; internal/derived use (our aggregation) is fine.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests


BASE_URL = "https://www.pricecharting.com"

# PriceCharting reuses its video-game price fields for trading cards. This is the
# documented trading-card field mapping; all values are returned as integer
# pennies (e.g. 17244 == $172.44).
GRADE_FIELDS: dict[str, str] = {
    "ungraded": "loose-price",
    "grade7": "cib-price",
    "grade8": "new-price",
    "grade9": "graded-price",
    "grade9.5": "box-only-price",
    "psa10": "manual-only-price",
    "bgs10": "bgs-10-price",
    "cgc10": "condition-17-price",
    "sgc10": "condition-18-price",
}

# The comp that lines up with our other (raw, NM) sources.
UNGRADED_FIELD = GRADE_FIELDS["ungraded"]


class PriceChartingError(RuntimeError):
    pass


class PriceChartingClient:
    def __init__(self, token: Optional[str] = None, timeout: int = 15) -> None:
        self.token = token or os.getenv("PRICECHARTING_API_TOKEN") or ""
        self.timeout = timeout
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        """True only when a token is configured. Callers can branch on this, or
        just call lookup_price() unconditionally — it no-ops when disabled."""
        return bool(self.token)

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "t": self.token}
        url = f"{BASE_URL}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(0.5 + attempt * 0.5)
                continue
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        if last_exc is not None:
            raise last_exc
        return {}

    def find_product(
        self,
        name: str = "",
        set_name: Optional[str] = None,
        card_number: Optional[str] = None,
        product_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Resolve a single PriceCharting product, by id or best-match search.

        Returns the raw product dict (``status == "success"``) or None.
        """
        if not self.enabled:
            return None

        if product_id:
            data = self._get("/api/product", {"id": product_id})
        else:
            query = " ".join(p for p in [name, set_name, card_number] if p).strip()
            if not query:
                return None
            data = self._get("/api/product", {"q": query})

        if not data or data.get("status") != "success":
            return None
        return data

    @staticmethod
    def _pennies_to_usd(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            cents = int(value)
        except (TypeError, ValueError):
            return None
        if cents <= 0:
            return None
        return round(cents / 100.0, 2)

    def prices(self, product: Optional[dict]) -> dict[str, Optional[float]]:
        """All known grade tiers for a product, converted pennies -> USD."""
        return {
            label: self._pennies_to_usd((product or {}).get(field))
            for label, field in GRADE_FIELDS.items()
        }

    def lookup_price(
        self,
        name: str = "",
        set_name: Optional[str] = None,
        card_number: Optional[str] = None,
        product_id: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[dict]]:
        """Return (ungraded_usd, product).

        The ungraded ("loose") price is the comp that matches our other raw-card
        sources; the full product dict is returned too so callers can pull graded
        tiers via ``prices()`` if they need them.
        """
        product = self.find_product(
            name=name, set_name=set_name, card_number=card_number, product_id=product_id
        )
        if not product:
            return None, None
        return self._pennies_to_usd(product.get(UNGRADED_FIELD)), product
