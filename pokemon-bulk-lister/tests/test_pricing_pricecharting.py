"""PriceCharting participates in aggregation like any other source."""
from __future__ import annotations

from lib.pricing import aggregate


def test_pricecharting_appears_in_sources():
    result = aggregate(
        tcgplayer_market=None,
        ebay_median_30d=None,
        ebay_max_30d=None,
        pricecharting_usd=12.50,
    )
    assert "pricecharting_usd" in result.sources
    assert result.sources["pricecharting_usd"] == 12.50
    assert result.price == 12.50  # single source still produces a price


def test_pricecharting_counts_toward_corroboration():
    # Two sources that agree -> high confidence (vs. 0.5 for a lone source).
    lone = aggregate(tcgplayer_market=10.0, ebay_median_30d=None, ebay_max_30d=None)
    corroborated = aggregate(
        tcgplayer_market=10.0,
        ebay_median_30d=None,
        ebay_max_30d=None,
        pricecharting_usd=10.5,
    )
    assert lone.confidence == 0.5
    assert corroborated.confidence > lone.confidence


def test_pricecharting_outlier_is_rejected():
    # PriceCharting wildly high vs. three agreeing sources -> flagged, not used.
    result = aggregate(
        tcgplayer_market=10.0,
        ebay_median_30d=10.0,
        ebay_max_30d=11.0,
        pricecharting_usd=500.0,
    )
    assert result.outlier_flag is True
    assert result.price != 500.0
