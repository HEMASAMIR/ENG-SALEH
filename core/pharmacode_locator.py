"""
Automatic Pharmacode (Laetus) locator + decoder.

Finds the bar pattern anywhere in the frame (no taught ROI needed) by scanning
rows for a run sequence that repeats identically over many consecutive rows,
then decodes the median bar widths (narrow = 1, wide = 2, most significant
bar first).
"""
from dataclasses import dataclass
from typing import List, Optional
import cv2
import numpy as np


@dataclass
class PharmaResult:
    value: Optional[int] = None
    bars: int = 0
    pattern: str = ""
    bbox: Optional[List[int]] = None   # [x1, y1, x2, y2] in the input image
    ratio: float = 0.0                 # wide / narrow width ratio
    error: str = ""


def _dark_mask(gray: np.ndarray) -> np.ndarray:
    # local background = bright paper around the bars (horizontal max filter)
    # (relative contrast only: the bottom flap is often in shadow, paper ~50 grey levels)
    bg = cv2.dilate(gray, cv2.getStructuringElement(cv2.MORPH_RECT, (61, 1))).astype(np.int16)
    g = gray.astype(np.int16)
    return (g < bg * 0.5) & (bg - g >= 20)


def _row_sequences(row: np.ndarray, min_bars: int, max_w: int, max_gap: int):
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    seqs, cur = [], []
    for s, e in zip(starts, ends):
        w = e - s
        if w < 2 or w > max_w:
            if len(cur) >= min_bars:
                seqs.append(cur)
            cur = []
            continue
        if cur and s - cur[-1][1] > max_gap:
            if len(cur) >= min_bars:
                seqs.append(cur)
            cur = []
        cur.append((s, e))
    if len(cur) >= min_bars:
        seqs.append(cur)
    return seqs


def locate_and_decode(gray: np.ndarray, min_bars: int = 3, max_bar_w: int = 40,
                      max_gap: int = 40, row_step: int = 3, min_rows: int = 10,
                      reverse: bool = False) -> PharmaResult:
    if gray is None or gray.size == 0:
        return PharmaResult(error="empty image")
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    dark = _dark_mask(gray)
    h, w = gray.shape

    # collect candidate sequences per row
    cands = []   # (y, x1, x2, widths)
    for y in range(0, h, row_step):
        for seq in _row_sequences(dark[y], min_bars, max_bar_w, max_gap):
            widths = tuple(e - s for s, e in seq)
            if max(widths) < 1.6 * min(widths):
                continue  # need both narrow and wide bars (all-equal runs are usually text strokes)
            cands.append((y, seq[0][0], seq[-1][1], widths))
    if not cands:
        return PharmaResult(error="no bar pattern found")

    # cluster by horizontal extent and bar count; the barcode repeats row after row
    # (compare with the cluster's latest row so a tilted carton can drift sideways)
    clusters = []
    for c in cands:
        for cl in clusters:
            ref = cl[-1]
            if (len(c[3]) == len(ref[3]) and abs(c[1] - ref[1]) <= 5 and abs(c[2] - ref[2]) <= 5
                    and 0 < c[0] - ref[0] <= row_step * 3):
                cl.append(c)
                break
        else:
            clusters.append([c])
    clusters = [cl for cl in clusters if len(cl) >= min_rows]
    if not clusters:
        return PharmaResult(error="bar pattern not stable over enough rows")
    best = max(clusters, key=lambda cl: len(cl) * len(cl[0][3]))

    widths = np.median(np.array([c[3] for c in best], dtype=np.float32), axis=0)
    lo, hi = float(widths.min()), float(widths.max())
    ratio = hi / lo if lo > 0 else 0.0
    x1 = int(min(c[1] for c in best)); x2 = int(max(c[2] for c in best))
    y1 = int(best[0][0]); y2 = int(best[-1][0])
    res = PharmaResult(bars=len(widths), bbox=[x1, y1, x2, y2], ratio=round(ratio, 2))
    if ratio < 1.8:
        res.error = f"narrow/wide contrast too low ({ratio:.2f})"
        return res
    thr = (lo + hi) / 2.0
    kinds = ["W" if x > thr else "n" for x in widths]
    if reverse:
        kinds = kinds[::-1]
    val = 0
    for k in kinds:
        val = val * 2 + (2 if k == "W" else 1)
    res.value, res.pattern = val, "".join(kinds)
    return res
