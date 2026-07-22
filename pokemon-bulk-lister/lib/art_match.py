"""Image-based card identification — no AI call needed when it hits.

Two layers share one representation and one nearest-neighbour search:

  * ``ArtIndex`` — descriptors of the official card images from pokemontcg.io
    (built once by ``scripts/08_build_art_index.py``). A confident match
    identifies the card outright, like commercial scanner apps do.
  * ``LearnedCache`` — descriptors of crops we already identified (by AI or a
    catalog match). Duplicate cards and re-uploads hit this for free.

How a crop is described (validated against real through-sleeve binder photos;
see docs/productization_review.md for the experiment):

  1. ``locate_card`` segments the card from the dark binder background and
     perspective-warps it to a canonical 200x280 frame. Without this step
     framing offsets dominate any descriptor.
  2. The warped card is blurred and downsampled to 12x12 **color** pixels,
     then contrast-normalized (zero mean, unit norm). Color matters: card
     frames all look alike in grayscale.
  3. Queries also generate slightly reframed "jitter" views to absorb
     residual localization error; the best view per candidate counts.

Matching is cosine distance with a double gate: the best candidate must be
close in absolute terms AND clearly ahead of the nearest *different* card
(same name+number entries — variants/reprints with identical art — don't
count against the margin). Same-art reprints from different sets therefore
tie, fail the margin, and correctly fall through to the AI, which can read
the collector number. Anything ambiguous falls through too — enabling this
layer can only reduce cost, never quality. On validation it accepted ~half
of real binder crops with zero wrong accepts (it even corrected two wrong
AI identifications).

Thresholds (cosine distance, env-tunable): ART_DIST_MAX / ART_MARGIN for the
catalog, ART_CACHE_DIST_MAX / ART_CACHE_MARGIN for the learned cache.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

DESC_SIZE = 12                     # descriptor is DESC_SIZE² x 3 color pixels
DESC_LEN = DESC_SIZE * DESC_SIZE * 3
CANON_W, CANON_H = 200, 280        # canonical warped-card frame

# Catalog acceptance (cosine distance).
DIST_MAX = float(os.getenv("ART_DIST_MAX", "0.15"))
MARGIN_MIN = float(os.getenv("ART_MARGIN", "0.05"))

# Learned-cache acceptance: same card re-photographed under the same setup
# sits far closer than photo-vs-official-scan, so demand a tighter distance.
CACHE_DIST_MAX = float(os.getenv("ART_CACHE_DIST_MAX", "0.10"))
CACHE_MARGIN_MIN = float(os.getenv("ART_CACHE_MARGIN", "0.05"))


# ----------------------------------------------------------------------
# Card localization + descriptor
# ----------------------------------------------------------------------

def locate_card(img: np.ndarray) -> np.ndarray:
    """Segment the card from the (dark) binder background and warp it to the
    canonical frame. Falls back to a light edge-trim when nothing card-shaped
    is found (e.g. the crop is already tight, or the background is bright)."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    # Card pixels: bright, or moderately bright AND colorful. Binder pocket:
    # near-black with tiny white sparkle specks (removed by the opening).
    mask = ((val > 120) | ((val > 60) & (sat > 60))).astype(np.uint8) * 255
    k = max(3, int(min(h, w) * 0.02)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    k2 = max(9, int(min(h, w) * 0.06)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k2, k2), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.25 * h * w:
            continue
        rect = cv2.minAreaRect(c)
        rw, rh = rect[1]
        if not rw or not rh:
            continue
        aspect = min(rw, rh) / max(rw, rh)
        if not (0.55 <= aspect <= 0.88):   # a Pokémon card is ~0.716
            continue
        if best is None or area > best[0]:
            best = (area, rect)
    if best is None:
        dy, dx = int(h * 0.04), int(w * 0.04)
        return cv2.resize(img[dy: h - dy, dx: w - dx], (CANON_W, CANON_H),
                          interpolation=cv2.INTER_AREA)

    box = cv2.boxPoints(best[1]).astype(np.float32)
    sums = box.sum(axis=1)
    diffs = np.diff(box, axis=1).ravel()
    src = np.array([box[np.argmin(sums)], box[np.argmin(diffs)],
                    box[np.argmax(sums)], box[np.argmax(diffs)]], dtype=np.float32)
    dst = np.array([[0, 0], [CANON_W - 1, 0], [CANON_W - 1, CANON_H - 1],
                    [0, CANON_H - 1]], dtype=np.float32)
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst),
                               (CANON_W, CANON_H))


def descriptor(card_bgr: np.ndarray) -> np.ndarray:
    """Contrast-normalized 12x12 color thumbnail of a located card."""
    img = card_bgr if card_bgr.ndim == 3 else cv2.cvtColor(card_bgr, cv2.COLOR_GRAY2BGR)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    small = cv2.resize(img, (DESC_SIZE, DESC_SIZE), interpolation=cv2.INTER_AREA)
    x = small.astype(np.float32).flatten()
    x -= x.mean()
    norm = float(np.linalg.norm(x))
    return x / (norm if norm > 1e-6 else 1.0)


def _load_image(image_or_path: Any) -> Optional[np.ndarray]:
    if isinstance(image_or_path, np.ndarray):
        return image_or_path
    img = cv2.imread(str(image_or_path))
    return img if img is not None and img.size else None


