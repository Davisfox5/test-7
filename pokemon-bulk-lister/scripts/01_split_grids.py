"""Step 1 — split 3x3 binder-page grid photos into 9 individual card crops.

Usage:
    python scripts/01_split_grids.py [--input input/grids] [--output output/crops]

Behavior:
- Auto-detects orientation (landscape vs portrait scans) by checking which
  rotation produces a more rectangular content region after thresholding.
- Detects the binder page edges, then divides the inner region into 3x3.
- Skips empty pockets by measuring per-cell color variance against a threshold.
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
EMPTY_POCKET_STDDEV = 12.0  # cells below this color stddev are considered empty


MIN_PAGE_FRACTION = 0.6  # detected bbox must cover at least this much of the image
                          # in each dimension; otherwise we fall back to the whole image
                          # (the previous version often picked a single card as the "page").


def find_page_bbox(img: np.ndarray) -> tuple[int, int, int, int]:
    """Find the bounding box of the binder page in the image.

    Uses Otsu thresholding + a morphological close so adjacent cards merge into
    one blob. Falls back to the full image if the detected bbox is too narrow
    or too short — common when the photo is already a tightly-cropped binder
    page or when a single bright card outshines the rest of the page.
    """
    img_h, img_w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Close the gaps between cards so the page reads as one contour, not nine.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(15, img_w // 30), max(15, img_h // 30)),
    )
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0, 0, img_w, img_h

    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)

    # Sanity check: a real binder page covers most of the frame in both
    # dimensions. If not, the photo is probably already cropped or the
    # threshold latched onto a single card — use the whole image instead.
    if w < img_w * MIN_PAGE_FRACTION or h < img_h * MIN_PAGE_FRACTION:
        return 0, 0, img_w, img_h

    pad_x = int(w * 0.01)
    pad_y = int(h * 0.01)
    x = max(0, x + pad_x)
    y = max(0, y + pad_y)
    w = min(img_w - x, w - 2 * pad_x)
    h = min(img_h - y, h - 2 * pad_y)
    return x, y, w, h


def auto_orient(img: np.ndarray) -> np.ndarray:
    """Rotate so the binder page is portrait (taller than wide).

    Binder pages with 3x3 pockets are taller than wide. If the detected page
    bbox is wider than tall, rotate 90 degrees clockwise.
    """
    _, _, w, h = find_page_bbox(img)
    if w > h:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def cell_is_empty(cell: np.ndarray) -> bool:
    """A pocket is empty if its color stddev is very low (uniform plastic)."""
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    return float(np.std(gray)) < EMPTY_POCKET_STDDEV


def split_grid(img: np.ndarray) -> list[Optional[np.ndarray]]:
    """Return 9 cell images in row-major order. None for empty pockets."""
    x, y, w, h = find_page_bbox(img)
    page = img[y : y + h, x : x + w]

    cell_w = w // 3
    cell_h = h // 3
    margin_x = int(cell_w * 0.04)
    margin_y = int(cell_h * 0.04)

    cells: list[Optional[np.ndarray]] = []
    for r in range(3):
        for c in range(3):
            cx0 = c * cell_w + margin_x
            cy0 = r * cell_h + margin_y
            cx1 = (c + 1) * cell_w - margin_x
            cy1 = (r + 1) * cell_h - margin_y
            cell = page[cy0:cy1, cx0:cx1]
            if cell.size == 0 or cell_is_empty(cell):
                cells.append(None)
            else:
                cells.append(cell)
    return cells


def process_image(path: Path, out_dir: Path) -> int:
    img = cv2.imread(str(path))
    if img is None:
        print(f"[warn] could not read {path}", file=sys.stderr)
        return 0

    img = auto_orient(img)
    cells = split_grid(img)

    saved = 0
    stem = path.stem
    for idx, cell in enumerate(cells):
        if cell is None:
            continue
        row, col = divmod(idx, 3)
        out_path = out_dir / f"{stem}_r{row}c{col}.jpg"
        cv2.imwrite(str(out_path), cell, [cv2.IMWRITE_JPEG_QUALITY, 92])
        saved += 1
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=os.getenv("INPUT_DIR", "input/grids"))
    parser.add_argument("--output", default="output/crops")
    args = parser.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists():
        print(f"input dir {in_dir} does not exist", file=sys.stderr)
        return 1

    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS)
    if not images:
        print(f"no images found in {in_dir}", file=sys.stderr)
        return 1

    total = 0
    for img_path in images:
        n = process_image(img_path, out_dir)
        print(f"{img_path.name}: {n} crops")
        total += n
    print(f"\nDone. {total} crops written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
