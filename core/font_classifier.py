"""
Printer-font character classifier (font training) for the dot-matrix LOT / MFG / EXP print.

Every printed character is normalised (ink that crosses the middle of the line, scaled to the
line height, centred) and compared with the character library of the printer's own font by
normalised cross-correlation. A character passes only if it is recognised as the expected
character with a clear margin over every other character - so a '6' printed like an '8'
fails even though it differs by only a few dots.

The library is built from good cartons (tools/train_font.py) and stored in core/font_library.npz.
"""
import os
import sys
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

NORM_W, NORM_H = 12, 16


def canonical(label: str) -> str:
    """In this printer font the letter O and the (dotted) zero are the same glyph."""
    u = label.upper()
    return "0" if u == "O" else u


def _default_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "core", "font_library.npz")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "font_library.npz")


def glyph_mask(crop: np.ndarray, thr: float) -> Optional[np.ndarray]:
    """Ink of the character in a window: the stroke groups that cross the middle of the line."""
    if crop is None or crop.size == 0:
        return None
    h, w = crop.shape[:2]
    ink = (crop < thr).astype(np.uint8)
    # join the dots of one character so it is one component per stroke group
    k = max(1, int(round(h / 12.0)))
    joined = cv2.dilate(ink, np.ones((k + 1, k + 1), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n <= 1:
        return None
    # keep what crosses the middle band of the line (neighbouring lines only touch the edges)
    # and the middle columns of the window (neighbouring characters only reach into the sides)
    band_a, band_b = int(0.3 * h), int(0.7 * h)
    col_a, col_b = int(0.35 * w), int(0.65 * w)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y = st[i, cv2.CC_STAT_LEFT], st[i, cv2.CC_STAT_TOP]
        ww, hh = st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]
        if (y < band_b and y + hh > band_a and x < col_b and x + ww > col_a
                and st[i, cv2.CC_STAT_AREA] >= 4):
            keep[i] = True
    mask = keep[lab] & (ink > 0)
    return mask if mask.any() else None


def glyph_rows(mask: np.ndarray) -> Tuple[int, int]:
    ys = np.where(mask.any(axis=1))[0]
    return int(ys.min()), int(ys.max()) + 1


def trim_rows(mask: np.ndarray, height: int) -> np.ndarray:
    """Keep the `height` rows with the most ink (drops a neighbouring line's dots at the top / bottom)."""
    y1, y2 = glyph_rows(mask)
    if y2 - y1 <= height:
        return mask
    prof = mask.sum(axis=1).astype(np.float32)
    best, best_s = y1, -1.0
    for s in range(y1, y2 - height + 1):
        v = float(prof[s:s + height].sum())
        if v > best_s:
            best, best_s = s, v
    out = np.zeros_like(mask)
    out[best:best + height] = mask[best:best + height]
    return out


def normalize_box(crop: np.ndarray, thr: float) -> Optional[np.ndarray]:
    """
    Character box (position taken from the golden sample, not from this print's ink) ->
    zero-mean unit vector of the ink darkness. Extra or missing ink therefore changes the
    vector instead of moving the box.
    """
    if crop is None or crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 2:
        return None
    g = crop.astype(np.float32)
    ink = np.clip((thr + 25.0 - g) / 50.0, 0.0, 1.0)      # soft ink level around the threshold
    h, w = ink.shape
    bw = max(w, int(round(0.75 * h)))                       # keep the aspect ratio (narrow '1')
    canvas = np.zeros((h, bw), np.float32)
    ox = (bw - w) // 2
    canvas[:, ox:ox + w] = ink
    v = cv2.resize(canvas, (NORM_W, NORM_H), interpolation=cv2.INTER_AREA)
    v = cv2.GaussianBlur(v, (3, 3), 0).reshape(-1)
    v = v - v.mean()
    nrm = float(np.linalg.norm(v))
    return v / nrm if nrm > 1e-6 else None


def normalize_char(crop: np.ndarray, thr: float, mask: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """Grey character window -> zero-mean unit vector (None if there is no ink)."""
    if mask is None:
        mask = glyph_mask(crop, thr)
    if mask is None or not mask.any():
        return None
    h = mask.shape[0]
    ys, xs = np.where(mask)
    y1, y2, x1, x2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    ch = y2 - y1
    if ch < 0.35 * h:            # '-' or ':' - punctuation is not classified
        return None
    glyph = mask[y1:y2, x1:x2].astype(np.float32)
    # keep the aspect ratio: box width follows the character height, glyph centred
    bw = max(x2 - x1, int(round(0.75 * ch)))
    canvas = np.zeros((ch, bw), np.float32)
    ox = (bw - (x2 - x1)) // 2
    canvas[:, ox:ox + (x2 - x1)] = glyph
    v = cv2.resize(canvas, (NORM_W, NORM_H), interpolation=cv2.INTER_AREA)
    v = cv2.GaussianBlur(v, (3, 3), 0).reshape(-1)
    v = v - v.mean()
    nrm = float(np.linalg.norm(v))
    return v / nrm if nrm > 1e-6 else None


class FontLibrary:
    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_path()
        self.labels: List[str] = []
        self.vectors: Optional[np.ndarray] = None
        self.load()

    @property
    def ready(self) -> bool:
        return self.vectors is not None and len(self.labels) > 0

    def classes(self) -> set:
        return set(self.labels)

    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        d = np.load(self.path, allow_pickle=False)
        self.labels = [canonical(str(x)) for x in d["labels"]]
        self.vectors = d["vectors"].astype(np.float32)
        return True

    def save(self, path: Optional[str] = None):
        np.savez_compressed(path or self.path, labels=np.array(self.labels), vectors=self.vectors)

    def fit(self, samples: List[Tuple[str, np.ndarray]]):
        self.labels = [canonical(s[0]) for s in samples]
        self.vectors = np.stack([s[1] for s in samples]).astype(np.float32)

    def add(self, samples: List[Tuple[str, np.ndarray]]):
        """Extend the library (e.g. with the golden sample's characters at teach time)."""
        if not samples:
            return
        if not self.ready:
            self.fit(samples)
            return
        self.labels = self.labels + [canonical(s[0]) for s in samples]
        self.vectors = np.vstack([self.vectors, np.stack([s[1] for s in samples]).astype(np.float32)])

    def _index(self):
        """Samples sorted by class so the best match per class is one vectorised reduce."""
        key = (len(self.labels), id(self.vectors))
        if getattr(self, "_idx_key", None) == key:
            return
        order = np.argsort(np.array(self.labels), kind="stable")
        self.labels = [self.labels[i] for i in order]
        self.vectors = self.vectors[order]
        self._cls = sorted(set(self.labels))
        self._starts = np.array([self.labels.index(c) for c in self._cls])
        self._idx_key = (len(self.labels), id(self.vectors))

    def score_matrix(self, vecs: np.ndarray) -> np.ndarray:
        """(n_chars x n_classes) best similarity per class."""
        self._index()
        sims = vecs @ self.vectors.T
        return np.maximum.reduceat(sims, self._starts, axis=1)

    def scores(self, vec: np.ndarray) -> Dict[str, float]:
        m = self.score_matrix(vec[None, :])[0]
        return {c: float(s) for c, s in zip(self._cls, m)}

    def check_many(self, vecs: np.ndarray, expected: List[str], min_score: float, min_margin: float):
        """Vectorised check of several characters: list of (ok, best, score, margin)."""
        m = self.score_matrix(vecs)
        out = []
        for i, e in enumerate(expected):
            e = canonical(e)
            if e not in self._cls:
                out.append((None, "", 0.0, 0.0)); continue
            k = self._cls.index(e)
            es = float(m[i, k])
            others = np.delete(m[i], k)
            other = float(others.max()) if others.size else -1.0
            best = self._cls[int(np.argmax(m[i]))]
            out.append((es >= min_score and es - other >= min_margin, best, es, es - other))
        return out

    def check(self, vec: np.ndarray, expected: str, min_score: float, min_margin: float) -> Tuple[bool, str, float, float]:
        """(ok, best label, score of expected, margin expected - best other)."""
        expected = canonical(expected)
        sc = self.scores(vec)
        best = max(sc, key=sc.get)
        exp_s = sc.get(expected, -1.0)
        other = max((s for l, s in sc.items() if l != expected), default=-1.0)
        margin = exp_s - other
        return (exp_s >= min_score and margin >= min_margin), best, exp_s, margin
