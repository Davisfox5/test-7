"""Step 1 — turn binder-page photos into clean per-card crops.

Two strategies. We try the smart one first, fall back to the grid one if it
doesn't find 9 cards.

1. **Per-card detection (preferred)**: edge-detect each card individually,
   find its 4 corners, perspective-warp it to a clean rectangle. Works on
   angled / skewed photos because each card is rectified on its own.

2. **Grid fallback**: divide the binder page into a 3x3 grid. Used when the
   per-card detector can't find at least N cards (likely a heavily occluded
   or low-contrast image).

Usage:
    python scripts/01_split_grids.py [--input input/grids] [--output output/crops]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}
EMPTY_POCKET_STDDEV = 12.0

# Per-card detection thresholds.
CARD_ASPECT = 6.3 / 8.8                   # Pokémon TCG card width / height
CARD_ASPECT_TOL = 0.30                    # accept aspect within ±30% of nominal
MIN_CARD_AREA_FRACTION = 0.02             # smallest plausible card is 2% of frame
MAX_CARD_AREA_FRACTION = 0.20             # largest plausible card is 20% of frame
OUTPUT_CARD_W = 480                       # output crop width in px (height set by aspect)
DETECTOR_MIN_HITS = 6                     # need at least this many cards to trust detector

# Grid fallback thresholds.
MIN_PAGE_FRACTION = 0.6                   # detected bbox must cover ≥60% of image,
                                          # else we use the whole image as the page


# ---------------------------------------------------------------------------
# Per-card detection + perspective warp
# ---------------------------------------------------------------------------

def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Return 4 corners in TL, TR, BR, BL order."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]   # top-left:     smallest x+y
    ordered[2] = pts[np.argmax(s)]   # bottom-right: largest x+y
    ordered[1] = pts[np.argmin(d)]   # top-right:    smallest y-x
    ordered[3] = pts[np.argmax(d)]   # bottom-left:  largest y-x
    return ordered


def _warp_card(img: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Perspective-warp a quad to a clean upright rectangle."""
    ordered = _order_corners(corners)
    out_w = OUTPUT_CARD_W
    out_h = int(OUTPUT_CARD_W / CARD_ASPECT)
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(img, M, (out_w, out_h))


def _approx_quad(contour: np.ndarray) -> Optional[np.ndarray]:
    """Approximate a contour as a 4-point polygon, or None if it doesn't reduce to one."""
    peri = cv2.arcLength(contour, True)
    for eps_frac in (0.02, 0.03, 0.04, 0.05):
        approx = cv2.approxPolyDP(contour, eps_frac * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx
    return None


def detect_cards(img: np.ndarray) -> list[np.ndarray]:
    """Find every Pokémon-card-shaped quadrilateral in the image.

    Returns a list of corner arrays (one per detected card), de-duplicated by
    centroid proximity.
    """
    img_h, img_w = img.shape[:2]
    frame_area = img_h * img_w
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Adaptive threshold handles uneven lighting (cards under binder plastic).
    thresh = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        25, 5,
    )
    # Also try Canny edges as a second signal.
    edges = cv2.Canny(blurred, 50, 150)
    combined = cv2.bitwise_or(thresh, edges)
    # Close small gaps in the card outlines.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    cards: list[tuple[float, np.ndarray, tuple[float, float]]] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < frame_area * MIN_CARD_AREA_FRACTION:
            continue
        if area > frame_area * MAX_CARD_AREA_FRACTION:
            continue
        quad = _approx_quad(cnt)
        if quad is None:
            continue
        # Reject non-rectangular shapes by aspect ratio of the minimum bounding rect.
        rect = cv2.minAreaRect(quad)
        (cx, cy), (rw, rh), _ = rect
        if rw == 0 or rh == 0:
            continue
        aspect = min(rw, rh) / max(rw, rh)
        nominal = CARD_ASPECT
        if abs(aspect - nominal) > CARD_ASPECT_TOL:
            continue
        # Reject if the quad's contour area is too different from the minAreaRect area
        # (rules out wildly skewed parallelograms).
        if cv2.contourArea(quad) < 0.7 * rw * rh:
            continue
        cards.append((area, quad, (cx, cy)))

    # Deduplicate: keep the largest contour among ones whose centroids are within
    # half a card's worth of each other.
    if not cards:
        return []
    cards.sort(key=lambda t: -t[0])
    kept: list[tuple[float, np.ndarray, tuple[float, float]]] = []
    avg_side = (frame_area * 0.05) ** 0.5
    for area, quad, (cx, cy) in cards:
        dup = False
        for _, _, (kx, ky) in kept:
            if (cx - kx) ** 2 + (cy - ky) ** 2 < (avg_side * 0.5) ** 2:
                dup = True
                break
        if not dup:
            kept.append((area, quad, (cx, cy)))
    return [q for _, q, _ in kept]


