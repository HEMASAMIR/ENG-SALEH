import os
import sys
import time
from typing import List, Tuple, Optional
import numpy as np
import cv2

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

# Suppress potential torch DLL load issues on Windows environments where torch is unused
try:
    import torch
except BaseException:
    pass

try:
    from paddlex.inference.models.runners.paddle_static.config.blocklists import MKLDNN_BLOCKLIST
    for m in [
        'PP-OCRv6_medium_det', 'PP-OCRv6_medium_rec',
        'PP-OCRv6_small_rec', 'PP-OCRv6_tiny_rec',
        'PP-OCRv4_mobile_det', 'PP-OCRv4_mobile_rec'
    ]:
        if m not in MKLDNN_BLOCKLIST:
            MKLDNN_BLOCKLIST.append(m)
except Exception:
    pass

import scipy.ndimage as ndi
from scipy.signal import find_peaks


class PaddleOcrService:
    def __init__(self, lang: str = 'en', model_name: str = 'PP-OCRv6_small_rec'):
        """
        Industrial OCR Service powered by PP-OCRv6 Recognition ONLY.
        Detection, orientation classification, and unwarping are strictly DISABLED.
        Configured for high-speed CPU inference on pharmaceutical Dot Matrix packaging.
        """
        self.lang = lang
        self.model_name = model_name
        self.device = "CPU"
        self._predictor = None
        self._init_paddle()

    def _init_paddle(self):
        try:
            import paddle
            if paddle.device.is_compiled_with_cuda() and "gpu" in paddle.device.get_device().lower():
                self.device = "GPU"
            else:
                self.device = "CPU"

            import paddlex
            self._predictor = paddlex.create_predictor(
                model_name=self.model_name,
                batch_size=4
            )

            # Warm-up pass to eliminate oneDNN / computational graph creation latency on frame 1
            dummy = np.zeros((48, 160, 3), dtype=np.uint8)
            list(self._predictor.predict(dummy))

            print("[PaddleOcrService] Backend: PP-OCRv6 Recognition ONLY")
            print(f"[PaddleOcrService] Model: {self.model_name}")
            print(f"[PaddleOcrService] Device: {self.device}")
            print("[PaddleOcrService] Detection: DISABLED")
            print("[PaddleOcrService] Orientation: DISABLED")
        except Exception as e:
            print(f"[PaddleOcrService] Warning: PP-OCRv6 recognition failed to load ({e}).")
            self._predictor = None

    @property
    def is_available(self) -> bool:
        return self._predictor is not None

    def _slice_lines(self, image: np.ndarray) -> List[Tuple[np.ndarray, int, int]]:
        """
        Extract line slices from ROI using horizontal projection profiling.
        If the ROI is already a single line strip (wide aspect ratio or small height),
        it returns the original image directly without any slicing.
        Runs purely in CPU NumPy in < 1ms, completely avoiding neural-network text detection.
        """
        h, w = image.shape[:2]
        # Fast exit: already a single text line strip
        if h <= 60 or (w / max(1, h) >= 2.5):
            return [(image, 0, h)]

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        mean_val = float(np.mean(gray))
        dark_text = mean_val > 120
        bin_img = (gray < (mean_val * 0.85)) if dark_text else (gray > (mean_val * 1.15))
        proj = np.sum(bin_img, axis=1).astype(float)
        max_p = np.max(proj)
        if max_p == 0:
            return [(image, 0, h)]

        smooth = ndi.gaussian_filter1d(proj, sigma=2.0)
        min_dist = max(15, int(h * 0.12))
        peaks, _ = find_peaks(smooth, distance=min_dist, prominence=max_p * 0.15)
        if len(peaks) <= 1:
            return [(image, 0, h)]

        valleys, _ = find_peaks(-smooth, distance=min_dist)
        split_pts = []
        for i in range(len(peaks) - 1):
            p1, p2 = peaks[i], peaks[i + 1]
            v_between = [v for v in valleys if p1 < v < p2]
            if v_between:
                split_pts.append(int(min(v_between, key=lambda v: smooth[v])))
            else:
                split_pts.append(int((p1 + p2) // 2))

        bounds = [0] + split_pts + [h]
        slices = []
        for i in range(len(bounds) - 1):
            y1, y2 = bounds[i], bounds[i + 1]
            if (y2 - y1) >= 10:
                slices.append((image[y1:y2, :], y1, y2))

        return slices if slices else [(image, 0, h)]

    def extract_text_with_confidence(self, image: np.ndarray, whitelist: str = None) -> Tuple[List[str], float]:
        """
        Extract text lines and compute average confidence (0.0 to 100.0%) using PP-OCRv6 Recognition ONLY.
        """
        if not self.is_available or image is None or image.size == 0:
            return [], 0.0

        t_total_start = time.perf_counter()
        h, w = image.shape[:2]

        try:
            # 1. Preprocessing: Color space adjustment & lightweight line segmentation if multi-line ROI
            t_prep_start = time.perf_counter()
            if len(image.shape) == 2:
                img_feed = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            else:
                img_feed = image

            line_slices = self._slice_lines(img_feed)
            crops = [s[0] for s in line_slices]
            t_prep_end = time.perf_counter()
            t_prep_ms = (t_prep_end - t_prep_start) * 1000.0

            # 2. Direct Recognition Inference (PP-OCRv6_small_rec)
            t_infer_start = time.perf_counter()
            preds = list(self._predictor.predict(crops))
            t_infer_end = time.perf_counter()
            t_infer_ms = (t_infer_end - t_infer_start) * 1000.0

            # 3. Clean results and apply whitelist filtering
            texts = []
            confs = []

            for p in preds:
                rec_text = p.get('rec_text', '') if isinstance(p, dict) else getattr(p, 'rec_text', '')
                rec_score = p.get('rec_score', 0.0) if isinstance(p, dict) else getattr(p, 'rec_score', 0.0)
                txt_clean = rec_text.strip()
                if whitelist:
                    txt_clean = ''.join([c for c in txt_clean if c in whitelist])
                if txt_clean:
                    texts.append(txt_clean)
                    confs.append(float(rec_score) * 100.0)

            avg_conf = round(sum(confs) / len(confs), 1) if confs else 0.0
            t_total_end = time.perf_counter()
            t_total_ms = (t_total_end - t_total_start) * 1000.0

            # 4. Mandatory Log
            print(
                f"[PaddleOcrService] ROI: {w}x{h} | "
                f"Prep: {t_prep_ms:.2f}ms | "
                f"Infer: {t_infer_ms:.2f}ms | "
                f"Total: {t_total_ms:.2f}ms | "
                f"Conf: {avg_conf:.1f}% | "
                f"Text: {texts}"
            )

            return texts, avg_conf

        except Exception as e:
            print(f"[PaddleOcrService] PP-OCRv6 inference error: {e}")
            return [], 0.0

    def extract_text(self, image: np.ndarray, whitelist: str = None) -> List[str]:
        texts, _ = self.extract_text_with_confidence(image, whitelist=whitelist)
        return texts

    def extract_boxes(self, image: np.ndarray, whitelist: str = None) -> str:
        """
        Extract character boxes in Tesseract box format for smudge detection compatibility.
        Constructed directly from recognized characters and line slices without running detection.
        """
        if not self.is_available or image is None or image.size == 0:
            return ""

        try:
            h, w = image.shape[:2]
            if len(image.shape) == 2:
                img_feed = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            else:
                img_feed = image

            line_slices = self._slice_lines(img_feed)
            crops = [s[0] for s in line_slices]
            preds = list(self._predictor.predict(crops))

            box_lines = []
            for (crop, y1, y2), p in zip(line_slices, preds):
                txt = p.get('rec_text', '') if isinstance(p, dict) else getattr(p, 'rec_text', '')
                txt = txt.strip()
                if whitelist:
                    txt = ''.join([c for c in txt if c in whitelist])
                if not txt:
                    continue

                tess_y1 = h - y2
                tess_y2 = h - y1
                char_w = max(1, w // len(txt))
                for idx, char in enumerate(txt):
                    cx1 = idx * char_w
                    cx2 = min(w, (idx + 1) * char_w)
                    box_lines.append(f"{char} {cx1} {tess_y1} {cx2} {tess_y2} 0")

            return "\n".join(box_lines)
        except Exception as e:
            print(f"[PaddleOcrService] Extract boxes error: {e}")
            return ""
