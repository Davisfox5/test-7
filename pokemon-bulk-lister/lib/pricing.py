"""Price aggregation, outlier detection, and confidence scoring."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional


OUTLIER_MULTIPLIER = 2.5
LOW_CONFIDENCE_THRESHOLD = 0.6
TIGHT_SPREAD_RATIO = 0.20


@dataclass
class PricingResult:
    price: Optional[float]
    sources: dict[str, Optional[float]]
    confidence: float
    outlier_flag: bool
    needs_review: bool
    notes: str


def _non_null(values: list[Optional[float]]) -> list[float]:
    return [v for v in values if v is not None and v > 0]


def aggregate(
    tcgplayer_market: Optional[float],
    ebay_median_30d: Optional[float],
    ebay_max_30d: Optional[float],
    cardmarket_trend_usd: Optional[float] = None,
    terapeak_median_usd: Optional[float] = None,
) -> PricingResult:
    """Aggregate prices from multiple sources.

    Rule:
        prices = [all non-null source prices]
        median = statistics.median(prices)
        candidate = max(prices)
        if candidate > 2.5 * median: use second-highest, flag outlier
        else: use candidate

    Cardmarket trend price should already be FX-converted to USD by the caller.
    Terapeak gives a longer (~365-day) window than the eBay 30-day stats.
    """
    sources: dict[str, Optional[float]] = {
        "tcgplayer_market": tcgplayer_market,
        "ebay_median_30d": ebay_median_30d,
        "ebay_max_30d": ebay_max_30d,
        "cardmarket_trend_usd": cardmarket_trend_usd,
        "terapeak_median_usd": terapeak_median_usd,
    }
    prices = [
        tcgplayer_market,
        ebay_median_30d,
        ebay_max_30d,
        cardmarket_trend_usd,
        terapeak_median_usd,
    ]
    valid = _non_null(prices)

    if not valid:
        return PricingResult(
            price=None,
            sources=sources,
            confidence=0.0,
            outlier_flag=False,
            needs_review=True,
            notes="no price sources available",
        )

    median = statistics.median(valid)
    candidate = max(valid)
    outlier_flag = False
    notes = ""

    if len(valid) >= 2 and candidate > OUTLIER_MULTIPLIER * median:
        sorted_desc = sorted(valid, reverse=True)
        price = sorted_desc[1]
        outlier_flag = True
        notes = f"max {candidate:.2f} > {OUTLIER_MULTIPLIER}x median {median:.2f}; using second-highest"
    else:
        price = candidate

    confidence = _confidence(valid)
    needs_review = confidence < LOW_CONFIDENCE_THRESHOLD or outlier_flag

    return PricingResult(
        price=round(price, 2),
        sources=sources,
        confidence=round(confidence, 3),
        outlier_flag=outlier_flag,
        needs_review=needs_review,
        notes=notes,
    )


def _confidence(values: list[float]) -> float:
    """1.0 if all sources within 20% of each other; decreasing as spread widens.

    Single-source falls back to 0.5 (we have a number but nothing to corroborate it).
    """
    if len(values) == 0:
        return 0.0
    if len(values) == 1:
        return 0.5

    lo, hi = min(values), max(values)
    if lo <= 0:
        return 0.0
    spread = (hi - lo) / lo

    if spread <= TIGHT_SPREAD_RATIO:
        return 1.0
    # Linearly decay: spread 0.2 -> 1.0, spread 1.2 -> 0.0.
    decayed = 1.0 - (spread - TIGHT_SPREAD_RATIO) / 1.0
    return max(0.0, min(1.0, decayed))
