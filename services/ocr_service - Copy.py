import time
import pytesseract
import numpy as np
from typing import List

class OcrService:
    def __init__(self, tesseract_cmd: str = None, backend: str = "tesseract"):
        """
        Initialize OCR Service with High-Speed Real-Time Architecture:
        - 'tesseract' (Default for Real-Time Production): High-speed C++ engine (~50-100ms), zero lag, zero UI freeze.
        - 'paddle': PaddleOCR deep learning model (~2.5s on CPU).
        - 'hybrid': Tesseract primary with PaddleOCR fallback only if Tesseract produces no text.
        """
        self.backend = backend
        self.tesseract_cmd = tesseract_cmd
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        try:
            pytesseract.pytesseract.DEFAULT_ENCODING = 'latin1'
        except Exception:
            pass

        self._paddle_service = None
        if self.backend in ("paddle", "hybrid"):
            try:
                from services.paddle_ocr_service import PaddleOcrService
                paddle = PaddleOcrService()
                if paddle.is_available:
                    self._paddle_service = paddle
                    print("[OcrService] PaddleOCR backend loaded.")
            except Exception as e:
                print(f"[OcrService] Note: Running Tesseract only ({e})")

    def extract_text_with_confidence(self, image: np.ndarray, whitelist: str = None) -> tuple[List[str], float]:
        """
        Extract text lines and compute average word confidence score (0.0 to 100.0%).
        Runs on high-speed Tesseract (~50ms) to ensure smooth 30+ FPS camera feed and zero UI lag.
        """
        if self.backend == "paddle" and self._paddle_service and self._paddle_service.is_available:
            return self._paddle_service.extract_text_with_confidence(image, whitelist=whitelist)

        # 1. High-speed Tesseract real-time pass (30-60ms, 0% UI lag)
        if whitelist is None:
            whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/:.- "
        config_psm6 = f'--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}'
        
        texts = []
        avg_conf = 0.0
        try:
            d = pytesseract.image_to_data(image, config=config_psm6, output_type=pytesseract.Output.DICT)
            lines_dict = {}
            confs = []
            for i in range(len(d['text'])):
                w = d['text'][i].strip()
                c = float(d['conf'][i])
                line_num = d.get('line_num', [0] * len(d['text']))[i]
                if w:
                    lines_dict.setdefault(line_num, []).append(w)
                    if c >= 0:
                        confs.append(c)

            texts = [' '.join(words) for words in lines_dict.values() if words]
            avg_conf = round(sum(confs) / len(confs), 1) if confs else 0.0
        except Exception:
            pass

        if not texts:
            texts = self.extract_text(image, whitelist=whitelist)
            avg_conf = 80.0 if texts else 0.0

        if texts or self.backend != "hybrid":
            return texts, avg_conf

        # 2. Rescue fallback to PaddleOCR only if explicitly hybrid and Tesseract found nothing
        if self._paddle_service and self._paddle_service.is_available:
            try:
                t_s8_start = time.perf_counter()
                p_texts, p_conf = self._paddle_service.extract_text_with_confidence(image, whitelist=whitelist)
                t_s8_end = time.perf_counter()
                print(f"[TIMING] Stage 08: PaddleOCR Fallback [NG ONLY] | START: {t_s8_start:.6f} | END: {t_s8_end:.6f} | ELAPSED: {(t_s8_end - t_s8_start)*1000:.2f} ms | Texts: {p_texts}")
                if p_texts:
                    return p_texts, p_conf
            except Exception:
                pass

        return texts, avg_conf

    def extract_text(self, image: np.ndarray, whitelist: str = None) -> List[str]:
        """Extract text from preprocessed image with fast Tesseract and PaddleOCR fallback."""
        if self.backend == "paddle" and self._paddle_service and self._paddle_service.is_available:
            return self._paddle_service.extract_text(image, whitelist=whitelist)

        if whitelist is None:
            whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/:.- "
        config_psm6 = f'--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}'
        
        texts = []
        try:
            text_bytes = pytesseract.image_to_string(image, config=config_psm6, output_type=pytesseract.Output.BYTES)
            text = text_bytes.decode('utf-8', errors='replace')
            texts = [line.strip() for line in text.split('\n') if line.strip()]
        except Exception:
            texts = []

        # If PSM 6 produced no text, fallback to PSM 4
        if not texts:
            try:
                t_s9_start = time.perf_counter()
                config_psm4 = f'--oem 3 --psm 4 -c tessedit_char_whitelist={whitelist}'
                text_bytes = pytesseract.image_to_string(image, config=config_psm4, output_type=pytesseract.Output.BYTES)
                text = text_bytes.decode('utf-8', errors='replace')
                texts = [line.strip() for line in text.split('\n') if line.strip()]
                t_s9_end = time.perf_counter()
                print(f"[TIMING] Stage 09: Tesseract Fallback (PSM 4) [NG ONLY] | START: {t_s9_start:.6f} | END: {t_s9_end:.6f} | ELAPSED: {(t_s9_end - t_s9_start)*1000:.2f} ms | Texts: {texts}")
            except Exception:
                pass

        # If Tesseract produced text, return immediately
        if texts:
            return texts

        # Fallback to PaddleOCR only if Tesseract missed
        if self._paddle_service and self._paddle_service.is_available:
            try:
                t_s8_start = time.perf_counter()
                p_texts = self._paddle_service.extract_text(image, whitelist=whitelist)
                t_s8_end = time.perf_counter()
                print(f"[TIMING] Stage 08: PaddleOCR Fallback [NG ONLY] | START: {t_s8_start:.6f} | END: {t_s8_end:.6f} | ELAPSED: {(t_s8_end - t_s8_start)*1000:.2f} ms | Texts: {p_texts}")
                if p_texts:
                    return p_texts
            except Exception:
                pass

        return texts

    def extract_boxes(self, image: np.ndarray, whitelist: str = None) -> str:
        """Extract character bounding boxes from preprocessed image."""
        if self._paddle_service and self._paddle_service.is_available:
            try:
                boxes = self._paddle_service.extract_boxes(image, whitelist=whitelist)
                if boxes:
                    return boxes
            except Exception:
                pass

        if whitelist is None:
            whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/:.- "
        config_psm6 = f'--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}'
        try:
            return pytesseract.image_to_boxes(image, config=config_psm6)
        except Exception:
            return ""

