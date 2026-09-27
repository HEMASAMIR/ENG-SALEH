import os
import sys
import json
import time
import cv2
import numpy as np

PROJECT_ROOT = r"C:\Users\User\Desktop\Blue Square 7-9-2026 V5 OCR HS\Blue Square 7-9-2026 V5\Blue Square 14-7-2026\New folder"
sys.path.insert(0, PROJECT_ROOT)

from services.redis_service import RedisManager
from services.ocr_service import OcrService
from core.config import settings
from processing_thread import ProcessingThread

def run_regression():
    print("=" * 110)
    print("         PHASE 1 REGRESSION TEST SUITE: 35 COMPREHENSIVE SAMPLES")
    print("=" * 110 + "\n")

    redis_mgr = RedisManager()
    if not redis_mgr.start():
        print("[ERROR] Failed to start Redis!")
        return

    import redis
    r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)
    r.set(settings.START_PIPELINE_KEY, "true")

    raw_frame_path = os.path.join(PROJECT_ROOT, "debug_raw_frame.png")
    img_bgr = cv2.imread(raw_frame_path)
    if img_bgr is None:
        print(f"[ERROR] Could not load {raw_frame_path}")
        redis_mgr.stop()
        return

    # Base configuration
    date_roi = [954, 58, 1085, 224]
    labels_roi = [973, 250, 1080, 326]
    pharma_roi = [939, 1038, 1281, 1083]
    locator_rect = [977, 373, 1110, 667]

    r.set(settings.ROI_DATE_KEY, json.dumps(date_roi))
    r.set(settings.ROI_LABELS_KEY, json.dumps(labels_roi))
    r.set(settings.ROI_PHARMA_KEY, json.dumps(pharma_roi))

    tess_path = os.path.join(PROJECT_ROOT, "bin", "tesseract", "tesseract.exe")
    ocr_service = OcrService(tesseract_cmd=tess_path, backend="hybrid", enable_fallbacks=True)

    pt = ProcessingThread(ocr_service=ocr_service)
    pt.locator.set_template(img_bgr, locator_rect)
    pt.locator.sync_to_redis(r, settings.ROI_LOCATOR_KEY, settings.ROI_LOCATOR_TEMPLATE_KEY)

    # Standard GOOD carton reference values
    base_expected = {
        "LOT": "03729",
        "MFG": "01-2826",
        "EXP": "41-2029",
        "PHARMA": "146",
        "LABELS": "LOT MFG EXP"
    }

    # Intercept stage and subcall counters
    counters = {"retries": 0, "paddle": 0, "image_to_boxes": 0}
    
    import core.timing_util
    orig_stage = core.timing_util.log_stage_timing
    orig_subcall = core.timing_util.log_subcall_timing

    def count_subcalls(subcall_name, reason, t_start, t_end, extra_info=""):
        if "Multi-pass retry" in subcall_name or "Multi-pass retry" in reason:
            counters["retries"] += 1
        if "PaddleOCR" in subcall_name:
            counters["paddle"] += 1
        if "image_to_boxes" in subcall_name or "image_to_boxes" in reason:
            counters["image_to_boxes"] += 1
        orig_subcall(subcall_name, reason, t_start, t_end, extra_info)

    core.timing_util.log_subcall_timing = count_subcalls

    def execute_sample(sample_id, desc, expected_dict, test_image, expected_verdict):
        counters["retries"] = 0
        counters["paddle"] = 0
        counters["image_to_boxes"] = 0

        r.set("expected_values", json.dumps(expected_dict))
        
        t0 = time.perf_counter()
        results = pt.process_frame(test_image)
        pt._handle_inspection_validation(results)
        t1 = time.perf_counter()
        
        total_time_ms = (t1 - t0) * 1000.0
        is_pass = not results.get("has_mismatch", False)
        verdict = "PASS" if is_pass else "FAIL"

        return {
            "id": sample_id,
            "desc": desc,
            "expected_verdict": expected_verdict,
            "actual_verdict": verdict,
            "time_ms": total_time_ms,
            "retries": counters["retries"],
            "paddle": counters["paddle"],
            "image_to_boxes": counters["image_to_boxes"],
            "results": results
        }

    # Pre-warm
    print("--- Pre-warming pipeline ---")
    _ = execute_sample("WARM", "Warmup", base_expected, img_bgr, "PASS")

    test_cases = []

    # 1. 5 GOOD SAMPLES
    for i in range(1, 6):
        test_cases.append((f"G{i:02d}", f"Pure GOOD Sample #{i:02d}", base_expected, img_bgr.copy(), "PASS"))

    # 2. 5 BAD LOT SAMPLES
    b_lot1 = base_expected.copy(); b_lot1["LOT"] = "99999"
    test_cases.append(("BL01", "LOT Confident Mismatch (99999 vs 03729, 5/5)", b_lot1, img_bgr.copy(), "FAIL"))
    b_lot2 = base_expected.copy(); b_lot2["LOT"] = "12345"
    test_cases.append(("BL02", "LOT Confident Mismatch (12345 vs 03729, 5/5)", b_lot2, img_bgr.copy(), "FAIL"))
    img_incomplete_lot = img_bgr.copy()
    img_incomplete_lot[65:90, 1050:1080] = 255  # Mask last digit '9'
    test_cases.append(("BL03", "LOT Incomplete Read (Masked digit, 4/5 read)", base_expected, img_incomplete_lot, "FAIL"))
    b_lot4 = base_expected.copy(); b_lot4["LOT"] = "88888"
    test_cases.append(("BL04", "LOT Confident Mismatch (88888 vs 03729, 5/5)", b_lot4, img_bgr.copy(), "FAIL"))
    b_lot5 = base_expected.copy(); b_lot5["LOT"] = "03728"
    test_cases.append(("BL05", "LOT Single Digit Mismatch (03728 vs 03729)", b_lot5, img_bgr.copy(), "FAIL"))

    # 3. 5 BAD MFG SAMPLES
    b_mfg1 = base_expected.copy(); b_mfg1["MFG"] = "06-2026"
    test_cases.append(("BM01", "MFG Confident Mismatch (06-2026 vs 01-2826)", b_mfg1, img_bgr.copy(), "FAIL"))
    b_mfg2 = base_expected.copy(); b_mfg2["MFG"] = "12-2025"
    test_cases.append(("BM02", "MFG Confident Mismatch (12-2025 vs 01-2826)", b_mfg2, img_bgr.copy(), "FAIL"))
    b_mfg3 = base_expected.copy(); b_mfg3["MFG"] = "01-28"  # Incomplete expected date
    test_cases.append(("BM03", "MFG Incomplete Date (01-28 vs 01-2826)", b_mfg3, img_bgr.copy(), "FAIL"))
    b_mfg4 = base_expected.copy(); b_mfg4["MFG"] = "02-2826"
    test_cases.append(("BM04", "MFG Month Mismatch (02-2826 vs 01-2826)", b_mfg4, img_bgr.copy(), "FAIL"))
    b_mfg5 = base_expected.copy(); b_mfg5["MFG"] = "01-2030"
    test_cases.append(("BM05", "MFG Year Mismatch (01-2030 vs 01-2826)", b_mfg5, img_bgr.copy(), "FAIL"))

    # 4. 5 BAD EXP SAMPLES
    b_exp1 = base_expected.copy(); b_exp1["EXP"] = "12-2030"
    test_cases.append(("BE01", "EXP Confident Mismatch (12-2030 vs 41-2029)", b_exp1, img_bgr.copy(), "FAIL"))
    b_exp2 = base_expected.copy(); b_exp2["EXP"] = "01-2028"
    test_cases.append(("BE02", "EXP Confident Mismatch (01-2028 vs 41-2029)", b_exp2, img_bgr.copy(), "FAIL"))
    b_exp3 = base_expected.copy(); b_exp3["EXP"] = "41-20"  # Incomplete
    test_cases.append(("BE03", "EXP Incomplete Date (41-20 vs 41-2029)", b_exp3, img_bgr.copy(), "FAIL"))
    b_exp4 = base_expected.copy(); b_exp4["EXP"] = "05-2029"
    test_cases.append(("BE04", "EXP Month Mismatch (05-2029 vs 41-2029)", b_exp4, img_bgr.copy(), "FAIL"))
    b_exp5 = base_expected.copy(); b_exp5["EXP"] = "41-2035"
    test_cases.append(("BE05", "EXP Year Mismatch (41-2035 vs 41-2029)", b_exp5, img_bgr.copy(), "FAIL"))

    # 5. 5 BAD LABELS SAMPLES
    b_lbl1 = base_expected.copy(); b_lbl1["LABELS"] = "BATCH PROD EXP"
    test_cases.append(("BLB01", "Labels Confident Mismatch (BATCH PROD EXP)", b_lbl1, img_bgr.copy(), "FAIL"))
    b_lbl2 = base_expected.copy(); b_lbl2["LABELS"] = "BN MFD ED"
    test_cases.append(("BLB02", "Labels Confident Mismatch (BN MFD ED)", b_lbl2, img_bgr.copy(), "FAIL"))
    b_lbl3 = base_expected.copy(); b_lbl3["LABELS"] = "LOT EXP"  # 2 words vs 3
    test_cases.append(("BLB03", "Labels Word Count Mismatch (LOT EXP vs 3 words)", b_lbl3, img_bgr.copy(), "FAIL"))
    b_lbl4 = base_expected.copy(); b_lbl4["LABELS"] = "LOT MFG BB"
    test_cases.append(("BLB04", "Labels Single Word Mismatch (LOT MFG BB)", b_lbl4, img_bgr.copy(), "FAIL"))
    b_lbl5 = base_expected.copy(); b_lbl5["LABELS"] = "LOT"  # 1 word
    test_cases.append(("BLB05", "Labels Missing 2 Words (LOT vs 3 words)", b_lbl5, img_bgr.copy(), "FAIL"))

    # 6. 5 BAD BLANK / OBSCURED SAMPLES
    img_blank_date = img_bgr.copy()
    img_blank_date[58:224, 954:1085] = 255  # White out Date ROI
    test_cases.append(("BB01", "Blank Date ROI (Whiteout)", base_expected, img_blank_date, "FAIL"))

    img_black_date = img_bgr.copy()
    img_black_date[58:224, 954:1085] = 0    # Black out Date ROI
    test_cases.append(("BB02", "Obscured Date ROI (Blackout)", base_expected, img_black_date, "FAIL"))

    img_blank_lbl = img_bgr.copy()
    img_blank_lbl[250:326, 973:1080] = 255  # White out Labels ROI
    test_cases.append(("BB03", "Blank Labels ROI (Whiteout)", base_expected, img_blank_lbl, "FAIL"))

    img_blank_both = img_bgr.copy()
    img_blank_both[58:224, 954:1085] = 255
    img_blank_both[250:326, 973:1080] = 255
    test_cases.append(("BB04", "Both Date & Labels Blank", base_expected, img_blank_both, "FAIL"))

    img_noisy = img_bgr.copy()
    noise = np.random.randint(0, 255, (166, 131, 3), dtype=np.uint8)
    img_noisy[58:224, 954:1085] = noise
    test_cases.append(("BB05", "Date ROI Pure Noise Pattern", base_expected, img_noisy, "FAIL"))

    # 7. 5 BAD SMUDGE / DEFECT SAMPLES
    img_smudge1 = img_bgr.copy()
    # Obliterate character '0' in '03729': Date crop has 0 at top-left
    # Draw solid black circle over the loop of 0
    cv2.circle(img_smudge1, (970, 75), 7, (0, 0, 0), -1)
    test_cases.append(("BS01", "Smudge Defect: Filled Loop on '0'", base_expected, img_smudge1, "FAIL"))

    img_smudge2 = img_bgr.copy()
    cv2.circle(img_smudge2, (975, 120), 8, (0, 0, 0), -1)
    test_cases.append(("BS02", "Smudge Defect: Filled Loop on '8'", base_expected, img_smudge2, "FAIL"))

    img_smudge3 = img_bgr.copy()
    cv2.circle(img_smudge3, (1020, 115), 14, (0, 0, 0), -1)
    test_cases.append(("BS03", "Smudge Defect: Ink Flood Blob", base_expected, img_smudge3, "FAIL"))

    img_smudge4 = img_bgr.copy()
    cv2.circle(img_smudge4, (970, 155), 7, (0, 0, 0), -1)
    test_cases.append(("BS04", "Smudge Defect: Filled Loop on '9'", base_expected, img_smudge4, "FAIL"))

    img_smudge5 = img_bgr.copy()
    cv2.circle(img_smudge5, (1040, 160), 12, (0, 0, 0), -1)
    test_cases.append(("BS05", "Smudge Defect: Large Ink Blot across EXP", base_expected, img_smudge5, "FAIL"))

    # Execute all 35 samples
    results_list = []
    print("\n" + "=" * 110)
    print(f"{'ID':<6} | {'DESCRIPTION':<40} | {'EXP':<5} | {'ACT':<5} | {'TIME (ms)':<10} | {'RETRY':<6} | {'PADDLE':<6} | {'BOXES':<6}")
    print("-" * 110)

    for case in test_cases:
        cid, cdesc, cexp_dict, cimg, cexp_verdict = case
        res = execute_sample(cid, cdesc, cexp_dict, cimg, cexp_verdict)
        results_list.append(res)
        
        match_icon = "PASS" if res["actual_verdict"] == res["expected_verdict"] else "FAIL (REGRESSION!)"
        print(f"{res['id']:<6} | {res['desc']:<40} | {res['expected_verdict']:<5} | {res['actual_verdict']:<5} | {res['time_ms']:>9.2f}  | {res['retries']:>5}  | {res['paddle']:>5}  | {res['image_to_boxes']:>5}  | {match_icon}")

    redis_mgr.stop()
    core.timing_util.log_subcall_timing = orig_subcall

    # Summary
    print("=" * 110)
    print("                         PHASE 1 REGRESSION SUMMARY")
    print("=" * 110)
    
    good_samples = [r for r in results_list if r["expected_verdict"] == "PASS"]
    bad_samples = [r for r in results_list if r["expected_verdict"] == "FAIL"]
    
    good_times = [r["time_ms"] for r in good_samples]
    bad_times = [r["time_ms"] for r in bad_samples]
    
    total_regressions = sum(1 for r in results_list if r["actual_verdict"] != r["expected_verdict"])
    
    print(f"Total Samples Tested  : {len(results_list)}")
    print(f"Regressions (Mismatch): {total_regressions}")
    print(f"GOOD Samples Pass Rate: {sum(1 for r in good_samples if r['actual_verdict'] == 'PASS')}/{len(good_samples)}")
    print(f"BAD Samples Reject Rate: {sum(1 for r in bad_samples if r['actual_verdict'] == 'FAIL')}/{len(bad_samples)}")
    print(f"\nGOOD Average Total Cycle Time: {sum(good_times)/len(good_times):.2f} ms (Min: {min(good_times):.2f}, Max: {max(good_times):.2f})")
    print(f"BAD Average Total Cycle Time : {sum(bad_times)/len(bad_times):.2f} ms (Min: {min(bad_times):.2f}, Max: {max(bad_times):.2f})")
    
    # Check retry count comparison for confident mismatches
    confident_bad = [r for r in results_list if r["id"] in ("BL01", "BL02", "BL04", "BL05", "BM01", "BM02", "BM04", "BM05", "BE01", "BE02", "BE04", "BE05", "BLB01", "BLB02", "BLB04")]
    avg_conf_retries = sum(r["retries"] for r in confident_bad) / len(confident_bad)
    print(f"Average Multi-pass Retries on Confident Mismatches: {avg_conf_retries} (Target: 0)")

if __name__ == "__main__":
    run_regression()
