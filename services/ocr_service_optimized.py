import time
import pytesseract
import numpy as np
from typing import List

class OcrService:
    def __init__(
        self,
        tesseract_cmd: str = None,
        backend: str = "tesseract",
        enable_fallbacks: bool = False,
    ):
        """
        Initialize OCR Service with High-Speed Real-Time Architecture:
        - 'tesseract' (Default for Real-Time Production): High-speed C++ engine (~50-100ms), zero lag, zero UI freeze.
        - 'paddle': PaddleOCR deep learning model (~2.5s on CPU).
        - 'hybrid': Tesseract primary with PaddleOCR fallback only if Tesseract produces no text.
        """
        self.backend = backend
        self.tesseract_cmd = tesseract_cmd
        # Fast production mode: avoid expensive sequential OCR retries.
        # Set enable_fallbacks=True only when recovery OCR is required.
        self.enable_fallbacks = enable_fallbacks
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

    def extract_text_with_confidence(
        self, image: np.ndarray, whitelist: str = None
    ) -> tuple[List[str], float]:
        """
        Extract text lines and calculate average confidence.

        The default path performs ONE Tesseract data pass only. Expensive
        PSM-4/Paddle retries are disabled by default to keep the inspection
        cycle fast. Enable them with enable_fallbacks=True when needed.
        """
        if self.backend == "paddle" and self._paddle_service and self._paddle_service.is_available:
            return self._paddle_service.extract_text_with_confidence(
                image, whitelist=whitelist
            )

        if whitelist is None:
            whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/:.- "

        config_psm6 = f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}"
        texts: List[str] = []
        confs: List[float] = []

        try:
            data = pytesseract.image_to_data(
                image,
                config=config_psm6,
                output_type=pytesseract.Output.DICT,
            )
            lines_dict = {}
            line_nums = data.get("line_num", [0] * len(data.get("text", [])))

            for i, raw_text in enumerate(data.get("text", [])):
                word = raw_text.strip()
                try:
                    confidence = float(data["conf"][i])
                except (ValueError, TypeError, IndexError):
                    confidence = -1.0

                if word:
                    line_num = line_nums[i] if i < len(line_nums) else 0
                    lines_dict.setdefault(line_num, []).append(word)
                    if confidence >= 0:
                        confs.append(confidence)

            texts = [" ".join(words) for words in lines_dict.values() if words]
        except Exception:
            texts = []
            confs = []

        avg_conf = round(sum(confs) / len(confs), 1) if confs else 0.0

        # Optional recovery path. Disabled by default because it adds latency.
        if not texts and self.enable_fallbacks:
            texts = self.extract_text(image, whitelist=whitelist)
            if texts and avg_conf == 0.0:
                avg_conf = 80.0

        if texts or not self.enable_fallbacks or self.backend != "hybrid":
            return texts, avg_conf

        if self._paddle_service and self._paddle_service.is_available:
            try:
                p_texts, p_conf = self._paddle_service.extract_text_with_confidence(
                    image, whitelist=whitelist
                )
                if p_texts:
                    return p_texts, p_conf
            except Exception:
                pass

        return texts, avg_conf

    def extract_text(self, image: np.ndarray, whitelist: str = None) -> List[str]:
        """
        Extract text using one fast Tesseract PSM-6 pass.

        PSM-4 and PaddleOCR recovery are executed only when
        enable_fallbacks=True, preventing unnecessary NG-only delays.
        """
        if self.backend == "paddle" and self._paddle_service and self._paddle_service.is_available:
            return self._paddle_service.extract_text(image, whitelist=whitelist)

        if whitelist is None:
            whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/:.- "

        config_psm6 = f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}"
        texts: List[str] = []

        try:
            text_bytes = pytesseract.image_to_string(
                image,
                config=config_psm6,
                output_type=pytesseract.Output.BYTES,
            )
            text = text_bytes.decode("utf-8", errors="replace")
            texts = [line.strip() for line in text.split("\n") if line.strip()]
        except Exception:
            texts = []

        if texts or not self.enable_fallbacks:
            return texts

        # Optional second Tesseract layout pass.
        if self.enable_fallbacks:
            try:
                config_psm4 = f"--oem 3 --psm 4 -c tessedit_char_whitelist={whitelist}"
                text_bytes = pytesseract.image_to_string(
                    image,
                    config=config_psm4,
                    output_type=pytesseract.Output.BYTES,
                )
                text = text_bytes.decode("utf-8", errors="replace")
                texts = [line.strip() for line in text.split("\n") if line.strip()]
            except Exception:
                texts = []

        if texts or not self.enable_fallbacks:
            return texts

        if self._paddle_service and self._paddle_service.is_available:
            try:
                p_texts = self._paddle_service.extract_text(image, whitelist=whitelist)
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

