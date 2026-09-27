"""
Fast print verification (OCV / golden-sample comparison), < 25 ms per carton.

Teach  : the full OCR inspector (AutoInspector, ~1 s) reads a good carton and confirms
         LOT / MFG / EXP / Pharmacode against the recipe. That carton becomes the golden
         sample: the printed block, every line and every character window are stored.
Verify : for every carton
           1. locate the printed block (coarse-to-fine template matching)
           2. per line: measure left / right shift (handles carton tilt)
           3. per character window: normalised cross-correlation with the golden print
              (a different digit, a missing / extra / smudged character drops the score)
           4. ink blot check on the block
           5. Pharmacode decoded in a small region next to the block
         -> PASS / FAIL with the failing field and character.

Re-teach whenever the batch data (LOT / dates) change.
"""
import os
import time
import pickle
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.pharmacode_locator import locate_and_decode
from core.auto_inspector import blot_scan, FIELDS

COARSE = 4          # coarse search downscale factor


@dataclass
class GoldenLine:
    field: str
    text: str
    rect: Tuple[int, int, int, int]                   # x1, y1, x2, y2 in the rotated teach frame
    halves: List[Tuple[Tuple[int, int, int, int], np.ndarray]] = field(default_factory=list)
    windows: List[Tuple[Tuple[int, int, int, int], np.ndarray]] = field(default_factory=list)
    chars: List[str] = field(default_factory=list)    # character(s) each window mostly covers


@dataclass
class GoldenModel:
    recipe: str
    rotation: int
    frame_shape: Tuple[int, int]
    block_rect: Tuple[int, int, int, int]
    block_tpl: np.ndarray
    block_tpl_small: np.ndarray
    lines: List[GoldenLine]
    expected: Dict[str, str]
    pharma_code: Optional[int]
    pharma_rect: Optional[Tuple[int, int, int, int]]  # rotated frame
    ink_threshold: float = 128.0                       # grey level between paper and ink on the golden
    created: float = 0.0


@dataclass
class VerifyResult:
    verdict: str = "FAIL"
    reasons: List[str] = field(default_factory=list)
    block_score: float = 0.0
    block_offset: Tuple[int, int] = (0, 0)
    field_scores: Dict[str, float] = field(default_factory=dict)
    worst_window: Dict[str, dict] = field(default_factory=dict)
    pharma_code: Optional[int] = None
    defects: List[dict] = field(default_factory=list)
    timings_ms: Dict[str, float] = field(default_factory=dict)


