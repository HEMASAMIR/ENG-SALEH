"""
Automatic carton inspection (no taught ROIs needed).

Pipeline per frame:
  1. Grayscale + rotate so the LOT/MFG/EXP print reads left-to-right.
  2. Text detection (RapidOCR / PP-OCR det, ONNX, CPU) on a downscaled frame.
  3. Anchor: first detected line that looks like "LOT:" / "MFG:" / "EXP:".
  4. Block: the lines that start at the anchor's left edge (ink based, so it
     works even when the detector merges or misses lines).
  5. Each line recognised separately; values parsed and normalised
     (LOT digits, MM-YYYY dates, printer dotted-zero rules).
  6. Print-defect checks: ink blots / smudges, missing or cut-off lines.
  7. Pharmacode located and decoded anywhere in the frame.
  8. Compare with expected values -> PASS / FAIL with reasons.
"""
import re
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from core import dotmatrix_ocr as dm
from core.pharmacode_locator import locate_and_decode

FIELDS = ("LOT", "MFG", "EXP")
LABEL_RE = re.compile(r"^[A-Z0-9]{2,4}[:;]")
LABEL_SHAPES = {
    # common recognitions of the dot-matrix labels (first letter decides when the rest is unclear)
    "LOT": {"LOT", "L0T", "LPT", "L9T", "LDT", "1OT", "HOT", "MOT", "HPT", "MPT", "H8T", "LOI"},
    "MFG": {"MFG", "MF6", "MEG", "ME6", "NEG", "NE6", "MFC", "MFE", "NFG", "NF6", "MPG", "MF0"},
    "EXP": {"EXP", "EXF", "EKP", "EYP", "E8P", "EXR"},
}
_DIG = {"O": "0", "o": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "l": "1", "|": "1", "i": "1",
        "L": "1", "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "G": "6", "b": "6", "g": "9",
        "q": "9", "T": "7"}


# --------------------------------------------------------------------------- #
# Value parsing
# --------------------------------------------------------------------------- #
def split_label_value(text: str) -> Tuple[str, str]:
    t = text.replace(" ", "")
    for sep in (":", ";"):
        if sep in t:
            a, b = t.split(sep, 1)
            return a, b
    return t[:3], t[3:]


def normalize_lot(v: str) -> str:
    return "".join(_DIG.get(c, c) for c in v if c.isalnum())


def normalize_date(v: str) -> str:
    v = "".join(_DIG.get(c, c) if c.isalpha() else c for c in v)
    v = v.replace("~", "-").replace("_", "-").replace(".", "-").replace("/", "-")
    v = re.sub(r"[^0-9\-]", "", v)
    d = re.sub(r"[^0-9]", "", v)
    if len(d) == 6:
        mm, yyyy = d[:2], d[2:]
        # printer's dotted zero is read as 8 / 9 / 3: a month's tens digit can only be 0 or 1,
        # and the year's hundreds digit (20xx) can only be 0
        if mm[0] not in "01":
            mm = "0" + mm[1]
        if yyyy[0] == "2" and yyyy[1] != "0":
            yyyy = "20" + yyyy[2:]
        return f"{mm}-{yyyy}"
    if len(d) == 4:     # MM-YY
        mm = d[:2] if d[0] in "01" else "0" + d[1]
        return f"{mm}-{d[2:]}"
    return v.strip("-")


def normalize_expected(field_name: str, value) -> str:
    s = str(value or "").strip().upper()
    for p in ("LOT", "MFG", "EXP", "BATCH", "B.NO", "BN"):
        if s.startswith(p):
            s = s[len(p):].lstrip(" :.-")
    if field_name == "LOT":
        return normalize_lot(s)
    return normalize_date(s)


def label_matches(read_label: str, field_name: str) -> bool:
    lab = read_label.upper()[-3:]
    if lab in LABEL_SHAPES[field_name]:
        return True
    target = field_name
    return len(lab) == 3 and sum(a != b for a, b in zip(lab, target)) <= 1


