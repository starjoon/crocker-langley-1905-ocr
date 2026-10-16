"""Crop ads off directory page scans and split the text area into columns.

For each page this writes, under data/prep/<page_id>/:
  header.png        the running header (page number, section / alphabetical range)
  colN.png          each text column, ads removed
  colN_partM.png    each column cut into chunks at blank lines, small enough that
                    a vision model sees them at native resolution
  debug.png         the page with detected rules and kept regions drawn on it
  layout.json       the coordinates of everything above

Usage: .venv/bin/python scripts/prep_pages.py [data/raw/*.jp2]
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "prep"

# Chunks taller than this get downscaled by most vision APIs (long edge ~1568px).
MAX_CHUNK_H = 1100
# Text columns are at least this wide; narrower strips between rules are side ads.
MIN_COL_W = 380


def load_gray(path):
    return np.array(Image.open(path).convert("L"))


def binarize(gray):
    # Ink = 255, paper = 0.
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def find_rules(bw, horizontal, min_len, seg_len=100, tol=8):
    """Return bounding boxes (x0, y0, x1, y1) of long straight printed rules.

    Rules on these scans are faint and broken, so short straight segments are
    found first, then segments on the same line are merged. A rule is kept when
    its segments add up to at least min_len.
    """
    # Bridge small breaks along the rule (smaller than the gap between text lines).
    close = (5, 1) if horizontal else (1, 5)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, close))
    k = (seg_len, 1) if horizontal else (1, seg_len)
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, k))
    n, _, stats, _ = cv2.connectedComponentsWithStats(lines)
    segs = [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h, _ in stats[1:n]]

    # Group segments lying on the same line (same y for horizontal, same x for vertical).
    pos = (lambda s: (s[1] + s[3]) / 2) if horizontal else (lambda s: (s[0] + s[2]) / 2)
    length = (lambda s: s[2] - s[0]) if horizontal else (lambda s: s[3] - s[1])
    groups = []
    for s in sorted(segs, key=pos):
        if groups and pos(s) - pos(groups[-1][0]) <= tol:
            groups[-1].append(s)
        else:
            groups.append([s])
    span = (lambda s: (s[0], s[2])) if horizontal else (lambda s: (s[1], s[3]))
    rules = []
    for g in groups:
        inked = union_length([span(s) for s in g])
        if inked < min_len:
            continue
        # (x0, y0, x1, y1, inked length): segments in one group can be far apart,
        # so callers judge a rule's size by its inked length, not its extent.
        rules.append((min(s[0] for s in g), min(s[1] for s in g),
                      max(s[2] for s in g), max(s[3] for s in g), inked))
    return rules


def union_length(intervals):
    total, end = 0, None
    for a, b in sorted(intervals):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def body_bounds(h_rules, H, W):
    """Top and bottom of the directory text, between the header rule and footer ad."""
    # A real page rule is long and mostly solid; ad-box borders lined up with
    # bits of bold text can add up to a long but gappy "rule".
    wide = [r for r in h_rules if r[4] > 0.45 * W and r[4] > 0.7 * (r[2] - r[0])]
    top_band = [r for r in wide if r[3] < 0.2 * H]
    bot_band = [r for r in wide if r[1] > 0.8 * H]
    # Scans are slightly skewed, so take the outer edge of each rule; cutting at
    # the inner edge can clip the first or last text line at one end.
    top = max(r[1] for r in top_band) if top_band else int(0.08 * H)
    bottom = min(r[3] for r in bot_band) if bot_band else int(0.92 * H)
    # The running header sits just above the lowest top rule; find the rule above it.
    above = [r for r in top_band if r[3] < top - 25]
    header_top = max(r[3] for r in above) if above else max(0, top - 70)
    return header_top, top, bottom


def column_spans(bw, v_rules, top, bottom, W):
    """Split the body into vertical strips at rules (or wide gaps), keep text-width ones."""
    body_h = bottom - top
    xs = sorted(
        (r[0] + r[2]) // 2
        for r in v_rules
        if r[1] < top + 0.5 * body_h and r[3] > top + 0.5 * body_h and r[4] > 0.4 * body_h
    )
    # Merge rules closer than 25px (double borders on ad boxes).
    cuts = []
    for x in xs:
        if cuts and x - cuts[-1] < 25:
            cuts[-1] = (cuts[-1] + x) // 2
        else:
            cuts.append(x)
    edges = [0] + cuts + [W]
    spans = []
    for x0, x1 in zip(edges, edges[1:]):
        x0, x1 = x0 + 6, x1 - 6
        if x1 - x0 < MIN_COL_W:
            continue
        ink = bw[top:bottom, x0:x1]
        if ink.mean() / 255 < 0.03:  # blank margin
            continue
        spans.extend(split_on_gutter(bw, x0, x1, top, bottom))
    return [trim_x(bw, x0, x1, top, bottom) for x0, x1 in spans]


def split_on_gutter(bw, x0, x1, top, bottom):
    """A strip twice as wide as a column with no rule between: split at the widest blank gap."""
    if x1 - x0 < 2 * MIN_COL_W + 60:
        return [(x0, x1)]
    prof = bw[top:bottom, x0:x1].mean(axis=0) / 255
    mid = slice(int(0.3 * len(prof)), int(0.7 * len(prof)))
    blank = prof[mid] < 0.01
    best, run, start = (0, 0), 0, 0
    for i, b in enumerate(blank):
        if b:
            run += 1
            if run > best[0]:
                best = (run, i - run + 1)
        else:
            run = 0
    if best[0] < 15:
        return [(x0, x1)]
    cut = x0 + mid.start + best[1] + best[0] // 2
    return [(x0, cut), (cut, x1)]


def trim_x(bw, x0, x1, top, bottom):
    """Shrink a column to its inked area plus a small margin."""
    prof = bw[top:bottom, x0:x1].mean(axis=0) / 255
    ink = np.where(prof > 0.004)[0]
    if len(ink):
        x0, x1 = x0 + max(0, ink[0] - 8), x0 + min(len(prof), ink[-1] + 8)
    return int(x0), int(x1)


def column_top(h_rules, x0, x1, top, bottom):
    """Skip a boxed or ruled ad at the top of a column (e.g. 'FRED H. CHASE & CO.')."""
    w = x1 - x0
    inside = [
        r for r in h_rules
        if r[0] < x0 + 0.15 * w and r[2] > x1 - 0.15 * w and r[4] > 0.6 * w
        and top + 20 < r[1] < top + 0.4 * (bottom - top)
    ]
    return max(r[3] for r in inside) + 4 if inside else top


def chunk_rows(bw, x0, x1, y0, y1):
    """Cut a column into chunks no taller than MAX_CHUNK_H, at blank lines between text."""
    prof = bw[y0:y1, x0:x1].mean(axis=1) / 255
    blank = prof < 0.002
    cuts, start = [], 0
    while (y1 - y0) - start > MAX_CHUNK_H:
        target = start + MAX_CHUNK_H
        # Search back from the target for the middle of a blank band.
        i = target
        while i > start + MAX_CHUNK_H // 2 and not blank[i]:
            i -= 1
        if i <= start + MAX_CHUNK_H // 2:
            i = target  # no gap found; cut hard
        else:
            j = i
            while j > start and blank[j - 1]:
                j -= 1
            i = (i + j) // 2
        cuts.append(i)
        start = i
    bounds = [0] + cuts + [y1 - y0]
    return [(y0 + a, y0 + b) for a, b in zip(bounds, bounds[1:])]


def process(path):
    page_id = path.stem
    out = OUT / page_id
    out.mkdir(parents=True, exist_ok=True)
    gray = load_gray(path)
    H, W = gray.shape
    bw = binarize(gray)

    h_rules = find_rules(bw, horizontal=True, min_len=W // 6)
    v_rules = find_rules(bw, horizontal=False, min_len=H // 6)
    header_top, top, bottom = body_bounds(h_rules, H, W)
    cols = column_spans(bw, v_rules, top, bottom, W)

    layout = {"page_id": page_id, "size": [W, H], "header": None, "columns": []}
    debug = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for r in h_rules + v_rules:
        cv2.rectangle(debug, r[:2], r[2:4], (0, 0, 255), 3)

    if cols:
        hx0, hx1 = cols[0][0], cols[-1][1]
        Image.fromarray(gray[header_top:top, hx0:hx1]).save(out / "header.png")
        layout["header"] = [hx0, header_top, hx1, top]
        cv2.rectangle(debug, (hx0, header_top), (hx1, top), (255, 0, 255), 4)

    for ci, (x0, x1) in enumerate(cols, 1):
        y0 = column_top(h_rules, x0, x1, top, bottom)
        Image.fromarray(gray[y0:bottom, x0:x1]).save(out / f"col{ci}.png")
        chunks = chunk_rows(bw, x0, x1, y0, bottom)
        col = {"box": [x0, y0, x1, bottom], "chunks": []}
        for mi, (a, b) in enumerate(chunks, 1):
            Image.fromarray(gray[a:b, x0:x1]).save(out / f"col{ci}_part{mi}.png")
            col["chunks"].append([x0, a, x1, b])
            cv2.rectangle(debug, (x0, a), (x1, b), (0, 160, 0), 4)
        layout["columns"].append(col)

    cv2.imwrite(str(out / "debug.png"), cv2.resize(debug, (W // 2, H // 2)))
    (out / "layout.json").write_text(json.dumps(layout, indent=2))
    n_chunks = sum(len(c["chunks"]) for c in layout["columns"])
    print(f"{page_id}: {len(cols)} columns, {n_chunks} chunks, body y={top}-{bottom}")


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "data" / "raw").glob("*.jp2"))
    for p in paths:
        process(p)