def assign_to_grid(quads: list[np.ndarray], img_shape: tuple[int, int]) -> list[Optional[np.ndarray]]:
    """Assign detected cards to 3x3 grid slots by centroid position.

    Returns a 9-long list in row-major order. None where no detection fell into
    that slot.
    """
    img_h, img_w = img_shape[:2]
    slots: list[Optional[np.ndarray]] = [None] * 9
    if not quads:
        return slots

    # Bucket by y into 3 rows, then by x into 3 cols.
    centroids = [(q.reshape(4, 2).mean(axis=0), q) for q in quads]
    centroids.sort(key=lambda t: t[0][1])  # sort by y
    rows: list[list[tuple[np.ndarray, np.ndarray]]] = [[], [], []]
    for i, (c, q) in enumerate(centroids):
        # Bucket by relative y. Use absolute thirds of the frame to handle missing cards.
        bucket = min(2, int(c[1] / (img_h / 3)))
        rows[bucket].append((c, q))

    for r, row in enumerate(rows):
        row.sort(key=lambda t: t[0][0])  # sort by x within row
        for (c, q) in row:
            col = min(2, int(c[0] / (img_w / 3)))
            idx = r * 3 + col
            if slots[idx] is None:
                slots[idx] = q
    return slots


# ---------------------------------------------------------------------------
# Grid fallback (the prior algorithm)
# ---------------------------------------------------------------------------

def find_page_bbox(img: np.ndarray) -> tuple[int, int, int, int]:
    img_h, img_w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(15, img_w // 30), max(15, img_h // 30)),
    )
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0, 0, img_w, img_h
    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)
    if w < img_w * MIN_PAGE_FRACTION or h < img_h * MIN_PAGE_FRACTION:
        return 0, 0, img_w, img_h
    pad_x, pad_y = int(w * 0.01), int(h * 0.01)
    x = max(0, x + pad_x)
    y = max(0, y + pad_y)
    w = min(img_w - x, w - 2 * pad_x)
    h = min(img_h - y, h - 2 * pad_y)
    return x, y, w, h


def auto_orient(img: np.ndarray) -> np.ndarray:
    _, _, w, h = find_page_bbox(img)
    if w > h:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def cell_is_empty(cell: np.ndarray) -> bool:
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    return float(np.std(gray)) < EMPTY_POCKET_STDDEV


def split_grid(img: np.ndarray, *, skip_bbox: bool = False) -> list[Optional[np.ndarray]]:
    if skip_bbox:
        x, y = 0, 0
        h, w = img.shape[:2]
    else:
        x, y, w, h = find_page_bbox(img)
    page = img[y : y + h, x : x + w]
    cell_w, cell_h = w // 3, h // 3
    mx, my = int(cell_w * 0.04), int(cell_h * 0.04)
    cells: list[Optional[np.ndarray]] = []
    for r in range(3):
        for c in range(3):
            cell = page[r * cell_h + my : (r + 1) * cell_h - my,
                        c * cell_w + mx : (c + 1) * cell_w - mx]
            cells.append(None if cell.size == 0 or cell_is_empty(cell) else cell)
    return cells


# ---------------------------------------------------------------------------
# Page-corner detection + perspective-correct, then 3x3 split
# ---------------------------------------------------------------------------

def find_page_quad(img: np.ndarray) -> Optional[np.ndarray]:
    """Find a 4-corner quad enclosing the binder page.

    Strategy: Otsu threshold → close gaps between cards so the page is one
    contour → take the minimum-area rotated rectangle of the largest contour.
    minAreaRect always returns exactly 4 corners and handles rotation (the
    common skew on hand-held binder photos) directly.
    """
    img_h, img_w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(20, img_w // 25), max(20, img_h // 25)),
    )
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 0.4 * img_w * img_h:
        return None  # the largest contour isn't the page — bail

    rect = cv2.minAreaRect(cnt)
    (_, _), (rw, rh), _ = rect
    if rw < 0.4 * img_w and rh < 0.4 * img_w:
        return None  # implausibly small rotated rect

    box = cv2.boxPoints(rect).astype(np.float32)
    return box.reshape(4, 1, 2)


