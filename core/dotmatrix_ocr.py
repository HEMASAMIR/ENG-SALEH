"""
Dot-matrix (inkjet / 5x7) character reader for the LOT / MFG / EXP block.

General-purpose OCR engines confuse the printer's dotted zero with 8 / 9 / 3.
This module segments each printed line into characters and classifies every
character against templates learned from the printer's own font
("font training", as done by industrial OCR tools).

Templates are stored in core/font_templates.npz and can be retrained from
labelled sample images with tools/train_font.py.
"""
import os
import sys
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

TEMPLATE_W, TEMPLATE_H = 16, 24


def _default_template_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(os.path.dirname(sys.executable), "core")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "font_templates.npz")


@dataclass
class CharCell:
    x1: int
    x2: int
    img: np.ndarray                 # normalized TEMPLATE_H x TEMPLATE_W float32 (ink = 1)
    label: str = "?"
    score: float = 0.0              # best correlation (0..1)
    margin: float = 0.0             # best - second best among *different* labels


@dataclass
class LineRead:
    text: str
    cells: List[CharCell] = field(default_factory=list)
    band: Tuple[int, int] = (0, 0)

    @property
    def min_score(self) -> float:
        return min((c.score for c in self.cells), default=0.0)


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def box_angle(box) -> float:
    """Angle in degrees of a RapidOCR quadrilateral's top edge."""
    b = np.asarray(box, dtype=np.float32)
    dx, dy = b[1][0] - b[0][0], b[1][1] - b[0][1]
    return float(np.degrees(np.arctan2(dy, dx)))


def crop_block(gray: np.ndarray, boxes, pad: int = 10) -> np.ndarray:
    """Deskew and crop the region covered by the given line boxes."""
    pts = np.concatenate([np.asarray(b, dtype=np.float32) for b in boxes])
    angle = float(np.median([box_angle(b) for b in boxes]))
    cx, cy = pts.mean(axis=0)
    M = cv2.getRotationMatrix2D((float(cx), float(cy)), angle, 1.0)
    rotated_pts = cv2.transform(pts[None, :, :], M)[0]
    x1, y1 = np.floor(rotated_pts.min(axis=0)).astype(int) - pad
    x2, y2 = np.ceil(rotated_pts.max(axis=0)).astype(int) + pad
    h, w = gray.shape[:2]
    warped = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    return warped[y1:y2, x1:x2]


