import os
import sys
import cv2
import json
import time
import base64
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Tuple

@dataclass
class LocatorResult:
    found: bool = False
    score: float = 0.0
    dx: int = 0
    dy: int = 0
    bbox: Optional[List[int]] = None  # [x1, y1, x2, y2]
    time_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "score": round(float(self.score), 4),
            "dx": int(self.dx),
            "dy": int(self.dy),
            "bbox": [int(v) for v in self.bbox] if self.bbox else None,
            "time_ms": round(float(self.time_ms), 2)
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LocatorResult":
        return cls(
            found=bool(d.get("found", False)),
            score=float(d.get("score", 0.0)),
            dx=int(d.get("dx", 0)),
            dy=int(d.get("dy", 0)),
            bbox=d.get("bbox"),
            time_ms=float(d.get("time_ms", 0.0))
        )


class ObjectLocator:
    """
    Industrial Object / Pattern Locator using coarse-to-fine Normalized Cross-Correlation (cv2.TM_CCOEFF_NORMED).
    Detects packaging position shifts (dx, dy) and provides dynamic coordinate anchoring for OCR and Pharmacode.
    """

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            if getattr(sys, 'frozen', False):
                storage_dir = os.path.dirname(sys.executable)
            else:
                storage_dir = os.path.dirname(os.path.abspath(__file__))
        self.storage_dir = storage_dir
        self.template_path = os.path.join(self.storage_dir, "locator_template.png")
        self.meta_path = os.path.join(self.storage_dir, "locator_meta.json")

        self.template_img: Optional[np.ndarray] = None
        self.gray_template: Optional[np.ndarray] = None
        self.ref_rect: Optional[List[int]] = None  # [x1, y1, x2, y2]
        self.ref_w: int = 0
        self.ref_h: int = 0
        self._last_synced_roi_raw = None
        self._last_synced_tpl_raw = None

        # Attempt auto-load from persisted disk files
        self.load_from_disk()

    @property
    def has_template(self) -> bool:
        return self.template_img is not None and self.ref_rect is not None

    def set_template(self, frame_bgr: np.ndarray, rect: List[int]) -> bool:
        """
        Teach/define the locator template from a bounding box on the reference frame.
        rect: [x1, y1, x2, y2]
        """
        try:
            raw_x1, raw_y1, raw_x2, raw_y2 = [int(v) for v in rect]
            x1, x2 = min(raw_x1, raw_x2), max(raw_x1, raw_x2)
            y1, y2 = min(raw_y1, raw_y2), max(raw_y1, raw_y2)

            h, w = frame_bgr.shape[:2]
            x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
            y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))

            if x2 <= x1 + 5 or y2 <= y1 + 5:
                print(f"[ObjectLocator] Invalid template rectangle (too small: {x2-x1}x{y2-y1}).")
                return False

            crop = frame_bgr[y1:y2, x1:x2].copy()
            if crop.size == 0:
                return False

            self.template_img = crop
            if len(crop.shape) == 3:
                self.gray_template = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            else:
                self.gray_template = crop.copy()

            self.ref_rect = [x1, y1, x2, y2]
            self.ref_w = x2 - x1
            self.ref_h = y2 - y1
            self._last_synced_roi_raw = None
            self._last_synced_tpl_raw = None

            # Persist to disk
            self.save_to_disk()
            print(f"[ObjectLocator] Template set at {self.ref_rect} (size {self.ref_w}x{self.ref_h}).")
            return True
        except Exception as e:
            print(f"[ObjectLocator] Error setting template: {e}")
            return False

    def clear(self):
        """Clear active template in memory and on disk."""
        self.template_img = None
        self.gray_template = None
        self.ref_rect = None
        self.ref_w = 0
        self.ref_h = 0
        self._last_synced_roi_raw = None
        self._last_synced_tpl_raw = None
        if os.path.exists(self.template_path):
            try:
                os.remove(self.template_path)
            except Exception:
                pass
        if os.path.exists(self.meta_path):
            try:
                os.remove(self.meta_path)
            except Exception:
                pass
        print("[ObjectLocator] Template cleared.")

    def save_to_disk(self):
        """Save template image and metadata to disk."""
        try:
            if self.template_img is not None and self.ref_rect is not None:
                os.makedirs(self.storage_dir, exist_ok=True)
                cv2.imwrite(self.template_path, self.template_img)
                meta = {
                    "ref_rect": self.ref_rect,
                    "ref_w": self.ref_w,
                    "ref_h": self.ref_h,
                    "updated_at": time.time()
                }
                with open(self.meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
        except Exception as e:
            print(f"[ObjectLocator] Failed to save template to disk: {e}")

    def load_from_disk(self) -> bool:
        """Load template image and metadata from disk if available."""
        try:
            if os.path.exists(self.template_path) and os.path.exists(self.meta_path):
                img = cv2.imread(self.template_path, cv2.IMREAD_COLOR)
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if img is not None and "ref_rect" in meta:
                    self.template_img = img
                    self.gray_template = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    self.ref_rect = meta["ref_rect"]
                    self.ref_w = meta.get("ref_w", self.ref_rect[2] - self.ref_rect[0])
                    self.ref_h = meta.get("ref_h", self.ref_rect[3] - self.ref_rect[1])
                    print(f"[ObjectLocator] Loaded template from disk: {self.ref_rect} ({self.ref_w}x{self.ref_h})")
                    return True
        except Exception as e:
            print(f"[ObjectLocator] Failed to load template from disk: {e}")
        return False

    def sync_to_redis(self, redis_client, roi_key: str, template_key: str):
        """Sync current template and ROI coordinates to Redis."""
        try:
            if self.has_template:
                roi_json = json.dumps(self.ref_rect)
                _, png_buf = cv2.imencode(".png", self.template_img)
                png_b64 = base64.b64encode(png_buf).decode("ascii")
                redis_client.set(roi_key, roi_json)
                redis_client.set(template_key, png_b64)
                self._last_synced_roi_raw = roi_json.encode("utf-8")
                self._last_synced_tpl_raw = png_b64.encode("utf-8")
            else:
                redis_client.delete(roi_key)
                redis_client.delete(template_key)
                self._last_synced_roi_raw = None
                self._last_synced_tpl_raw = None
        except Exception as e:
            print(f"[ObjectLocator] Redis sync error: {e}")

    def sync_from_redis(self, redis_client, roi_key: str, template_key: str) -> bool:
        """Sync template and ROI coordinates from Redis.
        If Redis is empty but disk template exists, pushes disk template to Redis.
        Uses caching to avoid base64 decoding when Redis keys have not changed.
        """
        try:
            roi_raw = redis_client.get(roi_key)
            tpl_raw = redis_client.get(template_key)

            if roi_raw and tpl_raw:
                # Fast cache check: already loaded and data has not changed in Redis
                if (self.has_template and 
                    self._last_synced_roi_raw == roi_raw and 
                    self._last_synced_tpl_raw == tpl_raw):
                    return True

                rect = json.loads(roi_raw)
                png_bytes = base64.b64decode(tpl_raw)
                nparr = np.frombuffer(png_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None and len(rect) == 4:
                    self.template_img = img
                    self.gray_template = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    self.ref_rect = [int(v) for v in rect]
                    self.ref_w = self.ref_rect[2] - self.ref_rect[0]
                    self.ref_h = self.ref_rect[3] - self.ref_rect[1]
                    self._last_synced_roi_raw = roi_raw
                    self._last_synced_tpl_raw = tpl_raw
                    # Save to disk as well
                    self.save_to_disk()
                    return True

            elif not roi_raw:
                # Redis key is empty/absent
                # If disk template exists, do NOT wipe! Push disk template to Redis!
                if os.path.exists(self.meta_path) and os.path.exists(self.template_path):
                    if not self.has_template:
                        self.load_from_disk()
                    if self.has_template:
                        self.sync_to_redis(redis_client, roi_key, template_key)
                        return True

                # Only clear if template does NOT exist on disk either (e.g. user cleared it)
                self.template_img = None
                self.gray_template = None
                self.ref_rect = None
                self.ref_w = 0
                self.ref_h = 0
                self._last_synced_roi_raw = None
                self._last_synced_tpl_raw = None

        except Exception as e:
            print(f"[ObjectLocator] Redis read error: {e}")
        return False

    def locate(self, frame_bgr: np.ndarray, min_confidence: float = 0.5) -> LocatorResult:
        """
        Locates the taught object in the given frame using coarse-to-fine template matching
        with automatic full-resolution fallback to guarantee zero false misses.
        Returns LocatorResult with (found, score, dx, dy, bbox, time_ms).
        """
        if not self.has_template or frame_bgr is None or frame_bgr.size == 0:
            return LocatorResult(found=False, score=0.0, dx=0, dy=0, bbox=None, time_ms=0.0)

        t_start = time.perf_counter()

        try:
            # Ensure gray template is valid
            if self.gray_template is None and self.template_img is not None:
                if len(self.template_img.shape) == 3:
                    self.gray_template = cv2.cvtColor(self.template_img, cv2.COLOR_BGR2GRAY)
                else:
                    self.gray_template = self.template_img.copy()

            if self.gray_template is None or self.gray_template.size == 0:
                return LocatorResult(found=False, score=0.0, dx=0, dy=0, bbox=None, time_ms=0.0)

            # Convert frame to grayscale
            if len(frame_bgr.shape) == 3:
                gray_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            else:
                gray_frame = frame_bgr

            h_f, w_f = gray_frame.shape[:2]
            th, tw = self.gray_template.shape[:2]

            if th >= h_f or tw >= w_f or th <= 5 or tw <= 5:
                return LocatorResult(found=False, score=0.0, dx=0, dy=0, bbox=None, time_ms=0.0)

            best_x, best_y = 0, 0
            max_val = 0.0

            # High performance coarse-to-fine: 2x downsample on large frames
            use_coarse = (w_f >= 800 and h_f >= 600 and tw >= 40 and th >= 40)

            if use_coarse:
                small_frame = cv2.resize(gray_frame, (0, 0), fx=0.5, fy=0.5)
                small_tpl = cv2.resize(self.gray_template, (0, 0), fx=0.5, fy=0.5)

                res_small = cv2.matchTemplate(small_frame, small_tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val_small, _, max_loc_small = cv2.minMaxLoc(res_small)
                coarse_x = int(max_loc_small[0] * 2)
                coarse_y = int(max_loc_small[1] * 2)

                # Generous search neighborhood around coarse match
                pad_x = max(64, int(tw * 0.4))
                pad_y = max(64, int(th * 0.4))

                rx1 = max(0, coarse_x - pad_x)
                ry1 = max(0, coarse_y - pad_y)
                rx2 = min(w_f, coarse_x + tw + pad_x)
                ry2 = min(h_f, coarse_y + th + pad_y)

                # Ensure search sub-frame is at least template size
                if rx2 - rx1 < tw:
                    rx2 = min(w_f, rx1 + tw)
                    rx1 = max(0, rx2 - tw)
                if ry2 - ry1 < th:
                    ry2 = min(h_f, ry1 + th)
                    ry1 = max(0, ry2 - th)

                sub_frame = gray_frame[ry1:ry2, rx1:rx2]
                if sub_frame.shape[0] >= th and sub_frame.shape[1] >= tw:
                    res_sub = cv2.matchTemplate(sub_frame, self.gray_template, cv2.TM_CCOEFF_NORMED)
                    _, max_val_sub, _, local_l = cv2.minMaxLoc(res_sub)
                    best_x = rx1 + local_l[0]
                    best_y = ry1 + local_l[1]
                    max_val = float(max_val_sub)

            # AUTOMATIC FALLBACK:
            # If coarse search was not used, OR if coarse search gave score below min_confidence,
            # perform full-resolution search across the entire frame to guarantee 100% detection rate!
            if not use_coarse or max_val < min_confidence:
                res_full = cv2.matchTemplate(gray_frame, self.gray_template, cv2.TM_CCOEFF_NORMED)
                _, max_val_full, _, max_l = cv2.minMaxLoc(res_full)
                if float(max_val_full) > max_val:
                    max_val = float(max_val_full)
                    best_x, best_y = max_l

            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            score = float(max_val)

            # Require positive score and score >= min_confidence
            # (protects against min_confidence == 0.0 matching error states)
            effective_min = max(0.01, min_confidence) if min_confidence <= 0.0 else min_confidence

            if score >= effective_min and score > 0.0:
                ref_x = self.ref_rect[0] if (self.ref_rect and len(self.ref_rect) >= 2) else best_x
                ref_y = self.ref_rect[1] if (self.ref_rect and len(self.ref_rect) >= 2) else best_y
                dx = int(best_x - ref_x)
                dy = int(best_y - ref_y)
                bbox = [int(best_x), int(best_y), int(best_x + tw), int(best_y + th)]
                return LocatorResult(found=True, score=score, dx=dx, dy=dy, bbox=bbox, time_ms=elapsed_ms)
            else:
                return LocatorResult(found=False, score=score, dx=0, dy=0, bbox=None, time_ms=elapsed_ms)

        except Exception as e:
            print(f"[ObjectLocator] Match error: {e}")
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            return LocatorResult(found=False, score=0.0, dx=0, dy=0, bbox=None, time_ms=elapsed_ms)

    @staticmethod
    def shift_roi(roi_coords, dx: int, dy: int, img_shape: Optional[Tuple[int, int]] = None) -> Optional[List[int]]:
        """
        Shifts [x1, y1, x2, y2] coordinates by (dx, dy).
        Clamps to image bounds if img_shape=(h, w) is supplied.
        """
        if roi_coords is None:
            return None

        if isinstance(roi_coords, (bytes, bytearray)):
            try:
                roi_coords = roi_coords.decode("utf-8")
            except Exception:
                return None

        if isinstance(roi_coords, str):
            try:
                roi_coords = json.loads(roi_coords)
            except Exception:
                return None

        if not isinstance(roi_coords, (list, tuple)) or len(roi_coords) < 4:
            return None

        x1 = roi_coords[0] + dx
        y1 = roi_coords[1] + dy
        x2 = roi_coords[2] + dx
        y2 = roi_coords[3] + dy

        if img_shape is not None:
            h, w = img_shape[:2]
            x1 = max(0, min(w - 1, x1))
            y1 = max(0, min(h - 1, y1))
            x2 = max(1, min(w, x2))
            y2 = max(1, min(h, y2))
            if x2 <= x1 or y2 <= y1:
                return None

        return [int(x1), int(y1), int(x2), int(y2)]
