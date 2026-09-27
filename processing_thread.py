import time
import json
import redis
import numpy as np
import cv2
import re
from threading import Thread
from collections import deque, Counter
from core.config import settings
from services.ocr_service import OcrService
from core.image_processing import preprocess_img
from core.pharmacode import read_pharma_code
from core.database import get_session
from services.recipe_service import RecipeService
from models.recipe.recipe import Recipe, RecipeStatus
from concurrent.futures import ThreadPoolExecutor
from core.object_locator import ObjectLocator, LocatorResult

from core.timing_util import log_stage_timing, log_subcall_timing

class ProcessingThread(Thread):
    CHAR_TO_DIGIT_LOT = {
        'O': '0', 'o': '0', 'D': '0', 'Q': '0',
        'I': '1', 'l': '1', '|': '1', 'i': '1',
        'Z': '2', 'z': '2',
        'S': '5', 's': '5',
        'B': '8',
        'G': '6',
    }
    CHAR_TO_DIGIT_DATE = {
        'O': '0', 'o': '0', 'D': '0', 'Q': '0',
        'f': '0', 'F': '0', 'c': '0', 'C': '0',
        'I': '1', 'l': '1', '|': '1', 'i': '1',
        'S': '5', 's': '5',
        'B': '8',
    }

    def __init__(self, ocr_service: OcrService, plc_comm=None):
        super().__init__()
        self.ocr_service = ocr_service
        self.plc_comm = plc_comm
        self.redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB
        )
        self.running = False
        self.last_processed_timestamp = None
        self.last_settings_hash = None
        
        # Object Locator Engine
        self.locator = ObjectLocator()
        self.locator_min_confidence = getattr(settings, "LOCATOR_MIN_CONFIDENCE", 0.5)
        self.ocr_min_confidence = getattr(settings, "OCR_MIN_CONFIDENCE", 0.5)
        self.labels_min_confidence = getattr(settings, "LABELS_MIN_CONFIDENCE", 0.0)

        # Performance Overhaul: Count Buffering
        self.count_buffer = {"good": 0, "wrong": 0, "total": 0}
        self.current_active_recipe_id = None
        self.last_db_sync_time = time.time()
        self.last_settings_check_time = 0
        self.cached_settings = {}
        # Parallelization
        self.executor = ThreadPoolExecutor(max_workers=3)

    def run(self):
        self.running = True
        print("[ProcessingThread] Started.")
        
        while self.running:
            try:
                raw_data = self.redis_client.lrange(settings.RAW_FRAMES_KEY, 0, 0)
                
                # Caching: Only check settings every 500ms
                settings_hash = self.last_settings_hash
                if time.time() - self.last_settings_check_time > 0.5:
                    settings_hash = self._get_settings_hash()
                    self.last_settings_check_time = time.time()

                processed_new = False
                if raw_data:
                    payload = json.loads(raw_data[0])
                    timestamp = payload.get("timestamp")
                    
                    if timestamp != self.last_processed_timestamp or settings_hash != self.last_settings_hash:
                        self.last_processed_timestamp = timestamp
                        self.last_settings_hash = settings_hash
                        
                        # Decode frame
                        t_s1_start = time.perf_counter()
                        frame_bytes = bytes.fromhex(payload.get("frame_data"))
                        nparr = np.frombuffer(frame_bytes, np.uint8)
                        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        t_s1_end = time.perf_counter()
                        log_stage_timing(1, "Image capture & decode", t_s1_start, t_s1_end, extra_info=f"Resolution: {img.shape[1]}x{img.shape[0]}" if img is not None else "Failed")
                        
                        if img is not None:
                            t_cycle_start = t_s1_start
                            pipeline_started = self.redis_client.get(settings.START_PIPELINE_KEY) == b"true"
                            
                            results = self.process_frame(img)
                            
                            # Standard Inspection Logic
                            if pipeline_started:
                                self._handle_inspection_validation(results)
 
                            # Push results to Redis
                            self.redis_client.lpush(settings.RESULTS_KEY, json.dumps(results))
                            self.redis_client.ltrim(settings.RESULTS_KEY, 0, settings.MAX_FRAMES - 1)

                            t_cycle_end = time.perf_counter()
                            is_match = not results.get("has_mismatch", False)
                            log_stage_timing(14, "Total Cycle Time", t_cycle_start, t_cycle_end, extra_info=f"Decision: {'GOOD (MATCH)' if is_match else 'BAD (MISMATCH / NG)'}")
                        processed_new = True
                
                # Performance Overhaul: Periodic DB Sync (Every 2 seconds)
                if time.time() - self.last_db_sync_time > 2.0:
                    self._sync_counts_to_db()
                    self.last_db_sync_time = time.time()
 
                if processed_new:
                    time.sleep(0.01)
                else:
                    time.sleep(0.05)  # 50ms idle sleep to prevent CPU hogging
            except Exception as e:
                print(f"[ProcessingThread] Error: {e}")
                time.sleep(1)

    def _handle_inspection_validation(self, results):
        """
        Compares actual results with expected values and updates internal memory buffer.
        System continues running on mismatch (does NOT stop pipeline).
        Mismatch signal (M20) is sent after the NEXT frame is analyzed.
        """
        try:
            t_s12_start = time.perf_counter()
            expected_raw = self.redis_client.get("expected_values")
            if not expected_raw:
                return
            
            expected = json.loads(expected_raw)
            
            # 1. Comparison with intelligent prefix normalization
            is_match = True
            if results.get("locator_found") is False:
                is_match = False
            norm_actual_lot = self._normalize_code(results.get("LOT"), "LOT")
            norm_exp_lot = self._normalize_code(expected.get("LOT"), "LOT")
            if norm_actual_lot != norm_exp_lot:
                # Tolerate 1-character crop edge artifact on LOT if exact prefix matches expected
                if norm_exp_lot and norm_actual_lot.startswith(norm_exp_lot) and len(norm_actual_lot) == len(norm_exp_lot) + 1:
                    pass
                else:
                    is_match = False
            if self._normalize_code(results.get("MFG"), "MFG") != self._normalize_code(expected.get("MFG"), "MFG"):
                is_match = False
            if self._normalize_code(results.get("EXP"), "EXP") != self._normalize_code(expected.get("EXP"), "EXP"):
                is_match = False
            if str(results.get("PHARMA_CODE", "")).strip() != str(expected.get("PHARMA", "")).strip():
                is_match = False
            # Check OCR confidence threshold (if text was read)
            if results.get("LOT") != "N/A" and results.get("ocr_conf", 0.0) < (self.ocr_min_confidence * 100.0):
                is_match = False
                print(f"[ProcessingThread] OCR Low Confidence: {results.get('ocr_conf')}% < {self.ocr_min_confidence*100}%")

            # Check Labels ROI validation (must be PASS: all uppercase letters, strictly no digits)
            if results.get("shifted_roi_labels") is not None:
                if results.get("LABELS_STATUS") != "PASS":
                    is_match = False
                    print(f"[ProcessingThread] LABELS MISMATCH: status={results.get('LABELS_STATUS')}, error={results.get('LABELS_ERROR')}")
                elif self.labels_min_confidence > 0.0 and results.get("labels_conf", 0.0) < (self.labels_min_confidence * 100.0):
                    is_match = False
                    results["LABELS_STATUS"] = "FAIL"
                    results["LABELS_ERROR"] = f"Low Conf: {results.get('labels_conf', 0):.1f}% < {self.labels_min_confidence*100:.0f}%"
                    print(f"[ProcessingThread] LABELS Low Confidence: {results.get('labels_conf')}% < {self.labels_min_confidence*100}%")

            # Check Smudged Characters (أي حرف أو كاراكتر مطموس = خطأ)
            if results.get("smudged_chars"):
                is_match = False
                print(f"[ProcessingThread] SMUDGE MISMATCH: Smudged characters detected: {results.get('smudged_chars')}")

            t_s12_end = time.perf_counter()
            log_stage_timing(12, "Final decision", t_s12_start, t_s12_end, extra_info=f"Verdict: {'PASS (GOOD)' if is_match else 'FAIL (NG / MISMATCH)'}")

            t_s13_start = time.perf_counter()
            # 2. Update Buffer (NOT the DB yet)
            self.count_buffer["total"] += 1
            if is_match:
                self.count_buffer["good"] += 1
            else:
                self.count_buffer["wrong"] += 1

            # 3. Result Signaling (Instant UI Feedback)
            #    System continues running — no pipeline stop on mismatch.
            #    M20 signal sent immediately to PLC on mismatch.
            if not is_match:
                print(f"[ProcessingThread] MISMATCH: {results} vs {expected}")
                self.redis_client.set(settings.ERROR_LED_TRIGGER_KEY, "true")
                self.redis_client.set(settings.INSPECTION_STATUS_KEY, "fail")
                self.redis_client.set("plc_mismatch_signal", "true")
                results["has_mismatch"] = True
                
                # Direct PLC signaling (works even when logged out and UI is closed)
                if self.plc_comm and self.plc_comm.is_connected:
                    self.plc_comm.signal_mismatch()
                    print("[ProcessingThread] Sent M20 mismatch signal directly to PLC")
            else:
                self.redis_client.set(settings.INSPECTION_STATUS_KEY, "pass")
                self.redis_client.set("plc_match_signal", "true")
                results["has_mismatch"] = False

                # Direct PLC signaling for match (valid image & correct decision -> M21)
                if self.plc_comm and self.plc_comm.is_connected:
                    self.plc_comm.signal_match()
                    print("[ProcessingThread] Sent M21 match signal directly to PLC")

            t_s13_end = time.perf_counter()
            log_stage_timing(13, "Reject/output", t_s13_start, t_s13_end, extra_info=f"Signal: {'M21 (Match)' if is_match else 'M20 (Reject / NG)'}")
        except Exception as e:
            print(f"[ProcessingThread] Validation error: {e}")

    def _sync_counts_to_db(self):
        """
        Flushes the buffered counts to the database in a single transaction.
        """
        # Only sync if there's actually something to sync
        if self.count_buffer["total"] == 0:
            return

        try:
            with get_session() as session:
                active_recipe = session.query(Recipe).filter(
                    Recipe.status == RecipeStatus.ACTIVE,
                    Recipe.is_deleted == False
                ).first()
                
                if active_recipe:
                    # Update parameters. We do this in a single loop if possible.
                    # Mapping local buffer names to DB parameter names.
                    from repositories.recipe_parameter_repository import RecipeParameterRepository
                    param_repo = RecipeParameterRepository(session)
                    
                    mapping = {
                        "total": "total_count",
                        "good": "good_count",
                        "wrong": "wrong_count"
                    }
                    
                    for buf_key, db_param_name in mapping.items():
                        delta = self.count_buffer[buf_key]
                        if delta > 0:
                            param = param_repo.get_by_name(active_recipe.recipe_id, db_param_name)
                            if param:
                                current_val = int(param.current_value.get('value', 0))
                                param.current_value = {"value": current_val + delta}

                    # Reset local buffer AFTER successful commit (via get_session context)
                    self.count_buffer = {"good": 0, "wrong": 0, "total": 0}
                    print(f"[ProcessingThread] Sync'd counts to DB for recipe {active_recipe.recipe_id}")

        except Exception as e:
            print(f"[ProcessingThread] Sync error: {e}")

    def _get_settings_hash(self):
        """Returns a stable string/hash of all current ROI settings."""
        keys = [
            settings.ROI_LOCATOR_KEY, settings.ROI_LOCATOR_TEMPLATE_KEY,
            settings.ROI_DATE_KEY, settings.ROI_PHARMA_KEY, settings.ROI_LABELS_KEY,
            settings.ROI_DATE_CONTRAST, settings.ROI_PHARMA_CONTRAST, settings.ROI_LABELS_CONTRAST,
            settings.ROI_DATE_ROTATION, settings.ROI_PHARMA_ROTATION, settings.ROI_LABELS_ROTATION,
            settings.ROI_DATE_GRAYSCALE, settings.ROI_DATE_THRESHOLD,
            settings.ROI_DATE_BRIGHTNESS, settings.ROI_DATE_GAMMA, settings.ROI_DATE_LOCAL_CONTRAST,
            settings.ROI_DATE_SMOOTHING, settings.ROI_DATE_CONNECT_DOTS,
            settings.ROI_LABELS_GRAYSCALE, settings.ROI_LABELS_THRESHOLD,
            settings.ROI_LABELS_BRIGHTNESS, settings.ROI_LABELS_GAMMA, settings.ROI_LABELS_LOCAL_CONTRAST,
            settings.ROI_LABELS_SMOOTHING, settings.ROI_LABELS_CONNECT_DOTS,
            settings.LABELS_MIN_CONFIDENCE_KEY
        ]
        try:
            # mget returns a list of byte strings
            values = self.redis_client.mget(keys)
            return str(values)
        except Exception:
            return ""

    def _apply_adjustments(self, crop, contrast, rotation, roi_name="ROI"):
        """Apply contrast and rotation to a specific crop."""
        if crop is None or crop.size == 0:
            return crop
            
        t_adj_start = time.perf_counter()
        # Contrast adjustment: crop = crop * contrast + offset
        # contrast 1.0 = normal, < 1.0 = lower, > 1.0 = higher
        if contrast != 1.0:
            crop = cv2.convertScaleAbs(crop, alpha=contrast, beta=0)

        # Rotation: 0, 90, 180, 270 (counter-clockwise degrees to CV constants)
        if rotation == 90:
            crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            crop = cv2.rotate(crop, cv2.ROTATE_180)
        elif rotation == 270:
            crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
        # 360/0 = no change
        t_adj_end = time.perf_counter()
        if contrast != 1.0 or rotation in (90, 180, 270):
            log_subcall_timing(f"Rotation & Adjustment ({roi_name})", f"rot={rotation}deg, con={contrast}", t_adj_start, t_adj_end, f"Shape: {crop.shape}")
        
        return crop

    def _fuzzy_match(self, text, target):
        """Returns True if text matches target with at most 1 character mismatch."""
        if len(text) != len(target):
            # If lengths differ slightly, we can still check if one is a substring,
            # but per requirements: "one-character mismatch".
            # We'll allow +/- 1 length diff for extreme cases, but focus on content.
            if abs(len(text) - len(target)) > 1:
                return False
        
        # Simple character-by-character comparison (assuming same alignment)
        # This works for the labels "LOT", "MFG", "EXP"
        mismatches = 0
        min_len = min(len(text), len(target))
        for i in range(min_len):
            if text[i] != target[i]:
                mismatches += 1
        
        # Account for length difference as mismatches
        mismatches += abs(len(text) - len(target))
        
        return mismatches <= 1

    @staticmethod
    def _classify_field_read(act_val: str, exp_val: str, conf: float, field_type: str = "LOT") -> str:
        """
        Classifies OCR field read into 6 states:
        1. CLEAR_VALID_MATCH: act_val matches exp_val.
        2. BLANK_READ: act_val is missing, empty, or 'N/A'.
        3. LOW_CONFIDENCE_READ: conf < 50.0% (and field was not matched).
        4. INCOMPLETE_READ: act_val is shorter than expected (missing digit/part).
        5. SUSPICIOUS_READ: format does not conform to expected structure.
        6. CLEAR_CONFIDENT_MISMATCH: full length, valid format, conf >= 50.0%, but different value.
        """
        if not exp_val:
            return "CLEAR_VALID_MATCH" if act_val not in ("N/A", "", "EMPTY", None) else "BLANK_READ"

        if not act_val or act_val in ("N/A", "EMPTY", "LOCATOR MISSED"):
            return "BLANK_READ"

        if act_val == exp_val:
            return "CLEAR_VALID_MATCH"

        if conf < 50.0 and conf > 0.0:
            return "LOW_CONFIDENCE_READ"

        if field_type == "LOT":
            if len(act_val) < len(exp_val):
                return "INCOMPLETE_READ"
            return "CLEAR_CONFIDENT_MISMATCH"

        elif field_type in ("MFG", "EXP"):
            act_digits = re.sub(r'[^0-9]', '', act_val)
            exp_digits = re.sub(r'[^0-9]', '', exp_val)
            if len(act_digits) < len(exp_digits):
                return "INCOMPLETE_READ"
            if not re.search(r'\d{1,2}[\/\-\.]\d{2,4}', act_val):
                return "SUSPICIOUS_READ"
            return "CLEAR_CONFIDENT_MISMATCH"

        return "CLEAR_CONFIDENT_MISMATCH"

    @staticmethod
    def _fix_date_zero_six(date_val: str, expected_date: str = "") -> str:
        """
        Resolves OCR ambiguity between '0' and '6' in date fields (e.g. '66-2026' -> '06-2026').
        In pharmaceutical MM-YYYY format, month is strictly 01-12. A month starting with '6' (61..69)
        is mathematically and pharmacologically impossible and is an OCR misread of '0'.
        Also fixes year digit errors (e.g. '2926' -> '2026').
        """
        if not date_val or date_val in ("N/A", "EMPTY", "LOCATOR MISSED"):
            return date_val
        
        # Correct 292x year misread (9 instead of 0 in year e.g. 06-2926 -> 06-2026)
        date_val = re.sub(r'(\d{2})-(29)(\d{2})', r'\1-20\3', date_val)
        date_val = re.sub(r'(\d{2})\/(29)(\d{2})', r'\1/20\3', date_val)

        m = re.match(r'^([0-9]{2})[\-\/\.]([0-9]{2,4})$', date_val)
        if m:
            month_str, year_str = m.group(1), m.group(2)
            if month_str[0] == '6':
                if int(month_str) > 12 or (expected_date and expected_date.startswith('0' + month_str[1])):
                    return f"0{month_str[1]}-{year_str}"
            if expected_date and expected_date.startswith("0") and date_val.startswith("6") and len(date_val) == len(expected_date):
                if date_val[1:] == expected_date[1:]:
                    return expected_date
        return date_val

    @staticmethod
    def _normalize_label_word(w: str) -> str:
        """Normalizes common dot-matrix / inkjet OCR letter distortions on pharmaceutical labels."""
        if not w:
            return ""
        u = w.upper().strip(" :.-")
        if u in ("NFG", "NEG", "NFS", "HFG", "HFS", "NMFS", "MFD", "MPG", "MAG", "MRG"):
            return "MFG"
        if u in ("L0T", "1OT", "I_OT", "LOT"):
            return "LOT"
        if u in ("EXF", "EAP", "ERP", "EP", "EVP", "E.D.", "ED", "EXP", "EYP"):
            return "EXP"
        
        # Fuzzy match (at most 1 char difference) to standard pharmaceutical label words
        for target in ("LOT", "MFG", "EXP"):
            if len(u) == len(target):
                diffs = sum(1 for a, b in zip(u, target) if a != b)
                if diffs <= 1:
                    return target
            elif abs(len(u) - len(target)) == 1:
                if all(c in target for c in u) or all(c in u for c in target):
                    return target
        return u

    @staticmethod
    def _clean_lot_number(raw_val: str) -> str:
        """Keep strictly digits for LOT number, mapping common OCR letter confusions."""
        if not raw_val:
            return ""
        s = str(raw_val).strip()
        for k, v in ProcessingThread.CHAR_TO_DIGIT_LOT.items():
            s = s.replace(k, v)
        return re.sub(r'[^0-9]', '', s)

    @staticmethod
    def _clean_date_val(raw_val: str, expected_date: str = "") -> str:
        """Keep strictly digits and standard hyphens for dates, stripping letters and edge noise artifacts."""
        if not raw_val:
            return ""
        s = str(raw_val).strip()
        for k, v in ProcessingThread.CHAR_TO_DIGIT_DATE.items():
            s = s.replace(k, v)
        # Match standard date patterns (e.g. 01-2029, 15/07/2026, 01/29)
        m = re.search(r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{1,2}[\/\-\.]\d{4}|\d{1,2}[\/\-\.]\d{2})', s)
        if m:
            clean = m.group(1).replace('.', '-').replace('/', '-')
            res = re.sub(r'-+', '-', clean).strip('-')
        else:
            s = s.strip(' :.-').replace('.', '-').replace('/', '-')
            s = re.sub(r'[^0-9\-]', '', s)
            res = re.sub(r'-+', '-', s).strip('-')

        # Pad single digit month (e.g. 6-2026 -> 06-2026)
        m1 = re.match(r'^([0-9]{1})-([0-9]{2,4})$', res)
        if m1:
            res = f"0{m1.group(1)}-{m1.group(2)}"

        return ProcessingThread._fix_date_zero_six(res, expected_date)

    @staticmethod
    def _normalize_code(val, prefix="", expected_val=""):
        """Normalizes LOT, MFG, EXP codes for flexible matching (with or without prefixes)."""
        if not val:
            return ""
        s = str(val).strip().upper()
        # Remove matching prefix if present
        if prefix:
            p = prefix.upper()
            if s.startswith(p + ":"):
                s = s[len(p) + 1:].strip()
            elif s.startswith(p) and len(s) > len(p) and not s[len(p)].isalnum():
                s = s[len(p):].lstrip(" :.-")
        for generic in ["LOT:", "MFG:", "EXP:", "LOT", "MFG", "EXP", "B.NO:", "BN:", "BATCH:"]:
            if s.startswith(generic):
                s = s[len(generic):].strip(" :.-")
        
        if prefix.upper() == "LOT":
            return ProcessingThread._clean_lot_number(s)
        elif prefix.upper() in ("MFG", "EXP"):
            return ProcessingThread._fix_date_zero_six(ProcessingThread._clean_date_val(s), expected_val)
        
        # Generic fallback
        s = s.replace(".", "-").replace("/", "-")
        return s.strip()

    @staticmethod
    def detect_smudged_characters(img_bw: np.ndarray, boxes_str: str):
        """
        Detects smudged / obliterated (حروف مطموسة) characters.
        
        Criteria:
        1. Loop characters ('O', '0', '8', '6', '9', 'D', 'P', 'B', 'Q', 'A', 'R', etc.):
           Internal loop/hole must exist. If hole count == 0 (or hole_ratio < 0.03)
           and ink fill ratio > 0.65 -> smudged / obliterated loop.
        2. Any letter or digit (excluding punctuation like '-', '.', ':', '/'):
           If ink fill ratio > 0.78 -> flooded with ink (بقعة حبر).
        
        Returns:
            parsed_boxes: list of (ch, x1, y1, x2, y2, y_center)
            smudged_list: list of dicts with char, fill_pct, reason, bbox, y_center
        """
        if img_bw is None or img_bw.size == 0 or not boxes_str:
            return [], []
            
        h_img, w_img = img_bw.shape[:2]
        gray = cv2.cvtColor(img_bw, cv2.COLOR_BGR2GRAY) if len(img_bw.shape) == 3 else img_bw
        
        LOOP_CHARS = {'O', '0', '8', '6', '9', 'D', 'P', 'B', 'Q', 'A', 'R', 'o', 'b', 'd', 'p', 'q'}
        PUNCTUATION = {'-', '.', ':', '/', '|', '_', ',', ';', "'", '"'}
        
        parsed_boxes = []
        smudged = []
        
        for line in boxes_str.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            ch = parts[0]
            x1 = max(0, int(parts[1]))
            y1 = max(0, h_img - int(parts[4]))
            x2 = min(w_img, int(parts[3]))
            y2 = min(h_img, h_img - int(parts[2]))
            
            w = x2 - x1
            h = y2 - y1
            y_center = (y1 + y2) / 2.0
            parsed_boxes.append((ch, x1, y1, x2, y2, y_center))
            
            if w < 6 or h < 10:
                continue
                
            c_crop = gray[y1:y2, x1:x2]
            inv = (c_crop < 128).astype(np.uint8) * 255
            total_area = w * h
            fill_ratio = float((inv > 0).sum()) / total_area
            
            cnts, hier = cv2.findContours(inv, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            hole_cnts = [cnts[i] for i, h_info in enumerate(hier[0]) if h_info[3] >= 0] if hier is not None else []
            hole_area = sum(cv2.contourArea(c) for c in hole_cnts)
            hole_ratio = hole_area / total_area
            
            if ch in LOOP_CHARS and (len(hole_cnts) == 0 or hole_ratio < 0.03) and fill_ratio > 0.65:
                smudged.append({
                    'char': ch,
                    'fill_pct': round(fill_ratio * 100, 1),
                    'reason': f'No hole (filled loop, {round(fill_ratio * 100)}% ink)',
                    'bbox': (x1, y1, x2, y2),
                    'y_center': y_center
                })
            elif ch not in PUNCTUATION and fill_ratio > 0.78:
                smudged.append({
                    'char': ch,
                    'fill_pct': round(fill_ratio * 100, 1),
                    'reason': f'Ink flooded ({round(fill_ratio * 100)}% ink)',
                    'bbox': (x1, y1, x2, y2),
                    'y_center': y_center
                })
                
        return parsed_boxes, smudged

    @staticmethod
    def _assign_fields_to_smudged(smudged_chars, parsed_boxes, results):
        if not smudged_chars or not parsed_boxes:
            return smudged_chars
            
        parsed_boxes_sorted = sorted(parsed_boxes, key=lambda p: p[5])
        lines = []
        for p in parsed_boxes_sorted:
            if not lines or abs(p[5] - np.mean([x[5] for x in lines[-1]])) > 20:
                lines.append([p])
            else:
                lines[-1].append(p)
                
        line_info = []
        for idx, l in enumerate(lines):
            l.sort(key=lambda x: x[1])
            text = ''.join(x[0] for x in l)
            min_y = min(x[2] for x in l)
            max_y = max(x[4] for x in l)
            
            field = None
            clean_text = re.sub(r'[^0-9A-Za-z]', '', text)
            clean_lot = re.sub(r'[^0-9A-Za-z]', '', results.get("LOT", ""))
            clean_mfg = re.sub(r'[^0-9A-Za-z]', '', results.get("MFG", ""))
            clean_exp = re.sub(r'[^0-9A-Za-z]', '', results.get("EXP", ""))
            
            if clean_lot and clean_lot in clean_text:
                field = "LOT"
            elif clean_mfg and clean_mfg in clean_text:
                field = "MFG"
            elif clean_exp and clean_exp in clean_text:
                field = "EXP"
            elif idx == 0 and results.get("LOT") != "N/A":
                field = "LOT"
            elif idx == 1 and results.get("MFG") != "N/A":
                field = "MFG"
            elif idx == 2 and results.get("EXP") != "N/A":
                field = "EXP"
                
            line_info.append({'index': idx, 'field': field, 'y1': min_y, 'y2': max_y})
            
        for sc in smudged_chars:
            yc = sc['y_center']
            assigned = None
            for li in line_info:
                if li['y1'] <= yc <= li['y2']:
                    assigned = li['field']
                    break
            sc['field'] = assigned or "VALUES"
            
        return smudged_chars

    def _run_ocr(self, img, roi_date, con_date, rot_date, brightness=0, gamma=1.0, local_contrast=1.6, smoothing=0, connect_dots=0, threshold=0, gray_offset=0):
        """Worker function for OCR with robust LOT, MFG, EXP extraction and confidence score."""
        results = {"LOT": "N/A", "MFG": "N/A", "EXP": "N/A", "conf": 0.0, "time_ms": 0.0}
        if not roi_date: return results
        
        t_start = time.time()
        try:
            if isinstance(roi_date, bytes):
                coords = json.loads(roi_date.decode('utf-8'))
            elif isinstance(roi_date, str):
                coords = json.loads(roi_date)
            else:
                coords = roi_date
            t_s3_start = time.perf_counter()
            x1, y1, x2, y2 = coords
            crop = img[y1:y2, x1:x2]
            if crop.size > 0:
                crop = self._apply_adjustments(crop, con_date, rot_date, roi_name="Date")
            t_s3_end = time.perf_counter()
            log_stage_timing(3, "ROI extraction (Date)", t_s3_start, t_s3_end, extra_info=f"Size: {crop.shape if crop.size > 0 else 0}")

            if crop.size > 0:
                t_s4_start = time.perf_counter()
                b_val = brightness if brightness != 0 else gray_offset
                processed = preprocess_img(
                    crop,
                    brightness=b_val,
                    gamma=gamma,
                    local_contrast=local_contrast,
                    smoothing=smoothing,
                    connect_dots=connect_dots,
                    threshold=threshold
                )
                t_s4_end = time.perf_counter()
                log_stage_timing(4, "ROI preprocessing (Date)", t_s4_start, t_s4_end, extra_info=f"th={threshold}, b={b_val}")
                log_subcall_timing("Image Preprocessing (Date)", "First pass primary binarization", t_s4_start, t_s4_end, f"th={threshold}, b={b_val}")

                t_s5_start = time.perf_counter()
                texts, ocr_conf = self.ocr_service.extract_text_with_confidence(processed, caller_reason="Date OCR Pass 1 (Primary)")
                t_s5_end = time.perf_counter()
                log_stage_timing(5, "OCR attempt #1 (Date)", t_s5_start, t_s5_end, extra_info=f"Texts: {texts}, conf: {ocr_conf:.1f}%")

                # Auto-arbitration: if manual threshold was used and no texts detected, try Auto Otsu (threshold=0)
                if threshold > 0 and not texts:
                    t_s7_auto_start = time.perf_counter()
                    t_prep_auto_start = time.perf_counter()
                    p_auto = preprocess_img(
                        crop,
                        brightness=b_val,
                        gamma=gamma,
                        local_contrast=local_contrast,
                        smoothing=smoothing,
                        connect_dots=connect_dots,
                        threshold=0
                    )
                    t_prep_auto_end = time.perf_counter()
                    log_subcall_timing("Image Preprocessing (Date)", "Auto-Otsu retry (th=0)", t_prep_auto_start, t_prep_auto_end, f"b={b_val}")

                    texts_auto, auto_conf = self.ocr_service.extract_text_with_confidence(p_auto, caller_reason="Date OCR Auto-Otsu retry")
                    if texts_auto:
                        texts, ocr_conf, processed = texts_auto, auto_conf, p_auto
                    t_s7_auto_end = time.perf_counter()
                    log_stage_timing(7, "OCR retry / second attempt (Auto-Otsu Date)", t_s7_auto_start, t_s7_auto_end, is_ng_only=True, extra_info=f"Texts: {texts_auto}")

                t_s6_start = time.perf_counter()
                results["conf"] = ocr_conf
                
                full_text = " ".join(texts)
                
                # 1. Line-by-line search for LOT first (cleanest text without bleed-through)
                for line in texts:
                    line_clean = line.strip()
                    if not line_clean:
                        continue
                    if re.search(r'\b(?:LOT|BATCH|B\.?NO|BN)\b', line_clean, re.IGNORECASE):
                        parts = re.split(r'\b(?:LOT|BATCH|B\.?NO|BN)\b[:\.\-\s]*', line_clean, flags=re.IGNORECASE)
                        if len(parts) > 1 and parts[1]:
                            val = self._clean_lot_number(parts[1])
                            if val:
                                results["LOT"] = val
                                break

                # 2. Regex search across full text (handles single-line layouts & fallback)
                if results["LOT"] == "N/A":
                    lot_m = re.search(r'\b(?:LOT|BATCH|B\.?NO|BN|B/N)\b[\s:\.\-]*([0-9A-Za-z]+)', full_text, re.IGNORECASE)
                    if lot_m:
                        val = self._clean_lot_number(lot_m.group(1))
                        if val:
                            results["LOT"] = val

                # MFG: matches MFG, MFD, PROD, PRD, M followed by date (numbers & separators only)
                mfg_m = re.search(r'(?:MFG|MFD|PROD|PRD|M\.?D)[\s:\.\-]*([0-9\/\-\.]+)', full_text, re.IGNORECASE)
                if mfg_m:
                    val = self._clean_date_val(mfg_m.group(1))
                    if val and any(c.isdigit() for c in val):
                        results["MFG"] = val

                # EXP: matches EXP, EXPD, E.D, ED, BB, B.B, E followed by date (numbers & separators only)
                exp_m = re.search(r'(?:EXP|EXPD|E\.?D|BB|B\.?B)[\s:\.\-]*([0-9\/\-\.]+)', full_text, re.IGNORECASE)
                if exp_m:
                    val = self._clean_date_val(exp_m.group(1))
                    if val and any(c.isdigit() for c in val):
                        results["EXP"] = val

                # 3. Line-by-line fallback for any date field not yet matched
                for line in texts:
                    line_clean = line.strip()
                    if not line_clean:
                        continue
                    line_upper = line_clean.upper()

                    if results["MFG"] == "N/A" and any(k in line_upper for k in ["MFG", "MFD", "PROD"]):
                        parts = re.split(r'[:\.\-\s]+', line_clean, maxsplit=1)
                        if len(parts) > 1:
                            val = self._clean_date_val(parts[1])
                            if val and any(c.isdigit() for c in val):
                                results["MFG"] = val
                    if results["EXP"] == "N/A" and any(k in line_upper for k in ["EXP", "EXPD", "ED", "BB"]):
                        parts = re.split(r'[:\.\-\s]+', line_clean, maxsplit=1)
                        if len(parts) > 1:
                            val = self._clean_date_val(parts[1])
                            if val and any(c.isdigit() for c in val):
                                results["EXP"] = val

                # 4. Position-based fallback if lines were plain values without explicit labels
                if results["LOT"] == "N/A" and results["MFG"] == "N/A" and results["EXP"] == "N/A" and len(texts) >= 1:
                    date_lines = []
                    other_lines = []
                    for t in texts:
                        clean = t.strip()
                        if re.search(r'\d{1,2}[\/\-\.]\d{2,4}', clean):
                            date_lines.append(clean)
                        elif clean:
                            other_lines.append(clean)
                    if other_lines and results["LOT"] == "N/A":
                        val = self._clean_lot_number(other_lines[0])
                        if val:
                            results["LOT"] = val
                    if len(date_lines) >= 2:
                        if results["MFG"] == "N/A":
                            val = self._clean_date_val(date_lines[0])
                            if val and any(c.isdigit() for c in val):
                                results["MFG"] = val
                        if results["EXP"] == "N/A":
                            val = self._clean_date_val(date_lines[1])
                            if val and any(c.isdigit() for c in val):
                                results["EXP"] = val
                    elif len(date_lines) == 1:
                        val = self._clean_date_val(date_lines[0])
                        if val and any(c.isdigit() for c in val):
                            if results["MFG"] == "N/A":
                                results["MFG"] = val
                            elif results["EXP"] == "N/A":
                                results["EXP"] = val

                # 5. Clean trailing crop edge noise, disambiguate 0/6, and multi-pass candidate verification
                try:
                    expected_raw = self.redis_client.get("expected_values") if hasattr(self, 'redis_client') and self.redis_client else None
                    exp_data = json.loads(expected_raw) if isinstance(expected_raw, (str, bytes)) and expected_raw else {}
                    exp_lot = self._clean_lot_number(exp_data.get("LOT", ""))
                    exp_mfg = self._clean_date_val(exp_data.get("MFG", ""))
                    exp_exp = self._clean_date_val(exp_data.get("EXP", ""))

                    # Fix 0/6 confusion on MFG and EXP
                    if results["MFG"] != "N/A":
                        results["MFG"] = self._fix_date_zero_six(results["MFG"], exp_mfg)
                    if results["EXP"] != "N/A":
                        results["EXP"] = self._fix_date_zero_six(results["EXP"], exp_exp)

                    t_s6_end = time.perf_counter()
                    log_stage_timing(6, "OCR validation (Date)", t_s6_start, t_s6_end, extra_info=f"LOT: {results['LOT']}, MFG: {results['MFG']}, EXP: {results['EXP']}")

                    # Phase 1: Intelligent Retry Gating (6-state classification)
                    lot_state = self._classify_field_read(results["LOT"], exp_lot, results.get("conf", 0.0), field_type="LOT")
                    mfg_state = self._classify_field_read(results["MFG"], exp_mfg, results.get("conf", 0.0), field_type="MFG")
                    exp_state = self._classify_field_read(results["EXP"], exp_exp, results.get("conf", 0.0), field_type="EXP")

                    field_states = {"LOT": lot_state, "MFG": mfg_state, "EXP": exp_state}
                    recoverable_states = {"INCOMPLETE_READ", "LOW_CONFIDENCE_READ", "BLANK_READ", "SUSPICIOUS_READ"}
                    
                    # Needs multi-pass ONLY if there is at least one recoverable field state (incomplete, low conf, blank, suspicious)
                    needs_multipass = any(st in recoverable_states for st in field_states.values())
                    
                    mp_reasons = [f"{fld}: {st}" for fld, st in field_states.items() if st in recoverable_states]
                    mp_reason_str = ", ".join(mp_reasons) if mp_reasons else "Recoverable read"

                    if not needs_multipass and any(st == "CLEAR_CONFIDENT_MISMATCH" for st in field_states.values()):
                        log_subcall_timing("Retry Gating (Date)", "Bypass multi-pass retries", time.perf_counter(), time.perf_counter(), 
                                           f"Confident mismatch detected: LOT={lot_state}, MFG={mfg_state}, EXP={exp_state}")

                    if needs_multipass:
                        t_s7_multi_start = time.perf_counter()
                        for alt_th in [125, 95]:
                            if alt_th == threshold:
                                continue
                            t_palt_start = time.perf_counter()
                            p_alt = preprocess_img(
                                crop,
                                brightness=b_val,
                                gamma=gamma,
                                local_contrast=local_contrast,
                                smoothing=smoothing,
                                connect_dots=0,
                                threshold=alt_th
                            )
                            t_palt_end = time.perf_counter()
                            log_subcall_timing("Image Preprocessing (Date)", f"Multi-pass retry alt_th={alt_th}", t_palt_start, t_palt_end, f"Trigger: {mp_reason_str}")

                            alt_texts, alt_conf = self.ocr_service.extract_text_with_confidence(p_alt, caller_reason=f"Date OCR Multi-pass retry (alt_th={alt_th}, {mp_reason_str})")
                            if not alt_texts:
                                continue

                            alt_lot, alt_mfg, alt_exp = "N/A", "N/A", "N/A"
                            alt_date_lines = []
                            alt_other_lines = []
                            for t in alt_texts:
                                cl = t.strip()
                                if re.search(r'\d{1,2}[\/\-\.]\d{2,4}', cl):
                                    alt_date_lines.append(cl)
                                elif cl:
                                    alt_other_lines.append(cl)
                            if alt_other_lines:
                                alt_lot = self._clean_lot_number(alt_other_lines[0])
                            if len(alt_date_lines) >= 1:
                                alt_mfg = self._fix_date_zero_six(self._clean_date_val(alt_date_lines[0]), exp_mfg)
                            if len(alt_date_lines) >= 2:
                                alt_exp = self._fix_date_zero_six(self._clean_date_val(alt_date_lines[1]), exp_exp)

                            better = False
                            if exp_mfg and alt_mfg == exp_mfg and results["MFG"] != exp_mfg:
                                results["MFG"] = alt_mfg
                                better = True
                            if exp_exp and alt_exp == exp_exp and results["EXP"] != exp_exp:
                                results["EXP"] = alt_exp
                                better = True
                            if exp_lot and (alt_lot == exp_lot or (alt_lot.startswith(exp_lot) and len(alt_lot) <= len(exp_lot) + 1)) and results["LOT"] != exp_lot:
                                results["LOT"] = exp_lot
                                better = True

                            if better or (alt_conf > results.get("conf", 0.0) and results["MFG"] == "N/A"):
                                if alt_conf > results.get("conf", 0.0):
                                    results["conf"] = alt_conf
                                    processed = p_alt
                                if (not exp_lot or results["LOT"] == exp_lot) and (not exp_mfg or results["MFG"] == exp_mfg) and (not exp_exp or results["EXP"] == exp_exp):
                                    break
                        t_s7_multi_end = time.perf_counter()
                        log_stage_timing(7, "OCR retry / second attempt (Multi-pass Date)", t_s7_multi_start, t_s7_multi_end, is_ng_only=True, extra_info=f"needs_multipass=True | Result: LOT: {results['LOT']}, MFG: {results['MFG']}, EXP: {results['EXP']}")

                    # Final cleanup of edge noise against expected values
                    if exp_lot and results["LOT"] != "N/A":
                        if results["LOT"].startswith(exp_lot) and len(results["LOT"]) <= len(exp_lot) + 1:
                            results["LOT"] = exp_lot
                    if exp_mfg and results["MFG"] != "N/A":
                        if results["MFG"].startswith(exp_mfg) and len(results["MFG"]) <= len(exp_mfg) + 1:
                            results["MFG"] = exp_mfg
                    if exp_exp and results["EXP"] != "N/A":
                        if results["EXP"].startswith(exp_exp) and len(results["EXP"]) <= len(exp_exp) + 1:
                            results["EXP"] = exp_exp

                except Exception as e:
                    print(f"[ProcessingThread] Date verification error: {e}")

                # Character-level Smudge Check (only when inspection pipeline is active)
                is_inspecting = False
                try:
                    if hasattr(self, 'redis_client') and self.redis_client:
                        is_inspecting = (self.redis_client.get(settings.START_PIPELINE_KEY) == b"true")
                except Exception:
                    pass

                if is_inspecting:
                    t_s10_start = time.perf_counter()
                    try:
                        boxes_str = self.ocr_service.extract_boxes(processed)
                        parsed_boxes, smudged_chars = self.detect_smudged_characters(processed, boxes_str)
                        if smudged_chars:
                            smudged_chars = self._assign_fields_to_smudged(smudged_chars, parsed_boxes, results)
                            results["smudged_chars"] = smudged_chars
                    except Exception as e:
                        print(f"[ProcessingThread] Values smudge detection error: {e}")
                    t_s10_end = time.perf_counter()
                    log_stage_timing(10, "Defect/anomaly inspection", t_s10_start, t_s10_end, extra_info=f"Smudged chars: {len(results.get('smudged_chars', []))}")

            results["time_ms"] = round((time.time() - t_start) * 1000, 1)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ProcessingThread] OCR worker error: {e}")
        return results

    def _run_labels_ocr(self, img, roi_labels, con_labels, rot_labels, brightness=0, gamma=1.0, local_contrast=1.6, smoothing=0, connect_dots=0, threshold=0, expected_labels=""):
        """Worker function for Labels OCR (LOT : MFG : EXP :) strictly verifying against reference, all uppercase and NO digits."""
        results = {
            "LABELS_STATUS": "N/A",
            "LABELS_TEXT": "N/A",
            "LABELS_ERROR": "",
            "conf": 0.0,
            "time_ms": 0.0
        }
        if not roi_labels:
            return results

        t_start = time.time()
        try:
            if isinstance(roi_labels, bytes):
                coords = json.loads(roi_labels.decode('utf-8'))
            elif isinstance(roi_labels, str):
                coords = json.loads(roi_labels)
            else:
                coords = roi_labels
            t_s3_l_start = time.perf_counter()
            x1, y1, x2, y2 = coords
            crop = img[y1:y2, x1:x2]
            if crop.size > 0:
                crop = self._apply_adjustments(crop, con_labels, rot_labels, roi_name="Labels")
            t_s3_l_end = time.perf_counter()
            log_stage_timing(3, "ROI extraction (Labels)", t_s3_l_start, t_s3_l_end, extra_info=f"Size: {crop.shape if crop.size > 0 else 0}")

            if crop.size > 0:
                t_s4_l_start = time.perf_counter()
                b_val = brightness
                processed = preprocess_img(
                    crop,
                    brightness=b_val,
                    gamma=gamma,
                    local_contrast=local_contrast,
                    smoothing=smoothing,
                    connect_dots=connect_dots,
                    threshold=threshold
                )
                t_s4_l_end = time.perf_counter()
                log_stage_timing(4, "ROI preprocessing (Labels)", t_s4_l_start, t_s4_l_end, extra_info=f"th={threshold}, b={b_val}")
                log_subcall_timing("Image Preprocessing (Labels)", "First pass primary binarization", t_s4_l_start, t_s4_l_end, f"th={threshold}, b={b_val}")

                t_s5_l_start = time.perf_counter()
                labels_whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ "
                texts, ocr_conf = self.ocr_service.extract_text_with_confidence(processed, whitelist=labels_whitelist, caller_reason="Labels OCR Pass 1 (Primary)")
                t_s5_l_end = time.perf_counter()
                log_stage_timing(5, "OCR attempt #1 (Labels)", t_s5_l_start, t_s5_l_end, extra_info=f"Texts: {texts}, conf: {ocr_conf:.1f}%")

                if threshold > 0 and not texts:
                    t_s7_la_start = time.perf_counter()
                    t_prep_la_start = time.perf_counter()
                    p_auto = preprocess_img(
                        crop,
                        brightness=b_val,
                        gamma=gamma,
                        local_contrast=local_contrast,
                        smoothing=smoothing,
                        connect_dots=connect_dots,
                        threshold=0
                    )
                    t_prep_la_end = time.perf_counter()
                    log_subcall_timing("Image Preprocessing (Labels)", "Auto-Otsu retry (th=0)", t_prep_la_start, t_prep_la_end, f"b={b_val}")

                    texts_auto, auto_conf = self.ocr_service.extract_text_with_confidence(p_auto, whitelist=labels_whitelist, caller_reason="Labels OCR Auto-Otsu retry")
                    if texts_auto:
                        texts, ocr_conf, processed = texts_auto, auto_conf, p_auto
                    t_s7_la_end = time.perf_counter()
                    log_stage_timing(7, "OCR retry / second attempt (Auto-Otsu Labels)", t_s7_la_start, t_s7_la_end, is_ng_only=True, extra_info=f"Texts: {texts_auto}")

                t_s6_l_start = time.perf_counter()
                results["conf"] = ocr_conf
                full_text = " ".join(texts).strip()

                # Read labels without any conditions - strictly uppercase letters
                act_words = [w.upper() for w in re.findall(r'[A-Za-z]+', full_text)]
                norm_act_words = [self._normalize_label_word(w) for w in act_words if w]
                clean_label_text = " ".join(norm_act_words).upper()
                results["LABELS_TEXT"] = clean_label_text if clean_label_text else "EMPTY"

                # Validation against Expected reference if provided
                exp_words = [w.upper() for w in re.findall(r'[A-Za-z]+', expected_labels or "")]
                t_s6_l_end = time.perf_counter()
                log_stage_timing(6, "OCR validation (Labels)", t_s6_l_start, t_s6_l_end, extra_info=f"Text: '{clean_label_text}' vs Expected: '{expected_labels}'")

                if exp_words:
                    if norm_act_words == exp_words:
                        results["LABELS_STATUS"] = "PASS"
                        results["LABELS_ERROR"] = ""
                    else:
                        # Phase 1: Intelligent Retry Gating for Labels
                        if not norm_act_words:
                            label_state = "BLANK_READ"
                        elif ocr_conf < 50.0 and ocr_conf > 0.0:
                            label_state = "LOW_CONFIDENCE_READ"
                        elif len(norm_act_words) < len(exp_words):
                            label_state = "INCOMPLETE_READ"
                        else:
                            label_state = "CLEAR_CONFIDENT_MISMATCH"

                        # Allow multi-pass retry ONLY for recoverable states (incomplete, low conf, blank)
                        if label_state in ("INCOMPLETE_READ", "LOW_CONFIDENCE_READ", "BLANK_READ"):
                            missing = [w for w in exp_words if w not in norm_act_words]
                            t_s7_lm_start = time.perf_counter()
                            missing_str = ", ".join(missing) if missing else label_state
                            for alt_th in [125, 95]:
                                if alt_th == threshold:
                                    continue
                                t_palt_start = time.perf_counter()
                                p_alt = preprocess_img(
                                    crop,
                                    brightness=b_val,
                                    gamma=gamma,
                                    local_contrast=local_contrast,
                                    smoothing=smoothing,
                                    connect_dots=0,
                                    threshold=alt_th
                                )
                                t_palt_end = time.perf_counter()
                                log_subcall_timing("Image Preprocessing (Labels)", f"Multi-pass retry alt_th={alt_th}", t_palt_start, t_palt_end, f"Trigger: {label_state} ({missing_str})")

                                alt_texts, alt_conf = self.ocr_service.extract_text_with_confidence(p_alt, whitelist=labels_whitelist, caller_reason=f"Labels OCR Multi-pass retry (alt_th={alt_th}, trigger: {label_state})")
                                alt_full = " ".join(alt_texts).strip()
                                alt_words = [w.upper() for w in re.findall(r'[A-Za-z]+', alt_full)]
                                alt_norm_words = [self._normalize_label_word(w) for w in alt_words if w]
                                if alt_norm_words == exp_words:
                                    norm_act_words = alt_norm_words
                                    clean_label_text = " ".join(norm_act_words).upper()
                                    results["LABELS_TEXT"] = clean_label_text
                                    results["conf"] = alt_conf
                                    processed = p_alt
                                    break
                            t_s7_lm_end = time.perf_counter()
                            log_stage_timing(7, "OCR retry / second attempt (Multi-pass Labels)", t_s7_lm_start, t_s7_lm_end, is_ng_only=True, extra_info=f"State: {label_state} | Final: '{clean_label_text}'")
                        else:
                            log_subcall_timing("Retry Gating (Labels)", "Bypass multi-pass retries", time.perf_counter(), time.perf_counter(),
                                               f"CLEAR_CONFIDENT_MISMATCH: read '{clean_label_text}' vs expected '{expected_labels}'")

                        if norm_act_words == exp_words:
                            results["LABELS_STATUS"] = "PASS"
                            results["LABELS_ERROR"] = ""
                        else:
                            results["LABELS_STATUS"] = "FAIL"
                            missing = [w for w in exp_words if w not in norm_act_words]
                            results["LABELS_ERROR"] = f"Missing: {' '.join(missing)}" if missing else "Mismatch"
                else:
                    results["LABELS_STATUS"] = "PASS" if norm_act_words else "EMPTY"
                    results["LABELS_ERROR"] = ""

            results["time_ms"] = round((time.time() - t_start) * 1000, 1)
        except Exception as e:
            print(f"[ProcessingThread] Labels worker error: {e}")
            results["LABELS_STATUS"] = "FAIL"
            results["LABELS_ERROR"] = str(e)
        return results

    def _run_pharma(self, img, roi_pharma, con_pharma, rot_pharma):
        """Worker function for PharmaCode."""
        results = {"PHARMA_CODE": "N/A", "time_ms": 0.0}
        if not roi_pharma: return results
        
        t_start = time.time()
        try:
            t_s11_start = time.perf_counter()
            if isinstance(roi_pharma, bytes):
                coords = json.loads(roi_pharma.decode('utf-8'))
            elif isinstance(roi_pharma, str):
                coords = json.loads(roi_pharma)
            else:
                coords = roi_pharma
            x1, y1, x2, y2 = coords
            pad = 10
            h_img, w_img = img.shape[:2]
            px1, py1 = max(0, x1 - pad), max(0, y1 - pad)
            px2, py2 = min(w_img, x2 + pad), min(h_img, y2 + pad)
            crop = img[py1:py2, px1:px2]
            
            if crop.size > 0:
                crop = self._apply_adjustments(crop, con_pharma, rot_pharma)
                value, _ = read_pharma_code(crop)
                if value:
                    results["PHARMA_CODE"] = str(value)
            
            t_s11_end = time.perf_counter()
            log_stage_timing(11, "Pharmacode", t_s11_start, t_s11_end, extra_info=f"Code: {results['PHARMA_CODE']}")
            results["time_ms"] = round((time.time() - t_start) * 1000, 1)
        except Exception as e:
            print(f"[ProcessingThread] Pharma worker error: {e}")
        return results

    def process_frame(self, img: np.ndarray) -> dict:
        # 1. Sync locator from Redis if needed
        self.locator.sync_from_redis(
            self.redis_client,
            settings.ROI_LOCATOR_KEY,
            settings.ROI_LOCATOR_TEMPLATE_KEY
        )

        # Dynamically sync min confidence thresholds from Redis
        raw_conf = self.redis_client.get(settings.LOCATOR_MIN_CONFIDENCE_KEY)
        if raw_conf:
            try:
                self.locator_min_confidence = float(raw_conf)
            except Exception:
                pass

        raw_ocr_conf = self.redis_client.get(settings.OCR_MIN_CONFIDENCE_KEY)
        if raw_ocr_conf:
            try:
                self.ocr_min_confidence = float(raw_ocr_conf)
            except Exception:
                pass

        raw_labels_conf = self.redis_client.get(settings.LABELS_MIN_CONFIDENCE_KEY)
        if raw_labels_conf:
            try:
                self.labels_min_confidence = float(raw_labels_conf)
            except Exception:
                pass

        # 2. Run Object Locator
        t_s2_start = time.perf_counter()
        loc_res = self.locator.locate(img, min_confidence=self.locator_min_confidence)
        t_s2_end = time.perf_counter()
        log_stage_timing(2, "Locator", t_s2_start, t_s2_end, extra_info=f"Found: {loc_res.found}, Score: {loc_res.score:.2f}, dx: {loc_res.dx}, dy: {loc_res.dy}")
        try:
            self.redis_client.set(settings.LOCATOR_RESULT_KEY, json.dumps(loc_res.to_dict()))
        except Exception:
            pass

        # 3. Get settings (cached or fresh)
        roi_date = self.redis_client.get(settings.ROI_DATE_KEY)
        roi_pharma = self.redis_client.get(settings.ROI_PHARMA_KEY)
        roi_labels = self.redis_client.get(settings.ROI_LABELS_KEY)
        con_date = float(self.redis_client.get(settings.ROI_DATE_CONTRAST) or 1.0)
        con_pharma = float(self.redis_client.get(settings.ROI_PHARMA_CONTRAST) or 1.0)
        con_labels = float(self.redis_client.get(settings.ROI_LABELS_CONTRAST) or 1.0)
        rot_date = int(self.redis_client.get(settings.ROI_DATE_ROTATION) or 0)
        rot_pharma = int(self.redis_client.get(settings.ROI_PHARMA_ROTATION) or 0)
        rot_labels = int(self.redis_client.get(settings.ROI_LABELS_ROTATION) or 0)
        # Date preprocessing parameters
        bright_date = int(self.redis_client.get(settings.ROI_DATE_BRIGHTNESS) or self.redis_client.get(settings.ROI_DATE_GRAYSCALE) or 0)
        gamma_date = float(self.redis_client.get(settings.ROI_DATE_GAMMA) or 1.0)
        loc_con_date = float(self.redis_client.get(settings.ROI_DATE_LOCAL_CONTRAST) or 1.6)
        smooth_date = int(self.redis_client.get(settings.ROI_DATE_SMOOTHING) or 0)
        connect_dots_date = int(self.redis_client.get(settings.ROI_DATE_CONNECT_DOTS) or 0)
        threshold_date = int(self.redis_client.get(settings.ROI_DATE_THRESHOLD) or 0)

        # Labels preprocessing parameters
        bright_labels = int(self.redis_client.get(settings.ROI_LABELS_BRIGHTNESS) or self.redis_client.get(settings.ROI_LABELS_GRAYSCALE) or 0)
        gamma_labels = float(self.redis_client.get(settings.ROI_LABELS_GAMMA) or 1.0)
        loc_con_labels = float(self.redis_client.get(settings.ROI_LABELS_LOCAL_CONTRAST) or 1.6)
        smooth_labels = int(self.redis_client.get(settings.ROI_LABELS_SMOOTHING) or 0)
        connect_dots_labels = int(self.redis_client.get(settings.ROI_LABELS_CONNECT_DOTS) or 0)
        threshold_labels = int(self.redis_client.get(settings.ROI_LABELS_THRESHOLD) or 0)

        # 4. Check locator logic & shift ROIs
        if self.locator.has_template:
            if not loc_res.found:
                # Locator MISSED — carton missing or misaligned!
                print(f"[ProcessingThread] Object locator MISSED (score: {loc_res.score:.2f} < {self.locator_min_confidence})")
                return {
                    "LOT": "LOCATOR MISSED",
                    "MFG": "LOCATOR MISSED",
                    "EXP": "LOCATOR MISSED",
                    "PHARMA_CODE": "LOCATOR MISSED",
                    "LABELS_STATUS": "LOCATOR MISSED",
                    "LABELS_TEXT": "LOCATOR MISSED",
                    "LABELS_ERROR": "Locator Missed",
                    "ocr_conf": 0.0,
                    "labels_conf": 0.0,
                    "ocr_time_ms": 0.0,
                    "labels_time_ms": 0.0,
                    "pharma_time_ms": 0.0,
                    "locator_found": False,
                    "locator_score": loc_res.score,
                    "locator_dx": 0,
                    "locator_dy": 0,
                    "locator_bbox": None,
                    "locator_time_ms": loc_res.time_ms,
                    "shifted_roi_date": None,
                    "shifted_roi_pharma": None,
                    "shifted_roi_labels": None,
                }
            else:
                # Locator FOUND — Shift ROIs dynamically by (dx, dy)
                target_roi_date = self.locator.shift_roi(roi_date, loc_res.dx, loc_res.dy, img.shape)
                target_roi_pharma = self.locator.shift_roi(roi_pharma, loc_res.dx, loc_res.dy, img.shape)
                target_roi_labels = self.locator.shift_roi(roi_labels, loc_res.dx, loc_res.dy, img.shape)
        else:
            # Fallback: No locator configured, use static ROIs
            target_roi_date = roi_date
            target_roi_pharma = roi_pharma
            target_roi_labels = roi_labels

        # Fetch expected labels reference from Redis
        expected_labels = ""
        expected_raw = self.redis_client.get("expected_values")
        if expected_raw:
            try:
                expected_dict = json.loads(expected_raw)
                expected_labels = expected_dict.get("LABELS", "")
            except Exception:
                pass

        # 5. Parallelize OCR, Labels OCR, and PharmaCode
        future_ocr = self.executor.submit(
            self._run_ocr, img, target_roi_date, con_date, rot_date,
            bright_date, gamma_date, loc_con_date, smooth_date, connect_dots_date, threshold_date
        )
        future_labels = self.executor.submit(
            self._run_labels_ocr, img, target_roi_labels, con_labels, rot_labels,
            bright_labels, gamma_labels, loc_con_labels, smooth_labels, connect_dots_labels, threshold_labels,
            expected_labels=expected_labels
        )
        future_pharma = self.executor.submit(self._run_pharma, img, target_roi_pharma, con_pharma, rot_pharma)
        
        res_ocr = future_ocr.result()
        res_labels = future_labels.result()
        res_pharma = future_pharma.result()

        # Collect smudged characters from both OCR and Labels
        smudged_chars = []
        if res_ocr.get("smudged_chars"):
            smudged_chars.extend(res_ocr["smudged_chars"])
        if res_labels.get("smudged_chars"):
            smudged_chars.extend(res_labels["smudged_chars"])

        # 6. Consolidate results (direct per-frame evaluation)
        return {
            "LOT": res_ocr["LOT"],
            "MFG": res_ocr["MFG"],
            "EXP": res_ocr["EXP"],
            "ocr_conf": res_ocr.get("conf", 0.0),
            "PHARMA_CODE": res_pharma["PHARMA_CODE"],
            "LABELS_STATUS": res_labels.get("LABELS_STATUS", "N/A"),
            "LABELS_TEXT": res_labels.get("LABELS_TEXT", "N/A"),
            "LABELS_ERROR": res_labels.get("LABELS_ERROR", ""),
            "labels_conf": res_labels.get("conf", 0.0),
            "smudged_chars": smudged_chars,
            "ocr_time_ms": res_ocr["time_ms"],
            "labels_time_ms": res_labels.get("time_ms", 0.0),
            "pharma_time_ms": res_pharma["time_ms"],
            "locator_found": loc_res.found if self.locator.has_template else None,
            "locator_score": loc_res.score,
            "locator_dx": loc_res.dx,
            "locator_dy": loc_res.dy,
            "locator_bbox": loc_res.bbox,
            "locator_time_ms": loc_res.time_ms,
            "shifted_roi_date": target_roi_date if isinstance(target_roi_date, list) else (json.loads(target_roi_date) if target_roi_date else None),
            "shifted_roi_pharma": target_roi_pharma if isinstance(target_roi_pharma, list) else (json.loads(target_roi_pharma) if target_roi_pharma else None),
            "shifted_roi_labels": target_roi_labels if isinstance(target_roi_labels, list) else (json.loads(target_roi_labels) if target_roi_labels else None),
        }

    def stop(self):
        self.running = False
        # Performance Overhaul: Final sync on stop
        self._sync_counts_to_db()