def _rotate(gray: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 90:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(gray, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def _orig_to_rot(pts: np.ndarray, orig_shape, rotation: int) -> np.ndarray:
    H, W = orig_shape[:2]
    x, y = pts[:, 0], pts[:, 1]
    if rotation == 90:
        return np.stack([H - 1 - y, x], axis=1)
    if rotation == 180:
        return np.stack([W - 1 - x, H - 1 - y], axis=1)
    if rotation == 270:
        return np.stack([y, W - 1 - x], axis=1)
    return pts


def _rot_to_orig(pts: np.ndarray, orig_shape, rotation: int) -> np.ndarray:
    H, W = orig_shape[:2]
    x, y = pts[:, 0], pts[:, 1]
    if rotation == 90:
        return np.stack([y, H - 1 - x], axis=1)
    if rotation == 180:
        return np.stack([W - 1 - x, H - 1 - y], axis=1)
    if rotation == 270:
        return np.stack([W - 1 - y, x], axis=1)
    return pts


def _rot_region(gray: np.ndarray, rotation: int, x1: int, y1: int, x2: int, y2: int):
    """
    The rectangle [x1:x2, y1:y2] of the *rotated* frame, cut from the camera frame and rotated
    (only this small region is rotated). Parts outside the frame are edge-replicated.
    Returns (image, (x1, y1)) - image pixel (0, 0) is rotated-frame point (x1, y1).
    """
    H0, W0 = gray.shape[:2]
    Hr, Wr = (W0, H0) if rotation in (90, 270) else (H0, W0)
    cx1, cy1, cx2, cy2 = max(0, x1), max(0, y1), min(Wr, x2), min(Hr, y2)
    if cx2 <= cx1 or cy2 <= cy1:
        return np.zeros((max(1, y2 - y1), max(1, x2 - x1)), gray.dtype), (x1, y1)
    if rotation == 90:
        crop = cv2.rotate(gray[H0 - cx2:H0 - cx1, cy1:cy2], cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        crop = cv2.rotate(gray[H0 - cy2:H0 - cy1, W0 - cx2:W0 - cx1], cv2.ROTATE_180)
    elif rotation == 270:
        crop = cv2.rotate(gray[cx1:cx2, W0 - cy2:W0 - cy1], cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        crop = gray[cy1:cy2, cx1:cx2]
    pads = (cy1 - y1, y2 - cy2, cx1 - x1, x2 - cx2)
    if any(pads):
        crop = cv2.copyMakeBorder(crop, *pads, cv2.BORDER_REPLICATE)
    return crop, (x1, y1)


def _match(img: np.ndarray, tpl: np.ndarray, cx: int, cy: int, r: int) -> Tuple[float, int, int]:
    """Best NCC of tpl around top-left (cx, cy) within radius r. Returns (score, x, y)."""
    th, tw = tpl.shape[:2]
    H, W = img.shape[:2]
    x1, y1 = max(0, cx - r), max(0, cy - r)
    x2, y2 = min(W, cx + r + tw), min(H, cy + r + th)
    if x2 - x1 < tw or y2 - y1 < th:
        return -1.0, cx, cy
    res = cv2.matchTemplate(img[y1:y2, x1:x2], tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), x1 + loc[0], y1 + loc[1]


def _ink(gray: np.ndarray, thr: float) -> np.ndarray:
    return (gray < thr).astype(np.uint8)


def _ink_diff(test: np.ndarray, thr_t: float, gold_ink: np.ndarray, gold_dil: np.ndarray,
              gold_sum: int, kernel: np.ndarray) -> Tuple[float, float]:
    """
    Ink present in one print but not within the tolerance of the other, as a fraction
    of the golden character's ink: (extra ink in test, ink missing from test).
    Only the middle rows are compared (neighbouring lines can touch the window edges).
    """
    it = _ink(test, thr_t)
    h = it.shape[0]
    a, b = int(0.12 * h), max(int(0.12 * h) + 1, int(0.88 * h))
    it, gi, gd = it[a:b], gold_ink[a:b], gold_dil[a:b]
    extra = int((it & (1 - gd)).sum())
    missing = int((gi & (1 - cv2.dilate(it, kernel))).sum())
    d = float(max(1, gold_sum))
    return extra / d, missing / d


class FastVerifier:
    SCALES_Y = (0.80, 0.86, 0.93, 1.0, 1.07, 1.14, 1.21, 1.28)

    def __init__(self, window_threshold: float = 0.55, block_threshold: float = 0.40,
                 blot_threshold: float = 0.95, diff_area_ratio: float = 0.11,
                 line_search: int = 14, window_search: int = 3, diff_tolerance: int = 1,
                 pharma_margin: int = 70, pharma_reverse: bool = False):
        """
        diff_area_ratio: a character fails when ink added or missing (beyond `diff_tolerance` px)
                         exceeds this fraction of the golden character's ink.
        """
        self.window_threshold = window_threshold
        self.block_threshold = block_threshold
        self.blot_threshold = blot_threshold
        self.diff_area_ratio = diff_area_ratio
        self.line_search = line_search
        self.window_search = window_search
        self.diff_tolerance = diff_tolerance
        self.pharma_margin = pharma_margin
        self.pharma_reverse = pharma_reverse
        self.model: Optional[GoldenModel] = None
        self._scaled_cache: Dict[float, dict] = {}
        self._last_sy = 1.0

    # ------------------------------------------------------------------ teach
    def teach(self, frame: np.ndarray, insp_result, recipe: str = "", rotation: int = 90,
              expected: Optional[dict] = None) -> GoldenModel:
        """Build the golden model from a carton that the full OCR inspector passed."""
        if insp_result.verdict != "PASS":
            raise ValueError("golden sample must PASS the full OCR inspection: "
                             + "; ".join(insp_result.reasons))
        if not insp_result.lines_original:
            raise ValueError("no line geometry in inspection result")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        rot = _rotate(gray, rotation)
        H, W = rot.shape[:2]

        lines = []
        for i, corners in enumerate(insp_result.lines_original[:len(FIELDS)]):
            pts = _orig_to_rot(np.array(corners, np.float32).reshape(4, 2), gray.shape, rotation)
            x1, y1 = np.floor(pts.min(axis=0)).astype(int)
            x2, y2 = np.ceil(pts.max(axis=0)).astype(int)
            # generous right margin: the line end found by OCR can stop inside the last character
            lh = y2 - y1
            x1, y1, x2, y2 = max(0, x1 - 3), max(0, y1 - 1), min(W, x2 + max(6, lh // 2)), min(H, y2 + 1)
            text = insp_result.line_texts[i].replace(" ", "") if i < len(insp_result.line_texts) else ""
            gl = GoldenLine(field=FIELDS[i], text=text, rect=(x1, y1, x2, y2))
            w = x2 - x1
            # two halves for the per-line shift / tilt estimate
            for hx1, hx2 in ((x1, x1 + w // 2), (x1 + w // 2, x2)):
                gl.halves.append(((hx1, y1, hx2, y2), rot[y1:y2, hx1:hx2].copy()))
            lines.append(gl)

        bx1 = max(0, min(l.rect[0] for l in lines) - 10); by1 = max(0, min(l.rect[1] for l in lines) - 8)
        bx2 = min(W, max(l.rect[2] for l in lines) + 10); by2 = min(H, max(l.rect[3] for l in lines) + 8)
        block = rot[by1:by2, bx1:bx2].copy()
        thr = self._ink_threshold(block)
        # character windows: one window per printed character, cut at the gaps between characters
        for gl in lines:
            x1, y1, x2, y2 = gl.rect
            text = "".join(c for c in gl.text.upper() if c.isalnum() or c in ":-/.")
            text = text.rstrip(".")
            for (sx1, sx2) in self._segment_chars(rot[y1:y2, x1:x2], thr, len(text)):
                wx1, wx2 = max(x1, x1 + sx1 - 2), min(x2, x1 + sx2 + 2)
                gl.windows.append(((wx1, y1, wx2, y2), rot[y1:y2, wx1:wx2].copy()))
            gl.chars = [text[j] if j < len(text) else "?" for j in range(len(gl.windows))]
        small = cv2.resize(block, None, fx=1.0 / COARSE, fy=1.0 / COARSE, interpolation=cv2.INTER_AREA)

        pharma_rect = None
        if insp_result.pharma_bbox:
            px1, py1, px2, py2 = insp_result.pharma_bbox
            pts = _orig_to_rot(np.array([[px1, py1], [px2, py2]], np.float32), gray.shape, rotation)
            pharma_rect = (int(pts[:, 0].min()), int(pts[:, 1].min()), int(pts[:, 0].max()), int(pts[:, 1].max()))

        exp = dict(expected or {})
        for k_ in FIELDS:
            exp.setdefault(k_, insp_result.fields[k_].value)
        self.model = GoldenModel(recipe=recipe, rotation=rotation, frame_shape=gray.shape[:2],
                                 block_rect=(bx1, by1, bx2, by2), block_tpl=block, block_tpl_small=small,
                                 lines=lines, expected=exp, pharma_code=insp_result.pharma_code,
                                 pharma_rect=pharma_rect, ink_threshold=self._ink_threshold(block),
                                 created=time.time())
        self._scaled_cache = {}
        return self.model

    @staticmethod
    def _segment_chars(line: np.ndarray, thr: float, k: int) -> List[Tuple[int, int]]:
        """Column ranges of the k characters of a golden line (gaps first, then even splits)."""
        h = line.shape[0]
        core = line[int(0.2 * h):max(int(0.2 * h) + 1, int(0.8 * h))]
        cols = (core < thr).sum(axis=0) > 0
        segs, start = [], None
        for x, a in enumerate(cols):
            if a and start is None:
                start = x
            elif not a and start is not None:
                segs.append([start, x]); start = None
        if start is not None:
            segs.append([start, len(cols)])
        # drop specks
        segs = [s for s in segs if (core[:, s[0]:s[1]] < thr).sum() >= 4]
        if not segs:
            return []
        if k <= 0:
            return [tuple(s) for s in segs]
        # too many pieces: close the smallest gaps (a character split by a missing dot column)
        while len(segs) > k:
            gaps = [segs[i + 1][0] - segs[i][1] for i in range(len(segs) - 1)]
            i = int(np.argmin(gaps))
            segs[i:i + 2] = [[segs[i][0], segs[i + 1][1]]]
        # too few: split the widest piece (touching characters) into equal parts
        while len(segs) < k:
            widths = [s[1] - s[0] for s in segs]
            i = int(np.argmax(widths))
            typical = float(np.median(widths)) if len(widths) > 1 else widths[i] / 2.0
            parts = max(2, min(k - len(segs) + 1, int(round(widths[i] / max(1.0, typical)))))
            a, b = segs[i]
            step = (b - a) / float(parts)
            segs[i:i + 1] = [[int(round(a + p * step)), int(round(a + (p + 1) * step))] for p in range(parts)]
        return [tuple(s) for s in segs]

    def _small_template_orig(self) -> np.ndarray:
        """Coarse block template in camera orientation (for the full-frame search)."""
        if "_small_orig" not in self._scaled_cache:
            back = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180,
                    270: cv2.ROTATE_90_CLOCKWISE}.get(self.model.rotation)
            t = self.model.block_tpl_small
            self._scaled_cache["_small_orig"] = cv2.rotate(t, back) if back is not None else t
        return self._scaled_cache["_small_orig"]

    @staticmethod
    def _ink_threshold(block: np.ndarray) -> float:
        return (float(np.median(block)) + float(np.percentile(block, 2))) / 2.0

    def _scaled(self, sy: float) -> dict:
        """Golden geometry + templates stretched vertically by sy (relative to the block top-left)."""
        key = round(sy, 3)
        if key in self._scaled_cache:
            return self._scaled_cache[key]
        m = self.model
        bx1, by1, bx2, by2 = m.block_rect

        def rs(img):
            h = max(4, int(round(img.shape[0] * sy)))
            return img if key == 1.0 else cv2.resize(img, (img.shape[1], h), interpolation=cv2.INTER_LINEAR)

        def rr(rect):
            x1, y1, x2, y2 = rect
            return (x1 - bx1, int(round((y1 - by1) * sy)), x2 - bx1, int(round((y1 - by1) * sy)) + int(round((y2 - y1) * sy)))

        kern = np.ones((2 * self.diff_tolerance + 1, 2 * self.diff_tolerance + 1), np.uint8)

        def win(r, t):
            tpl = rs(t)
            gi = _ink(tpl, m.ink_threshold)
            h = gi.shape[0]
            a, b = int(0.12 * h), max(int(0.12 * h) + 1, int(0.88 * h))
            return rr(r), tpl, gi, cv2.dilate(gi, kern), int(gi[a:b].sum())

        block = rs(m.block_tpl)
        d = {
            "block": block,
            "block_half": cv2.resize(block, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
            "block_size": (block.shape[1], block.shape[0]),
            "kernel": kern,
            "lines": [{
                "field": gl.field, "chars": gl.chars,
                "halves": [(rr(r), rs(t)) for r, t in gl.halves],
                "windows": [win(r, t) for r, t in gl.windows],
            } for gl in m.lines],
        }
        if m.pharma_rect is not None:
            # the pharmacode is pre-printed on the carton: its offset is not stretched with the inkjet print
            px1, py1, px2, py2 = m.pharma_rect
            d["pharma"] = (px1 - bx1, py1 - by1, px2 - bx1, py2 - by1)
        self._scaled_cache[key] = d
        return d

    def save(self, path: str):
        with open(path, "wb") as fh:
            pickle.dump(self.model, fh)

    def load(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        with open(path, "rb") as fh:
            self.model = pickle.load(fh)
        self._scaled_cache = {}
        return True

    # ----------------------------------------------------------------- verify
    def verify(self, frame: np.ndarray) -> VerifyResult:
        res = VerifyResult()
        m = self.model
        t0 = time.perf_counter()
        if m is None:
            res.reasons.append("not taught")
            return res
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        t1 = time.perf_counter()

        # 1. block position: coarse over the whole frame (1/4 res, camera orientation, no full-frame rotate) ...
        small = cv2.resize(gray, None, fx=1.0 / COARSE, fy=1.0 / COARSE, interpolation=cv2.INTER_AREA)
        tpl_o = self._small_template_orig()
        # pad so a block that partly leaves the frame is still found (and then reported)
        py_, px_ = tpl_o.shape[0] // 3, tpl_o.shape[1] // 3
        small = cv2.copyMakeBorder(small, py_, py_, px_, px_, cv2.BORDER_REPLICATE)
        r = cv2.matchTemplate(small, tpl_o, cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(r)
        loc = (loc[0] - px_, loc[1] - py_)
        oh, ow = m.block_tpl.shape[:2]
        if m.rotation in (90, 270):
            oh, ow = ow, oh                      # block size in camera orientation
        c = _orig_to_rot(np.array([[loc[0] * COARSE, loc[1] * COARSE],
                                   [loc[0] * COARSE + ow - 1, loc[1] * COARSE + oh - 1]], np.float32),
                         gray.shape, m.rotation)
        cx, cy = int(c[:, 0].min()), int(c[:, 1].min())
        # ... only the neighbourhood of the block is rotated (padded where it leaves the frame) ...
        bw, bh = m.block_tpl.shape[1], int(m.block_tpl.shape[0] * max(self.SCALES_Y)) + 1
        MG = COARSE * 2 + self.line_search + 12
        rot, (lx0, ly0) = _rot_region(gray, m.rotation, cx - MG, cy - MG, cx + bw + MG, cy + bh + MG)
        # ... then print height (printhead setting / angle changes it) at 1/2 res ...
        local = cv2.resize(rot, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        x1 = y1 = 0
        best = (-1.0, 1.0, MG, MG)

        def try_scale(sy_):
            nonlocal best
            tpl = self._scaled(sy_)["block_half"]
            if tpl.shape[0] > local.shape[0] or tpl.shape[1] > local.shape[1]:
                return
            rr_ = cv2.matchTemplate(local, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, l2 = cv2.minMaxLoc(rr_)
            if mx > best[0]:
                best = (float(mx), sy_, x1 + 2 * l2[0], y1 + 2 * l2[1])

        # consecutive cartons share the printhead setting: try the last scale and its neighbours first
        i0 = self.SCALES_Y.index(self._last_sy) if self._last_sy in self.SCALES_Y else self.SCALES_Y.index(1.0)
        near = [self.SCALES_Y[i] for i in (i0 - 1, i0, i0 + 1) if 0 <= i < len(self.SCALES_Y)]
        for s_ in near:
            try_scale(s_)
        if best[0] < 0.75 or best[1] != self._last_sy:
            for s_ in self.SCALES_Y:
                if s_ not in near:
                    try_scale(s_)
        _, sy, gx, gy = best
        self._last_sy = sy
        S = self._scaled(sy)
        # ... and full-resolution refinement
        score, bx, by = _match(rot, S["block"], gx, gy, 3)
        res.block_score = round(score, 3)
        res.block_offset = (int(bx + lx0 - m.block_rect[0]), int(by + ly0 - m.block_rect[1]))
        t2 = time.perf_counter()
        if score < self.block_threshold:
            res.reasons.append(f"print block not found (score {score:.2f})")
            res.timings_ms = {"total": round((time.perf_counter() - t0) * 1000, 2)}
            return res
        # printed lines partly outside the camera view cannot be verified (trigger / position issue)
        H0, W0 = gray.shape[:2]
        Hr, Wr = (W0, H0) if m.rotation in (90, 270) else (H0, W0)
        gx1, gy1 = bx + lx0, by + ly0
        lines_x1 = min(L["halves"][0][0][0] for L in S["lines"])
        lines_y1 = min(L["halves"][0][0][1] for L in S["lines"])
        lines_x2 = max(L["halves"][1][0][2] for L in S["lines"])
        lines_y2 = max(L["halves"][1][0][3] for L in S["lines"])
        if gx1 + lines_x1 < 0 or gy1 + lines_y1 < 0 or gx1 + lines_x2 > Wr or gy1 + lines_y2 > Hr:
            res.reasons.append("print partly outside the camera view (check trigger / camera position)")
            res.defects.append({"type": "OUT_OF_VIEW", "field": "BLOCK"})
            res.timings_ms = {"total": round((time.perf_counter() - t0) * 1000, 2)}
            return res

        blk_w, blk_h = S["block_size"]
        blk = rot[by:by + blk_h, bx:bx + blk_w]
        thr_t = self._ink_threshold(blk) if blk.size else m.ink_threshold

        # 2-3. lines -> character windows: correlation + ink difference against the golden print
        for L in S["lines"]:
            ends = []
            for (hx1, hy1, hx2, hy2), tpl in L["halves"]:
                s, mx, my = _match(rot, tpl, bx + hx1, by + hy1, self.line_search)
                ends.append((hx1 + (hx2 - hx1) / 2.0, mx - (bx + hx1), my - (by + hy1)))
            (cxl, oxl, oyl), (cxr, oxr, oyr) = ends
            worst_s, worst_j, worst_diff = 1.0, -1, 0.0
            fail_j, fail_msg = -1, ""
            for j, ((wx1, wy1, wx2, wy2), tpl, gink, gdil, gsum) in enumerate(L["windows"]):
                cxw = (wx1 + wx2) / 2.0
                f = 0.0 if cxr == cxl else (cxw - cxl) / (cxr - cxl)
                ox = oxl + f * (oxr - oxl); oy = oyl + f * (oyr - oyl)
                s, mx, my = _match(rot, tpl, int(round(bx + wx1 + ox)), int(round(by + wy1 + oy)),
                                   self.window_search)
                th, tw = tpl.shape[:2]
                patch = rot[my:my + th, mx:mx + tw]
                extra = missing = 1.0
                if patch.shape == tpl.shape:
                    extra, missing = _ink_diff(patch, thr_t, gink, gdil, gsum, S["kernel"])
                diff = max(extra, missing)
                if s < worst_s:
                    worst_s = s
                if diff > worst_diff:
                    worst_diff, worst_j = diff, j
                if fail_j < 0 and (s < self.window_threshold or diff > self.diff_area_ratio):
                    fail_j = j
                    kind = "extra ink" if extra >= missing else "missing ink"
                    fail_msg = (f"{L['field']}: character {j + 1} ('{L['chars'][j]}') {kind} "
                                f"{diff * 100:.0f}% / corr {s:.2f}")
            res.field_scores[L["field"]] = round(worst_s, 3)
            res.worst_window[L["field"]] = {"index": worst_j, "diff": round(worst_diff, 3),
                                            "char": L["chars"][worst_j] if worst_j >= 0 else ""}
            if fail_j >= 0:
                res.reasons.append(fail_msg)
        t3 = time.perf_counter()

        # 4. ink blots on the found block (margins excluded: carton edge / neighbouring print)
        if blk.size:
            inner = blk[6:-6, 8:-8] if blk.shape[0] > 20 and blk.shape[1] > 24 else blk
            res.defects = blot_scan(inner, self.blot_threshold)
            for d in res.defects:
                res.reasons.append(f"DEFECT INK_BLOT (score {d['score']:.2f})")
        t4 = time.perf_counter()

        # 5. pharmacode near its taught position (decoded in camera orientation: no rotation needed)
        if "pharma" in S:
            px1, py1, px2, py2 = S["pharma"]
            pm = self.pharma_margin
            gbx, gby = bx + lx0, by + ly0
            q = _rot_to_orig(np.array([[gbx + px1 - pm, gby + py1 - pm], [gbx + px2 + pm, gby + py2 + pm]],
                                      np.float32), gray.shape, m.rotation)
            H0, W0 = gray.shape[:2]
            ox1, oy1 = max(0, int(q[:, 0].min())), max(0, int(q[:, 1].min()))
            ox2, oy2 = min(W0, int(q[:, 0].max())), min(H0, int(q[:, 1].max()))
            crop = gray[oy1:oy2, ox1:ox2]
            pr = locate_and_decode(crop, reverse=self.pharma_reverse, row_step=2, min_rows=8) if crop.size else None
            res.pharma_code = pr.value if pr else None
            exp_ph = str(m.expected.get("PHARMA") or m.pharma_code or "")
            if exp_ph and str(res.pharma_code) != exp_ph:
                res.reasons.append(f"PHARMA: read '{res.pharma_code}' expected '{exp_ph}'")
        t5 = time.perf_counter()

        res.verdict = "PASS" if not res.reasons else "FAIL"
        res.timings_ms = {
            "gray": round((t1 - t0) * 1000, 2), "locate": round((t2 - t1) * 1000, 2),
            "chars": round((t3 - t2) * 1000, 2), "blots": round((t4 - t3) * 1000, 2),
            "pharma": round((t5 - t4) * 1000, 2), "total": round((t5 - t0) * 1000, 2),
            "scale_y": sy,
        }
        return res

