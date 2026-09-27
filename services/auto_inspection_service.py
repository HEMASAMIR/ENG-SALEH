"""
Automatic inspection service used by ProcessingThread when inspection_engine == "auto".

Flow per recipe / batch:
  * TEACH : the first carton after Start (or after the expected values change) is read by
            the full OCR inspector (~1 s). If it matches the recipe and has no print defect
            it becomes the golden sample (saved to golden/ so a restart keeps it).
  * VERIFY: every following carton is checked against the golden sample by the fast
            verifier (~15-25 ms): print position, every character, ink blots, Pharmacode.

The returned dict uses the same keys as the classic pipeline so the UI, PLC signalling
and counters keep working unchanged; "auto_verdict" carries the final decision.
"""
import os
import sys
import json
import time
import hashlib
import threading
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

from core.auto_inspector import (AutoInspector, FIELDS, normalize_expected, label_matches, split_label_value,
                                 normalize_lot, normalize_date)
from core.fast_verifier import FastVerifier, _rot_to_orig
from services.inspection_log_service import InspectionLogService


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _recipe_key(expected: dict) -> str:
    norm = {k: normalize_expected(k, expected.get(k, "")) for k in FIELDS}
    norm["PHARMA"] = str(expected.get("PHARMA", "") or "").strip()
    raw = json.dumps(norm, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class AutoInspectionService:
    def __init__(self, rotation: int = 90, save_pass_images: bool = False, log_results: bool = True,
                 escalation_threshold: Optional[float] = 0.03):
        """
        escalation_threshold: golden-difference above which a line is also read by the OCR
        recogniser before PASS (None = fast check only). 0.03 caught every defect in the
        sample tests; those cartons take ~60-120 ms instead of ~15 ms.
        """
        self.inspector = AutoInspector(rotation=rotation)
        self.verifier = FastVerifier()
        self.escalation_threshold = escalation_threshold
        self.rotation = rotation
        self.golden_dir = os.path.join(_base_dir(), "golden")
        os.makedirs(self.golden_dir, exist_ok=True)
        self.golden_key: Optional[str] = None
        self.logger = InspectionLogService(save_pass_images=save_pass_images) if log_results else None
        self._ready = threading.Event()
        threading.Thread(target=self._warmup, name="OCRWarmup", daemon=True).start()

    def _warmup(self):
        try:
            t = time.perf_counter()
            self.inspector.warmup()
            print(f"[AutoInspection] OCR engine ready ({(time.perf_counter() - t):.1f} s)")
        except Exception as e:
            print(f"[AutoInspection] OCR engine failed to load: {e}")
        self._ready.set()

    # ------------------------------------------------------------ golden state
    def reset_golden(self):
        """Force the next carton to be taught (new batch / operator request)."""
        self.verifier.model = None
        self.verifier._scaled_cache = {}
        self.golden_key = None

    def _golden_path(self, key: str) -> str:
        return os.path.join(self.golden_dir, f"golden_{key}.pkl")

    def _ensure_golden_for(self, key: str):
        if self.golden_key == key and self.verifier.model is not None:
            return
        self.reset_golden()
        path = self._golden_path(key)
        try:
            if self.verifier.load(path):
                self.golden_key = key
                print(f"[AutoInspection] golden sample loaded ({path})")
        except Exception as e:
            print(f"[AutoInspection] could not load golden sample: {e}")

    # ----------------------------------------------------------------- process
    def process(self, frame_bgr: np.ndarray, expected: Optional[dict], force_teach: bool = False) -> dict:
        t0 = time.perf_counter()
        expected = {k: v for k, v in (expected or {}).items() if v not in (None, "")}
        key = _recipe_key(expected)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        if force_teach:
            self.reset_golden()
            try:
                os.remove(self._golden_path(key))
            except OSError:
                pass
        self._ensure_golden_for(key)

        if self.verifier.model is None:
            out = self._teach(gray, expected, key)
        else:
            out = self._verify(gray)
        out["auto_total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        if self.logger is not None:
            self.logger.log(frame_bgr, {
                "ts": datetime.now().isoformat(timespec="milliseconds"), "recipe": key,
                "mode": out.get("auto_mode"), "verdict": out.get("auto_verdict"),
                "LOT": out.get("LOT"), "MFG": out.get("MFG"), "EXP": out.get("EXP"),
                "PHARMA": out.get("PHARMA_CODE"), "product": out.get("PRODUCT", ""),
                "reasons": out.get("auto_reasons", []), "time_ms": out["auto_total_ms"],
            })
        return out

    # ------------------------------------------------------------------ teach
    def _teach(self, gray: np.ndarray, expected: dict, key: str) -> dict:
        if not self._ready.wait(timeout=60):
            return self._error("OCR engine not ready")
        r = self.inspector.inspect(gray, expected)
        out = self._from_full(r)
        out["auto_mode"] = "TEACH"
        if r.verdict == "PASS":
            try:
                self.verifier.teach(gray, r, recipe=key, rotation=self.rotation, expected=expected)
                self.verifier.save(self._golden_path(key))
                self._warm_confirmation(gray)
                self.golden_key = key
                out["auto_reasons"] = ["golden sample taught from this carton"]
                print(f"[AutoInspection] golden sample taught: {r.line_texts} pharma={r.pharma_code}")
            except Exception as e:
                out["auto_reasons"] = [f"teach failed: {e}"]
                out["auto_verdict"] = "FAIL"
        return out

    def _from_full(self, r) -> dict:
        labels_ok = True
        for i, name in enumerate(FIELDS):
            raw = r.fields[name].raw
            if raw and not label_matches(split_label_value(raw)[0], name):
                labels_ok = False
        confs = [r.fields[k].conf for k in FIELDS if r.fields[k].conf > 0]
        smudged = [{"field": d.get("field") if d.get("field") in FIELDS else "VALUES", "reason": d.get("type"),
                    "char": ""} for d in r.defects if d.get("type") == "INK_BLOT"]
        return {
            "LOT": r.fields["LOT"].value, "MFG": r.fields["MFG"].value, "EXP": r.fields["EXP"].value,
            "ocr_conf": round(float(np.mean(confs)), 1) if confs else 0.0,
            "PHARMA_CODE": str(r.pharma_code) if r.pharma_code is not None else "N/A",
            "PRODUCT": r.product_text,
            "LABELS_STATUS": "PASS" if labels_ok else "FAIL",
            "LABELS_TEXT": "LOT : MFG : EXP :" if labels_ok else " ".join(split_label_value(t)[0] for t in r.line_texts),
            "LABELS_ERROR": "" if labels_ok else "label mismatch",
            "labels_conf": 0.0,
            "smudged_chars": smudged,
            "ocr_time_ms": r.timings_ms.get("total", 0.0),
            "labels_time_ms": 0.0,
            "pharma_time_ms": r.timings_ms.get("pharma", 0.0),
            "locator_found": r.block_bbox is not None,
            "locator_score": 1.0 if r.block_bbox else 0.0,
            "locator_dx": 0, "locator_dy": 0, "locator_bbox": r.block_bbox, "locator_time_ms": 0.0,
            "shifted_roi_date": r.block_bbox, "shifted_roi_pharma": r.pharma_bbox, "shifted_roi_labels": None,
            "auto_verdict": r.verdict, "auto_reasons": list(r.reasons), "auto_mode": "FULL",
        }

    # ----------------------------------------------------------------- verify
    # recogniser confusions seen on GOOD prints of this dot-matrix font (dotted zero, open 9)
    OCR_TOLERATED = {"9": set("35"), "0": set("8")}

    def _escalate(self, v, expected: dict) -> None:
        """
        Characters that are close to, but not clearly beyond, the golden-difference limit are
        confirmed by reading their line with the OCR recogniser (whole value, recipe comparison).
        Adds reasons to `v` and sets FAIL when a value does not read as the recipe.
        """
        lo = self.escalation_threshold
        if lo is None or v.verdict != "PASS":
            return
        fields = [f for f in FIELDS if lo < v.worst_window.get(f, {}).get("diff", 0.0)
                  and f in v.line_crops and v.line_crops[f].size]
        if not fields:
            return
        t = time.perf_counter()
        crops = [cv2.cvtColor(v.line_crops[f], cv2.COLOR_GRAY2BGR) for f in fields]
        with self.inspector._rec_lock:
            out, _ = self.inspector.engine().text_rec(crops)
        for f, (txt, conf) in zip(fields, out):
            _, val = split_label_value(txt)
            got = normalize_lot(val) if f == "LOT" else normalize_date(val)
            want = normalize_expected(f, expected.get(f, ""))
            ok = len(got) == len(want) and all(g == w or g in self.OCR_TOLERATED.get(w, ())
                                               for g, w in zip(got, want))
            v.escalated[f] = got
            if want and not ok:
                v.reasons.append(f"{f}: reads '{got}' expected '{want}' (OCR confirmation)")
        v.verdict = "PASS" if not v.reasons else "FAIL"
        v.timings_ms["ocr_confirm"] = round((time.perf_counter() - t) * 1000, 1)

    def _warm_confirmation(self, gray: np.ndarray):
        """Run the OCR confirmation once on the golden print so the first real carton is not slowed."""
        if self.escalation_threshold is None:
            return
        try:
            v = self.verifier.verify(gray, collect_lines=True)
            crops = [cv2.cvtColor(c, cv2.COLOR_GRAY2BGR) for c in v.line_crops.values() if c.size]
            with self.inspector._rec_lock:
                for k in range(1, len(crops) + 1):        # every batch size that can occur
                    self.inspector.engine().text_rec(crops[:k])
        except Exception as e:
            print(f"[AutoInspection] confirmation warm-up skipped: {e}")

    def _verify(self, gray: np.ndarray) -> dict:
        v = self.verifier.verify(gray, collect_lines=self.escalation_threshold is not None)
        v.escalated = {}
        m = self.verifier.model
        self._escalate(v, m.expected)
        failed_fields = set()
        for reason in v.reasons:
            for f in FIELDS + ("PHARMA",):
                if reason.startswith(f + ":"):
                    failed_fields.add(f)
        values = {}
        for f in FIELDS:
            gold = normalize_expected(f, m.expected.get(f, "")) or "N/A"
            if f in failed_fields:
                idx = v.worst_window.get(f, {}).get("index", -1)
                values[f] = f"MISMATCH (char {idx + 1})" if idx >= 0 else "MISMATCH"
            elif v.block_score <= 0 or any(r.startswith("print") for r in v.reasons):
                values[f] = "N/A"
            else:
                values[f] = gold
        # block rectangle back in camera coordinates (for overlays / logs)
        bbox = None
        if v.block_score > 0:
            x1, y1, x2, y2 = m.block_rect
            dx, dy = v.block_offset
            pts = _rot_to_orig(np.array([[x1 + dx, y1 + dy], [x2 + dx, y2 + dy]], np.float32),
                               m.frame_shape, m.rotation)
            bbox = [int(pts[:, 0].min()), int(pts[:, 1].min()), int(pts[:, 0].max()), int(pts[:, 1].max())]
        smudged = [{"field": "VALUES", "reason": d.get("type"), "char": ""} for d in v.defects
                   if d.get("type") == "INK_BLOT"]
        label_fail = any(":" in r and "character" in r and int(r.split("character ")[1].split(" ")[0]) <= 3
                         for r in v.reasons if "character" in r)
        scores = [s for s in v.field_scores.values()]
        return {
            "LOT": values["LOT"], "MFG": values["MFG"], "EXP": values["EXP"],
            "ocr_conf": round(100.0 * min(scores), 1) if scores else 0.0,
            "PHARMA_CODE": str(v.pharma_code) if v.pharma_code is not None else "N/A",
            "PRODUCT": "",
            "LABELS_STATUS": "FAIL" if label_fail else "PASS",
            "LABELS_TEXT": "LOT : MFG : EXP :",
            "LABELS_ERROR": "label differs from golden" if label_fail else "",
            "labels_conf": 0.0,
            "smudged_chars": smudged,
            "ocr_time_ms": v.timings_ms.get("total", 0.0),
            "labels_time_ms": 0.0,
            "pharma_time_ms": v.timings_ms.get("pharma", 0.0),
            "locator_found": v.block_score >= self.verifier.block_threshold,
            "locator_score": v.block_score,
            "locator_dx": v.block_offset[0], "locator_dy": v.block_offset[1],
            "locator_bbox": bbox, "locator_time_ms": v.timings_ms.get("locate", 0.0),
            "shifted_roi_date": bbox, "shifted_roi_pharma": None, "shifted_roi_labels": None,
            "auto_verdict": v.verdict, "auto_reasons": list(v.reasons), "auto_mode": "VERIFY",
            "auto_timings_ms": v.timings_ms,
        }

    @staticmethod
    def _error(msg: str) -> dict:
        return {"LOT": "N/A", "MFG": "N/A", "EXP": "N/A", "ocr_conf": 0.0, "PHARMA_CODE": "N/A",
                "LABELS_STATUS": "N/A", "LABELS_TEXT": "N/A", "LABELS_ERROR": msg, "labels_conf": 0.0,
                "smudged_chars": [], "ocr_time_ms": 0.0, "labels_time_ms": 0.0, "pharma_time_ms": 0.0,
                "locator_found": False, "locator_score": 0.0, "locator_dx": 0, "locator_dy": 0,
                "locator_bbox": None, "locator_time_ms": 0.0, "shifted_roi_date": None,
                "shifted_roi_pharma": None, "shifted_roi_labels": None,
                "auto_verdict": "FAIL", "auto_reasons": [msg], "auto_mode": "ERROR"}