def warp_page(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Perspective-warp the binder page to a clean upright rectangle.

    Output aspect mirrors a 3x3 grid of Pokémon cards (≈ 3·(6.3/8.8) wide × 3 tall).
    """
    ordered = _order_corners(quad)
    page_aspect = 3 * CARD_ASPECT / 3  # = CARD_ASPECT (3 wide, 3 tall)
    out_w = 1200
    out_h = int(out_w / page_aspect)
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(img, M, (out_w, out_h))


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def _detect_card_in_cell(cell: np.ndarray) -> Optional[np.ndarray]:
    """Find the actual card within a rough grid cell and return its 4 corners.

    Cards inside binder pockets are often slightly tilted/shifted. This pulls
    each card's own outline so we can warp it to a clean rectangle, instead
    of taking a fixed grid slice that may straddle a card edge.
    """
    h, w = cell.shape[:2]
    if h < 50 or w < 50:
        return None
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    # Try Otsu — card body is brighter than binder plastic / shadow.
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Morph close to merge interior texture into one card blob.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w // 30), max(5, h // 30)))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cell_area = h * w
    # Pick the largest contour that's plausibly card-shaped.
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < cell_area * 0.25:  # cards fill at least 25% of their cell
            continue
        if area > cell_area * 0.98:  # too-big = likely captured borders
            continue
        rect = cv2.minAreaRect(cnt)
        (_, _), (rw, rh), _ = rect
        if min(rw, rh) <= 0:
            continue
        aspect = min(rw, rh) / max(rw, rh)
        # Pokémon card aspect = 6.3 / 8.8 ≈ 0.716. Allow ±25%.
        if not (0.55 <= aspect <= 0.95):
            continue
        candidates.append((area, rect))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[0])
    _, best = candidates[0]
    return cv2.boxPoints(best).astype(np.float32).reshape(4, 1, 2)


def _warp_card_quad(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Perspective-warp a card quad to an upright 480×671 rectangle (6.3:8.8)."""
    ordered = _order_corners(quad)
    out_w = 480
    out_h = int(out_w / CARD_ASPECT)  # 671
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(img, M, (out_w, out_h))


def _split_with_per_card_warp(warped: np.ndarray) -> list[Optional[np.ndarray]]:
    """Divide a warped page into 9 cells, then perspective-warp each card individually."""
    h, w = warped.shape[:2]
    cell_w, cell_h = w // 3, h // 3
    # Small overlap so a card sitting on a cell boundary still gets fully captured.
    overlap = int(min(cell_w, cell_h) * 0.05)
    cells: list[Optional[np.ndarray]] = []
    for r in range(3):
        for c in range(3):
            y0 = max(0, r * cell_h - overlap)
            y1 = min(h, (r + 1) * cell_h + overlap)
            x0 = max(0, c * cell_w - overlap)
            x1 = min(w, (c + 1) * cell_w + overlap)
            cell = warped[y0:y1, x0:x1]
            if cell.size == 0 or cell_is_empty(cell):
                cells.append(None)
                continue
            quad_local = _detect_card_in_cell(cell)
            if quad_local is None:
                # Fallback: just the centered rectangle (no per-card warp).
                cells.append(cell)
                continue
            # Translate quad coords back into the warped-page coordinate space,
            # then warp from the full warped page so we don't run into the
            # cell-overlap edge.
            quad_global = quad_local.copy()
            quad_global[:, 0, 0] += x0
            quad_global[:, 0, 1] += y0
            cells.append(_warp_card_quad(warped, quad_global))
    return cells


def split_page(img: np.ndarray) -> tuple[list[Optional[np.ndarray]], str]:
    """Return (9 crops, method).

    Pipeline:
      1) Page corners → perspective warp the binder page to a clean rectangle.
      2) Inside the warped page, detect each individual card's 4 corners and
         perspective-warp THAT card to an upright 480×671 rectangle, so cards
         tilted in their pockets still come out clean. Method = 'per_card_warp'.
      3) Fall back to a plain 3x3 grid divide if the page can't be located.
         Method = 'grid'.
    """
    quad = find_page_quad(img)
    if quad is not None:
        warped = warp_page(img, quad)
        return _split_with_per_card_warp(warped), "per_card_warp"
    return split_grid(img), "grid"


def process_image(path: Path, out_dir: Path) -> tuple[int, str]:
    img = cv2.imread(str(path))
    if img is None:
        print(f"[warn] could not read {path}", file=sys.stderr)
        return 0, "fail"
    img = auto_orient(img)
    cells, method = split_page(img)
    stem = path.stem
    saved = 0
    for idx, cell in enumerate(cells):
        if cell is None:
            continue
        row, col = divmod(idx, 3)
        out_path = out_dir / f"{stem}_r{row}c{col}.jpg"
        cv2.imwrite(str(out_path), cell, [cv2.IMWRITE_JPEG_QUALITY, 92])
        saved += 1
    return saved, method


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=os.getenv("INPUT_DIR", "input/grids"))
    parser.add_argument("--output", default="output/crops")
    args = parser.parse_args()
    in_dir, out_dir = Path(args.input), Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not in_dir.exists():
        print(f"input dir {in_dir} does not exist", file=sys.stderr)
        return 1
    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS)
    total = 0
    for img_path in images:
        n, method = process_image(img_path, out_dir)
        print(f"{img_path.name}: {n} crops ({method})")
        total += n
    print(f"\nDone. {total} crops written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