def blot_scan(sub: np.ndarray, threshold: float = 0.95, sigma: float = 3.0,
              lines_offset=(0, 0), lines=None) -> List[dict]:
    """Find ink blots in a grey crop of the printed block (see AutoInspector._blot_defects)."""
    sub = sub.astype(np.float32)
    bg = float(np.median(sub))
    ink_level = float(np.percentile(sub, 2))
    depth = max(1.0, bg - ink_level)
    blur = cv2.GaussianBlur(sub, (0, 0), sigma)
    score = (bg - blur) / depth
    mask = (score >= threshold).astype(np.uint8)
    if not mask.any():
        return []
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    ox, oy = lines_offset
    out = []
    for i in range(1, n):
        bx, by, bw, bh = stats[i, :4]
        cy = by + bh / 2.0 + oy
        field_name = "BLOCK"
        if lines:
            idx = next((k for k, l in enumerate(lines) if l[0] - 2 <= cy <= l[1] + 2), None)
            if idx is not None and idx < len(FIELDS):
                field_name = FIELDS[idx]
        out.append({
            "type": "INK_BLOT",
            "field": field_name,
            "score": round(float(score[lab == i].max()), 3),
            "bbox_roi": [int(bx + ox), int(by + oy), int(bx + bw + ox), int(by + bh + oy)],
        })
    return out


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class FieldRead:
    value: str = "N/A"
    raw: str = ""
    conf: float = 0.0
    status: str = "N/A"       # PASS / FAIL / N/A
    reason: str = ""


@dataclass
class InspectionResult:
    verdict: str = "FAIL"
    reasons: List[str] = field(default_factory=list)
    fields: Dict[str, FieldRead] = field(default_factory=lambda: {k: FieldRead() for k in FIELDS})
    product_text: str = ""
    pharma_code: Optional[int] = None
    pharma_status: str = "N/A"
    defects: List[dict] = field(default_factory=list)
    block_bbox: Optional[List[int]] = None     # in original frame coordinates
    pharma_bbox: Optional[List[int]] = None
    line_texts: List[str] = field(default_factory=list)
    lines_original: List[List[float]] = field(default_factory=list)  # 4 corners per line, original frame
    timings_ms: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