def binarize(gray: np.ndarray) -> np.ndarray:
    """Return ink mask (uint8 0/1) with paper speckle and carton edges removed."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    h, w = bw.shape
    keep = np.zeros(n, dtype=bool)
    area = stats[:, cv2.CC_STAT_AREA]
    ch, cw = stats[:, cv2.CC_STAT_HEIGHT], stats[:, cv2.CC_STAT_WIDTH]
    # printed dots / strokes: not specks, and not long carton edges / fold shadows
    keep[1:] = (area[1:] >= 4) & (ch[1:] < 0.6 * h) & (cw[1:] < 0.5 * w)
    return keep[lab].astype(np.uint8)


def clean_line_image(gray_line: np.ndarray, scale: float = 2.0, join: float = 0.5,
                     drop_zero_dot: bool = True) -> np.ndarray:
    """
    Turn a dot-matrix line into solid dark strokes on white for a general OCR engine:
      * upscale, binarize, join neighbouring dots into strokes
      * remove the printer's centre dot inside '0' (read as 8 / 9 otherwise)
    Returns a 3-channel uint8 image.
    """
    g = cv2.resize(gray_line, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    _, ink = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h = ink.shape[0]
    # speck removal
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    min_area = max(6, int(0.0025 * h * h))
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    ink = (keep[lab] * 255).astype(np.uint8)
    # join dots: close with a kernel of ~ one dot gap
    k = max(1, int(round(h * 0.06 * join * 2)))
    if k > 1:
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if drop_zero_dot:
        # components fully enclosed in another component's hole and small -> centre dot of '0'
        cnts, hier = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hier is not None:
            hier = hier[0]
            holes = [i for i, hh in enumerate(hier) if hh[3] >= 0]
            outers = [i for i, hh in enumerate(hier) if hh[3] < 0]
            for i in outers:
                x, y, cw_, ch_ = cv2.boundingRect(cnts[i])
                if ch_ > 0.35 * h:
                    continue
                cx, cy = x + cw_ / 2.0, y + ch_ / 2.0
                for j in holes:
                    if cv2.pointPolygonTest(cnts[j], (cx, cy), False) > 0:
                        hx, hy, hw, hh_ = cv2.boundingRect(cnts[j])
                        if cw_ * ch_ < 0.5 * hw * hh_:
                            cv2.drawContours(ink, cnts, i, 0, thickness=-1)
                        break
    out = 255 - ink
    out = cv2.copyMakeBorder(out, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def _runs(active: np.ndarray) -> List[List[int]]:
    runs, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            runs.append([start, i]); start = None
    if start is not None:
        runs.append([start, len(active)])
    return runs


def split_lines(ink: np.ndarray, expected: int = 3) -> List[Tuple[int, int]]:
    """Find `expected` horizontal text bands from the row ink profile."""
    h, w = ink.shape
    prof = ink.sum(axis=1).astype(np.float32)
    runs = _runs(prof > max(2.0, 0.015 * w))
    # merge runs separated by a 1px gap (broken dot rows)
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= 1:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    runs = [r for r in merged if r[1] - r[0] >= 3]
    if not runs:
        return []
    # strip fragments of neighbouring print at the top / bottom of the crop
    while len(runs) > 1:
        line_h = (runs[-1][1] - runs[0][0]) / float(expected)
        if runs[0][1] - runs[0][0] < 0.45 * line_h:
            runs.pop(0)
        elif runs[-1][1] - runs[-1][0] < 0.45 * line_h:
            runs.pop()
        else:
            break
    top, bottom = runs[0][0], runs[-1][1]
    span = bottom - top
    if span < expected * 6:
        return []
    # place the (expected-1) cuts at the emptiest rows near the ideal positions
    cuts = [top]
    for k in range(1, expected):
        centre = top + k * span / float(expected)
        lo = int(max(cuts[-1] + 4, centre - span / (2.5 * expected)))
        hi = int(min(bottom - 4, centre + span / (2.5 * expected)))
        if hi <= lo:
            cuts.append(int(centre)); continue
        seg = prof[lo:hi]
        m = seg.min()
        idx = np.where(seg <= m + 0.5)[0]
        # middle of the lowest valley (closest to the ideal position)
        best = idx[np.argmin(np.abs(idx + lo - centre))]
        cuts.append(int(lo + best))
    cuts.append(bottom)
    out = []
    for i in range(expected):
        a, b = cuts[i], cuts[i + 1]
        rows = np.where(prof[a:b] > max(1.0, 0.01 * w))[0]
        if rows.size:
            a, b = a + int(rows[0]), a + int(rows[-1]) + 1
        out.append((int(a), int(b)))
    return out


def segment_chars(ink_line: np.ndarray) -> List[Tuple[int, int]]:
    """Split a single text band into character column ranges.

    5x7 dot-matrix geometry: band height ~ 7 dot pitches, character pitch
    ~ 6 dot pitches (5 dots + 1 space).
    """
    h, w = ink_line.shape
    dot = max(1.0, h / 7.0)
    char_pitch = 6.0 * dot
    # join the separate dots of one character horizontally (but not across the 1-dot gap)
    k = max(1, int(round(dot * 0.6)))
    joined = cv2.dilate(ink_line, np.ones((1, k), np.uint8)) if k > 1 else ink_line
    col = joined.sum(axis=0)
    segs = _runs(col > 0)
    # undo dilation growth on the right edge
    segs = [[a, max(a + 1, b - (k - 1))] for a, b in segs]
    # drop anything touching the crop border (carton edges / neighbouring print)
    segs = [s for s in segs if s[0] > 0 and s[1] < w]
    if not segs:
        return []
    # merge fragments of one character split by a missing dot column
    merged = [segs[0]]
    for s in segs[1:]:
        prev = merged[-1]
        if s[0] - prev[1] <= max(1, int(dot * 0.5)) and (s[1] - prev[0]) <= char_pitch * 0.95:
            prev[1] = s[1]
        else:
            merged.append(s)
    # split blobs that are two or more touching characters
    out = []
    raw_col = ink_line.sum(axis=0).astype(np.float32)
    for a, b in merged:
        width = b - a
        n = max(1, int(round((width + dot) / char_pitch)))
        if n == 1:
            out.append((a, b)); continue
        cuts = [a]
        for i in range(1, n):
            centre = a + int(round(i * (width + dot) / n - dot / 2))
            lo, hi = max(a + 1, centre - int(dot)), min(b - 1, centre + int(dot) + 1)
            cuts.append(lo + int(np.argmin(raw_col[lo:hi])) if hi > lo else centre)
        cuts.append(b)
        out.extend((cuts[i], cuts[i + 1]) for i in range(n))
    # drop speck-sized segments (less ink than a colon)
    min_ink = max(4, int(dot * dot * 1.2))
    return [(a, b) for a, b in out if ink_line[:, a:b].sum() >= min_ink]


def normalize_cell(ink_line: np.ndarray, x1: int, x2: int) -> np.ndarray:
    """Place a character into a fixed-size canvas preserving its vertical position in the band."""
    h = ink_line.shape[0]
    cell = ink_line[:, x1:x2].astype(np.float32)
    w = x2 - x1
    # keep aspect: characters are drawn into a box of band height, width = band height * 2/3
    box_w = max(w, int(round(h * 2 / 3)))
    canvas = np.zeros((h, box_w), dtype=np.float32)
    off = (box_w - w) // 2
    canvas[:, off:off + w] = cell
    out = cv2.resize(canvas, (TEMPLATE_W, TEMPLATE_H), interpolation=cv2.INTER_AREA)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    return out


# --------------------------------------------------------------------------- #
# Font templates
# --------------------------------------------------------------------------- #
class FontModel:
    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_template_path()
        self.labels: List[str] = []
        self.samples: Optional[np.ndarray] = None   # N x D (zero-mean, unit-norm)
        self.load()

    @property
    def ready(self) -> bool:
        return self.samples is not None and len(self.labels) > 0

    @staticmethod
    def _vec(img: np.ndarray) -> np.ndarray:
        v = img.reshape(-1).astype(np.float32)
        v = v - v.mean()
        n = np.linalg.norm(v)
        return v / n if n > 1e-6 else v

    def load(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            d = np.load(self.path, allow_pickle=False)
            self.labels = [str(x) for x in d["labels"]]
            self.samples = d["samples"].astype(np.float32)
            return True
        except Exception as e:
            print(f"[DotMatrixOCR] Could not load font templates: {e}")
            return False

    def save(self, path: Optional[str] = None):
        np.savez_compressed(path or self.path, labels=np.array(self.labels), samples=self.samples)

    def fit(self, imgs: List[np.ndarray], labels: List[str]):
        self.labels = list(labels)
        self.samples = np.stack([self._vec(i) for i in imgs])

    def classify(self, img: np.ndarray) -> Tuple[str, float, float]:
        """k-NN (k=3 vote weighted by similarity). Returns (label, score, margin)."""
        v = self._vec(img)
        sims = self.samples @ v
        best_by_label = {}
        for lab, s in zip(self.labels, sims):
            if s > best_by_label.get(lab, -1.0):
                best_by_label[lab] = float(s)
        ranked = sorted(best_by_label.items(), key=lambda kv: -kv[1])
        top_lab, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        return top_lab, top, top - second


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def read_block(block_gray: np.ndarray, model: Optional[FontModel], expected_lines: int = 3) -> List[LineRead]:
    ink = binarize(block_gray)
    lines = []
    for (a, b) in split_lines(ink, expected_lines):
        band = ink[a:b]
        cells = []
        for (x1, x2) in segment_chars(band):
            img = normalize_cell(band, x1, x2)
            c = CharCell(x1=x1, x2=x2, img=img)
            if model is not None and model.ready:
                c.label, c.score, c.margin = model.classify(img)
            cells.append(c)
        text = "".join(c.label for c in cells)
        lines.append(LineRead(text=text, cells=cells, band=(a, b)))
    return lines