def query_views(image_or_path: Any) -> Optional[np.ndarray]:
    """Descriptors of the located card plus jittered reframings (V x DESC_LEN).

    The jitter views absorb residual localization error; matching takes the
    best view per candidate. Returns None if the image is unreadable."""
    img = _load_image(image_or_path)
    if img is None:
        return None
    card = locate_card(img)
    h, w = card.shape[:2]
    views = [card]
    for f in (0.03, 0.06):
        dy, dx = int(h * f), int(w * f)
        views.append(card[dy: h - dy, dx: w - dx])   # zoom in
        views.append(card[0: h - 2 * dy, 0: w - 2 * dx])  # shift toward TL
        views.append(card[2 * dy: h, 2 * dx: w])          # shift toward BR
    return np.stack([descriptor(v) for v in views])


def index_descriptor(image_or_path: Any) -> Optional[np.ndarray]:
    """Single descriptor for indexing (official image or identified crop)."""
    img = _load_image(image_or_path)
    if img is None:
        return None
    return descriptor(locate_card(img))


# ----------------------------------------------------------------------
# Index
# ----------------------------------------------------------------------

class ArtIndex:
    """Nearest-neighbour store over card descriptors with metadata."""

    def __init__(self) -> None:
        self._rows: list[np.ndarray] = []
        self.meta: list[dict] = []
        self._mat: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.meta)

    def add(self, desc: np.ndarray, meta: dict) -> None:
        with self._lock:
            self._rows.append(desc.astype(np.float32))
            self.meta.append(meta)
            self._mat = None

    def _matrix(self) -> tuple[np.ndarray, list[dict]]:
        with self._lock:
            if self._mat is None:
                self._mat = np.stack(self._rows) if self._rows else \
                    np.zeros((0, DESC_LEN), dtype=np.float32)
            return self._mat, self.meta

    # -- persistence -----------------------------------------------------

    def save(self, dir_path: Any) -> None:
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        mat, meta = self._matrix()
        np.savez_compressed(dir_path / "index.npz", x=mat.astype(np.float16))
        tmp = dir_path / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(dir_path / "meta.json")

    @classmethod
    def load(cls, dir_path: Any) -> "ArtIndex":
        dir_path = Path(dir_path)
        idx = cls()
        npz_path, meta_path = dir_path / "index.npz", dir_path / "meta.json"
        if not (npz_path.exists() and meta_path.exists()):
            return idx
        try:
            with np.load(npz_path) as z:
                mat = z["x"].astype(np.float32)
            meta = json.loads(meta_path.read_text())
        except Exception:
            return idx  # corrupt index behaves like an empty one
        if len(meta) == mat.shape[0] and (mat.shape[0] == 0 or mat.shape[1] == DESC_LEN):
            idx._rows = list(mat)
            idx.meta = meta
        return idx

    # -- matching ----------------------------------------------------------

    def match(
        self,
        views: np.ndarray,
        dist_max: float = DIST_MAX,
        margin_min: float = MARGIN_MIN,
    ) -> Optional[dict]:
        """Best confident match for a stack of query views, or None.

        Accepts only when the closest entry is within ``dist_max`` cosine
        distance AND the nearest entry that is a *different card* (different
        name+number) is at least ``margin_min`` further away. Entries sharing
        name+number (variants with identical art) don't count against the
        margin; same-name reprints from other sets DO, so ambiguous reprints
        fall through to the AI, which can read the collector number.
        """
        mat, meta = self._matrix()
        if not meta:
            return None
        if views.ndim == 1:
            views = views[np.newaxis, :]
        # errstate: Apple's Accelerate BLAS raises spurious FP warnings on
        # strided float32 matmuls; there is no real division here.
        with np.errstate(all="ignore"):
            sims = (views @ mat.T).max(axis=0)   # best view per candidate
        best = int(np.argmax(sims))
        best_dist = 1.0 - float(sims[best])
        if best_dist > dist_max:
            return None

        best_key = (meta[best].get("name"), meta[best].get("card_number"))
        margin = None
        for j in np.argsort(-sims)[1:]:
            m = meta[int(j)]
            if (m.get("name"), m.get("card_number")) != best_key:
                margin = float(sims[best] - sims[int(j)])
                break
        if margin is not None and margin < margin_min:
            return None

        return {"meta": dict(meta[best]), "distance": round(best_dist, 4),
                "margin": None if margin is None else round(margin, 4)}


# ----------------------------------------------------------------------
# Learned cache (previously identified crops)
# ----------------------------------------------------------------------

class LearnedCache:
    """Append-only JSONL of identified-crop descriptors; tight-bound matching."""

    def __init__(self, path: Any) -> None:
        self.path = Path(path)
        self._index = ArtIndex()
        self._io_lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    desc = np.frombuffer(bytes.fromhex(rec["x"]), dtype=np.float16)
                    if desc.shape[0] != DESC_LEN:
                        continue
                    self._index.add(desc.astype(np.float32), rec["meta"])
                except Exception:
                    continue  # skip torn/corrupt lines

    def __len__(self) -> int:
        return len(self._index)

    def add(self, desc: np.ndarray, meta: dict) -> None:
        self._index.add(desc, meta)
        line = json.dumps({"x": desc.astype(np.float16).tobytes().hex(), "meta": meta})
        with self._io_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(line + "\n")

    def match(self, views: np.ndarray) -> Optional[dict]:
        return self._index.match(views, dist_max=CACHE_DIST_MAX,
                                 margin_min=CACHE_MARGIN_MIN)