# Inspector
# --------------------------------------------------------------------------- #
class AutoInspector:
    _engine = None
    _engine_lock = threading.Lock()

    def __init__(self, rotation: int = 90, expected_lines: int = 3, det_scale: float = 0.6,
                 pharma_enabled: bool = True, pharma_reverse: bool = False,
                 blot_threshold: float = 0.95, min_line_conf: float = 0.5):
        """
        rotation: clockwise degrees (0/90/180/270) that make the print read left-to-right.
        """
        self.rotation = int(rotation) % 360
        self.expected_lines = expected_lines
        self.det_scale = det_scale
        self.pharma_enabled = pharma_enabled
        self.pharma_reverse = pharma_reverse
        self.blot_threshold = blot_threshold
        self.min_line_conf = min_line_conf
        self._rec_lock = threading.Lock()

    # ---------------- engine ----------------
    @classmethod
    def engine(cls):
        with cls._engine_lock:
            if cls._engine is None:
                from rapidocr_onnxruntime import RapidOCR
                cls._engine = RapidOCR()
            return cls._engine

    def warmup(self):
        eng = self.engine()
        dummy = np.full((64, 256, 3), 255, np.uint8)
        cv2.putText(dummy, "LOT:12345", (5, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
        eng(dummy, use_cls=False)

    def _rec(self, img_gray: np.ndarray) -> Tuple[str, float]:
        img = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR) if img_gray.ndim == 2 else img_gray
        with self._rec_lock:
            r, _ = self.engine()(img, use_det=False, use_cls=False)
        if not r:
            return "", 0.0
        return str(r[0][0]), float(r[0][1])

    def _det(self, img_gray: np.ndarray) -> List[np.ndarray]:
        s = self.det_scale
        small = cv2.resize(img_gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1 else img_gray
        with self._rec_lock:
            det, _ = self.engine()(cv2.cvtColor(small, cv2.COLOR_GRAY2BGR), use_cls=False, use_rec=False)
        return [np.asarray(b, dtype=np.float32) / s for b in (det or [])]

    # ---------------- geometry ----------------
    def _rotate(self, gray):
        if self.rotation == 90:
            return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
        if self.rotation == 180:
            return cv2.rotate(gray, cv2.ROTATE_180)
        if self.rotation == 270:
            return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return gray

    def _to_original(self, pts: np.ndarray, orig_shape) -> np.ndarray:
        H, W = orig_shape[:2]
        x, y = pts[:, 0], pts[:, 1]
        if self.rotation == 90:
            return np.stack([y, H - 1 - x], axis=1)
        if self.rotation == 180:
            return np.stack([W - 1 - x, H - 1 - y], axis=1)
        if self.rotation == 270:
            return np.stack([W - 1 - y, x], axis=1)
        return pts

    def _roi_rect_to_original(self, rect, orig_shape) -> List[float]:
        """ROI (deskewed) rect -> 4 corner points in the original frame, as [x1,y1,...,x4,y4]."""
        rx1, ry1, M = self._roi_geom
        x1, y1, x2, y2 = rect
        pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)
        if M is not None:
            Minv = cv2.invertAffineTransform(M)
            pts = cv2.transform(pts[None], Minv)[0]
        pts = pts + np.array([rx1, ry1], np.float32)
        pts = self._to_original(pts, orig_shape)
        return [round(float(v), 1) for v in pts.reshape(-1)]

    # ---------------- block localisation ----------------
    def _find_anchor(self, rot: np.ndarray, boxes: List[np.ndarray]):
        cands = []
        for b in boxes:
            x1, y1 = b.min(axis=0); x2, y2 = b.max(axis=0)
            w, h = x2 - x1, y2 - y1
            if 18 <= h <= 90 and 60 <= w <= 520 and w > 2.5 * h:
                cands.append((y1, x1, b))
        cands.sort(key=lambda c: (c[0], c[1]))
        for _, _, b in cands:
            x1, y1 = np.floor(b.min(axis=0)).astype(int)
            x2, y2 = np.ceil(b.max(axis=0)).astype(int)
            crop = rot[max(0, y1):y2, max(0, x1):x2]
            if crop.size == 0:
                continue
            txt, conf = self._rec(crop)
            t = txt.replace(" ", "").upper()
            if LABEL_RE.match(t):
                return b, t
        return None, ""

    def _block_lines(self, rot: np.ndarray, anchor: np.ndarray):
        """Return (deskewed ROI gray, [(y1, y2, x1, x2) per line]) for the lines left-aligned with the anchor."""
        angle = dm.box_angle(anchor)
        ax1, ay1 = anchor.min(axis=0); ax2, ay2 = anchor.max(axis=0)
        ah = ay2 - ay1
        n = self.expected_lines
        rx1 = int(max(0, ax1 - 1.0 * ah))
        rx2 = int(min(rot.shape[1], ax1 + max(1.8 * (ax2 - ax1), 9.0 * ah)))
        ry1 = int(max(0, ay1 - (n + 0.5) * ah))
        ry2 = int(min(rot.shape[0], ay2 + (n + 0.5) * ah))
        roi = rot[ry1:ry2, rx1:rx2]
        if roi.size == 0:
            return None, []
        M = None
        if abs(angle) > 0.3:
            M = cv2.getRotationMatrix2D((roi.shape[1] / 2.0, roi.shape[0] / 2.0), angle, 1.0)
            roi = cv2.warpAffine(roi, M, (roi.shape[1], roi.shape[0]), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        # geometry to map ROI points back to the rotated frame (used for teaching the fast verifier)
        self._roi_geom = (rx1, ry1, M)
        ink = dm.binarize(roi)
        lx = int(ax1 - rx1)
        # label column: the first ~2.5 characters of every line start at the same x
        c1, c2 = max(0, lx - int(0.35 * ah)), min(ink.shape[1], lx + int(1.6 * ah))
        prof = ink[:, c1:c2].sum(axis=1).astype(np.float32)
        # full line width for choosing cut rows: labels of adjacent lines often touch, digits less so
        cw2 = min(ink.shape[1], int(ax2 - rx1))
        prof_wide = ink[:, c1:cw2].sum(axis=1).astype(np.float32)
        acy = (ay1 + ay2) / 2.0 - ry1
        # block extent: label-column runs separated by only a few rows belong to the same block,
        # other print on the carton is separated by a clear gap
        # (other print joined to the block is handled by the line-count choice + label matching)
        runs = [r for r in dm._runs(prof > 1) if r[1] - r[0] >= 3]
        if not runs:
            return roi, []
        join_gap = max(4, int(0.3 * ah))
        blocks = [list(runs[0])]
        for r in runs[1:]:
            if r[0] - blocks[-1][1] <= join_gap:
                blocks[-1][1] = r[1]
            else:
                blocks.append(list(r))
        blk = min(blocks, key=lambda bb: 0 if bb[0] <= acy <= bb[1] else min(abs(bb[0] - acy), abs(bb[1] - acy)))
        top, bottom = blk
        span = bottom - top
        mean_ink = float(prof_wide[top:bottom].mean()) or 1.0

        def split(k):
            """Cut the block into k lines at the emptiest rows near the ideal positions."""
            p = span / float(k)
            cuts, worst = [top], 0.0
            for i in range(1, k):
                centre = top + i * p
                lo = int(max(cuts[-1] + 3, centre - 0.3 * p))
                hi = int(min(bottom - 3, centre + 0.3 * p))
                if hi <= lo:
                    cuts.append(int(round(centre))); worst = max(worst, 1.0); continue
                seg = prof_wide[lo:hi]
                idx = np.where(seg <= seg.min() + 0.5)[0]
                cuts.append(int(lo + idx[np.argmin(np.abs(idx + lo - centre))]))
                worst = max(worst, float(seg.min()) / mean_ink)
            cuts.append(bottom)
            return [[cuts[i], cuts[i + 1]] for i in range(k)], worst, p

        # the block may also hold a line of other print right above / below at the same edge:
        # take the line count whose worst cut is cleanest (cuts through characters score badly)
        options = [split(k) for k in range(n, n + 3) if span / float(k) >= 10]
        if not options:
            return roi, []
        best_worst = min(o[1] for o in options)
        group, _, pitch = next(o for o in options if o[1] <= best_worst + 0.08)
        # horizontal extent of every line: from the label to the last ink before a big gap
        max_len = int(12 * pitch)
        out = []
        for a, b in group:
            rows = np.where(prof[a:b] > 0)[0]
            if rows.size == 0:
                continue
            a2, b2 = a + int(rows[0]), a + int(rows[-1]) + 1
            # extent from the middle rows only: touching neighbours reach into the top/bottom rows
            m = max(1, int(0.2 * (b2 - a2)))
            cols = ink[a2 + m:b2 - m].sum(axis=0) > 0
            x_start = None
            for x in range(max(0, c1 - int(0.5 * pitch)), min(len(cols), c2)):
                if cols[x]:
                    x_start = x; break
            if x_start is None:
                continue
            x_end, gap = x_start, 0
            for x in range(x_start, min(len(cols), x_start + max_len)):
                if cols[x]:
                    x_end, gap = x + 1, 0
                else:
                    gap += 1
                    if gap > 0.9 * pitch:
                        break
            out.append((a2, b2, x_start, x_end))
        return roi, out

    @staticmethod
    def _line_score(text: str, field_name: str) -> float:
        label, value = split_label_value(text)
        s = 0.0
        if label_matches(label, field_name):
            s += 2.0
        elif label[:1].upper() == field_name[0]:
            s += 1.0
        if field_name == "LOT":
            if 3 <= len(normalize_lot(value)) <= 12:
                s += 1.0
        elif re.fullmatch(r"\d{2}-\d{2,4}", normalize_date(value)):
            s += 1.0
        return s

    def _select_lines(self, cand_lines, reads):
        """Pick the consecutive lines whose recognised labels best match LOT / MFG / EXP."""
        n = min(self.expected_lines, len(FIELDS))
        if len(cand_lines) <= n:
            return list(cand_lines), list(reads)
        best, best_s = 0, -1.0
        for s in range(len(cand_lines) - n + 1):
            score = sum(self._line_score(reads[s + k][0], FIELDS[k]) for k in range(n))
            if score > best_s:
                best, best_s = s, score
        return list(cand_lines[best:best + n]), list(reads[best:best + n])

    # ---------------- defects ----------------
    def _blot_defects(self, roi: np.ndarray, lines) -> List[dict]:
        """
        Ink blots / smudges. Printed dots fade when blurred together with the paper
        around them; a pool of ink stays as dark as solid ink. Score = darkness left
        after a sigma-3 blur relative to the ink level (good print <= ~0.89, blots >= ~1.0).
        """
        if not lines:
            return []
        y1 = max(0, min(l[0] for l in lines) - 3); y2 = min(roi.shape[0], max(l[1] for l in lines) + 3)
        x1 = max(0, min(l[2] for l in lines) - 3); x2 = min(roi.shape[1], max(l[3] for l in lines) + 3)
        sub = roi[y1:y2, x1:x2].astype(np.float32)
        if sub.size == 0:
            return []
        return blot_scan(sub, self.blot_threshold, lines_offset=(x1, y1), lines=lines)

    # ---------------- product name ----------------
    def _product_text(self, rot, boxes, block_rect) -> str:
        best = None
        for b in boxes:
            x1, y1 = b.min(axis=0); x2, y2 = b.max(axis=0)
            h, w = y2 - y1, x2 - x1
            if h < 55 or w < 1.8 * h or w > 700:
                continue
            if block_rect is not None and not (x2 < block_rect[0] or x1 > block_rect[2] or
                                               y2 < block_rect[1] or y1 > block_rect[3]):
                continue
            if best is None or h > best[0]:
                best = (h, b)
        if best is None:
            return ""
        b = best[1]
        x1, y1 = np.floor(b.min(axis=0)).astype(int); x2, y2 = np.ceil(b.max(axis=0)).astype(int)
        txt, conf = self._rec(rot[max(0, y1):y2, max(0, x1):x2])
        return txt if conf >= 0.6 else ""

    # ---------------- main ----------------
    def inspect(self, frame: np.ndarray, expected: Optional[dict] = None,
                read_product: bool = True) -> InspectionResult:
        res = InspectionResult()
        t0 = time.perf_counter()
        if frame is None or frame.size == 0:
            res.reasons.append("No image")
            return res
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        expected = expected or {}

        # Pharmacode (original orientation)
        if self.pharma_enabled:
            tp = time.perf_counter()
            pr = locate_and_decode(gray, reverse=self.pharma_reverse)
            res.pharma_code, res.pharma_bbox = pr.value, pr.bbox
            res.timings_ms["pharma"] = round((time.perf_counter() - tp) * 1000, 1)

        rot = self._rotate(gray)
        td = time.perf_counter()
        boxes = self._det(rot)
        res.timings_ms["detect"] = round((time.perf_counter() - td) * 1000, 1)

        ta = time.perf_counter()
        anchor, _ = self._find_anchor(rot, boxes)
        res.timings_ms["anchor"] = round((time.perf_counter() - ta) * 1000, 1)

        block_rect_rot = None
        if anchor is None:
            res.reasons.append("LOT/MFG/EXP print not found")
        else:
            tr = time.perf_counter()
            roi, cand_lines = self._block_lines(rot, anchor)
            ax1, ay1 = anchor.min(axis=0); ah = anchor.max(axis=0)[1] - ay1
            rx1 = int(max(0, ax1 - 1.0 * ah))
            ry1 = int(max(0, ay1 - (self.expected_lines + 0.5) * ah))
            bg = int(np.median(roi)) if roi is not None and roi.size else 255
            reads = []
            for (a, b, x1, x2) in cand_lines:
                lh = b - a
                pad_y, pad_x = max(3, lh // 3), max(4, lh // 2)
                ya, yb = max(0, a - pad_y), min(roi.shape[0], b + pad_y)
                xa, xb = max(0, x1 - pad_x), min(roi.shape[1], x2 + pad_x)
                crop = roi[ya:yb, xa:xb].copy()
                # blank rows that belong to the neighbouring lines
                crop[: max(0, a - 1 - ya)] = bg
                crop[b + 1 - ya:] = bg
                reads.append(self._rec(crop))
            lines, chosen = self._select_lines(cand_lines, reads)
            res.line_texts = [t for t, _ in chosen]
            res.lines_original = [self._roi_rect_to_original((x1, a, x2, b), gray.shape)
                                  for (a, b, x1, x2) in lines]
            if lines:
                bx1 = rx1 + min(l[2] for l in lines); bx2 = rx1 + max(l[3] for l in lines)
                by1 = ry1 + min(l[0] for l in lines); by2 = ry1 + max(l[1] for l in lines)
                block_rect_rot = [bx1, by1, bx2, by2]
                pts = self._to_original(np.array([[bx1, by1], [bx2, by2]], np.float32), gray.shape)
                res.block_bbox = [int(pts[:, 0].min()), int(pts[:, 1].min()),
                                  int(pts[:, 0].max()), int(pts[:, 1].max())]
            for i, (txt, conf) in enumerate(chosen[:len(FIELDS)]):
                name = FIELDS[i]
                label, value = split_label_value(txt)
                fr = res.fields[name]
                fr.raw, fr.conf = txt, round(conf * 100, 1)
                fr.value = normalize_lot(value) if name == "LOT" else normalize_date(value)
                if not fr.value:
                    fr.value = "N/A"
            res.defects = self._blot_defects(roi, lines)
            if len(lines) < self.expected_lines:
                res.defects.append({"type": "MISSING_LINES", "field": "BLOCK",
                                    "detail": f"{len(lines)}/{self.expected_lines} lines found"})
            res.timings_ms["read"] = round((time.perf_counter() - tr) * 1000, 1)

        if read_product:
            tpn = time.perf_counter()
            res.product_text = self._product_text(rot, boxes, block_rect_rot)
            res.timings_ms["product"] = round((time.perf_counter() - tpn) * 1000, 1)

        self._decide(res, expected)
        res.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return res

    def _decide(self, res: InspectionResult, expected: dict):
        reasons = list(res.reasons)
        for name in FIELDS:
            fr = res.fields[name]
            exp_raw = expected.get(name)
            if exp_raw in (None, ""):
                fr.status = "PASS" if fr.value != "N/A" else "FAIL"
                if fr.value == "N/A":
                    fr.reason = fr.reason or "not read"
            else:
                exp_v = normalize_expected(name, exp_raw)
                if fr.value == exp_v:
                    fr.status = "PASS"
                else:
                    fr.status = "FAIL"
                    fr.reason = f"read '{fr.value}' expected '{exp_v}'"
            if fr.status == "PASS" and 0 < fr.conf < self.min_line_conf * 100:
                fr.status = "FAIL"
                fr.reason = f"low confidence {fr.conf:.0f}%"
            if fr.status == "FAIL":
                reasons.append(f"{name}: {fr.reason}")
        # pharmacode
        exp_ph = str(expected.get("PHARMA", "") or "").strip()
        if self.pharma_enabled:
            if exp_ph:
                ok = res.pharma_code is not None and str(res.pharma_code) == exp_ph
                res.pharma_status = "PASS" if ok else "FAIL"
                if not ok:
                    reasons.append(f"PHARMA: read '{res.pharma_code}' expected '{exp_ph}'")
            else:
                res.pharma_status = "PASS" if res.pharma_code is not None else "FAIL"
                if res.pharma_code is None:
                    reasons.append("PHARMA: not found")
        # product name (optional substring check)
        exp_prod = str(expected.get("PRODUCT", "") or "").strip()
        if exp_prod and exp_prod.lower().replace(" ", "") not in res.product_text.lower().replace(" ", ""):
            reasons.append(f"PRODUCT: read '{res.product_text}' expected '{exp_prod}'")
        for d in res.defects:
            reasons.append(f"DEFECT {d['type']} ({d.get('field', '')})")
        res.reasons = reasons
        res.verdict = "PASS" if not reasons else "FAIL"
