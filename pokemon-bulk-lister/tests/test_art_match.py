"""Tests for lib/art_match — locate, descriptors, index matching, learned cache."""
from __future__ import annotations

import cv2
import numpy as np

from lib.art_match import (
    CANON_H,
    CANON_W,
    ArtIndex,
    LearnedCache,
    descriptor,
    index_descriptor,
    locate_card,
    query_views,
)


def _fake_card(seed: int, size: tuple[int, int] = (280, 200)) -> np.ndarray:
    """Deterministic structured 'card art' — smooth colorful blobs."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (14, 10, 3), dtype=np.uint8)
    img = cv2.resize(small, (size[1], size[0]), interpolation=cv2.INTER_CUBIC)
    # keep it bright enough to read as "card" for locate_card
    return np.clip(img.astype(np.int16) + 80, 0, 255).astype(np.uint8)


def _in_binder(card: np.ndarray, pad: float = 0.15) -> np.ndarray:
    """Paste the card off-center on a dark binder-like background."""
    h, w = card.shape[:2]
    H, W = int(h * (1 + 2 * pad)), int(w * (1 + 2 * pad))
    rng = np.random.default_rng(0)
    bg = (rng.random((H, W, 3)) * 25).astype(np.uint8)  # near-black + noise
    y, x = int(h * pad * 1.6), int(w * pad * 0.4)       # asymmetric offset
    bg[y: y + h, x: x + w] = card
    return bg


def _photo_like(img: np.ndarray) -> np.ndarray:
    """Same card as a 'phone photo': rescaled, brighter, noisy."""
    h, w = img.shape[:2]
    big = cv2.resize(img, (int(w * 1.7), int(h * 1.7)), interpolation=cv2.INTER_LINEAR)
    rng = np.random.default_rng(1)
    noisy = big.astype(np.int16) + 18 + rng.integers(-10, 11, big.shape, dtype=np.int16)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def test_locate_card_finds_offset_card():
    card = _fake_card(0)
    located = locate_card(_in_binder(card))
    assert located.shape[:2] == (CANON_H, CANON_W)
    # located result should resemble the card, not the binder background
    d_card = 1 - float(np.dot(descriptor(located), descriptor(card)))
    assert d_card < 0.15, f"located card diverges from original ({d_card:.3f})"


def test_descriptor_is_unit_normalized():
    d = descriptor(_fake_card(1))
    assert d.shape == (12 * 12 * 3,)
    assert abs(float(np.linalg.norm(d)) - 1.0) < 1e-4


def test_query_views_shape():
    views = query_views(_in_binder(_fake_card(2)))
    assert views is not None
    assert views.ndim == 2 and views.shape[1] == 12 * 12 * 3
    assert views.shape[0] >= 5


def _build_index(n: int = 50) -> ArtIndex:
    idx = ArtIndex()
    for i in range(n):
        idx.add(index_descriptor(_fake_card(100 + i)), {
            "name": f"Card {i}", "card_number": str(i), "set_code": "tst",
            "set_name": "Test Set", "rarity": "Common", "is_holo": False,
        })
    return idx


def test_index_matches_binder_photo_of_known_card():
    idx = _build_index()
    query = _photo_like(_in_binder(_fake_card(117)))  # card 17
    res = idx.match(query_views(query))
    assert res is not None
    assert res["meta"]["name"] == "Card 17"


def test_index_rejects_unknown_card():
    idx = _build_index()
    unknown = _photo_like(_in_binder(_fake_card(999)))
    assert idx.match(query_views(unknown)) is None


def test_same_art_variants_do_not_kill_margin():
    """Entries with identical art AND same name+number (variants) must not
    make the margin gate reject; a same-art entry with a DIFFERENT number
    (reprint in another set) must."""
    art = _fake_card(50)
    idx = ArtIndex()
    for set_code in ("tst1", "tst2"):
        idx.add(index_descriptor(art), {"name": "Pikachu", "card_number": "25", "set_code": set_code})
    for i in range(10):
        idx.add(index_descriptor(_fake_card(200 + i)), {"name": f"Other {i}", "card_number": str(i)})
    views = query_views(_photo_like(_in_binder(art)))
    res = idx.match(views)
    assert res is not None and res["meta"]["name"] == "Pikachu"

    # now add a same-art reprint under a different number → ambiguity → reject
    idx.add(index_descriptor(art), {"name": "Pikachu", "card_number": "199", "set_code": "tst3"})
    assert idx.match(views) is None


def test_index_save_load_roundtrip(tmp_path):
    idx = _build_index(10)
    idx.save(tmp_path / "idx")
    loaded = ArtIndex.load(tmp_path / "idx")
    assert len(loaded) == 10
    res = loaded.match(query_views(_in_binder(_fake_card(105))))
    assert res is not None and res["meta"]["name"] == "Card 5"


def test_load_missing_dir_gives_empty_index(tmp_path):
    idx = ArtIndex.load(tmp_path / "nope")
    assert len(idx) == 0
    assert idx.match(query_views(_fake_card(0))) is None


def test_learned_cache_roundtrip(tmp_path):
    path = tmp_path / "learned.jsonl"
    cache = LearnedCache(path)
    crop = _in_binder(_fake_card(7))
    cache.add(index_descriptor(crop), {"name": "Charmander", "card_number": "4"})
    assert cache.match(query_views(crop))["meta"]["name"] == "Charmander"
    assert cache.match(query_views(_in_binder(_fake_card(8)))) is None
    # reload from disk
    cache2 = LearnedCache(path)
    assert len(cache2) == 1
    assert cache2.match(query_views(crop))["meta"]["name"] == "Charmander"


def test_learned_cache_survives_corrupt_line(tmp_path):
    path = tmp_path / "learned.jsonl"
    cache = LearnedCache(path)
    cache.add(index_descriptor(_fake_card(9)), {"name": "Squirtle"})
    with path.open("a") as fh:
        fh.write("{torn json\n")
    assert len(LearnedCache(path)) == 1


def test_query_views_unreadable_returns_none(tmp_path):
    bad = tmp_path / "not_an_image.jpg"
    bad.write_text("hello")
    assert query_views(bad) is None
