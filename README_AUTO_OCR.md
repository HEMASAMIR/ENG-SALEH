# Blue Square – AUTO OCR Inspection Engine

Automatic inspection of the inkjet (dot-matrix) print **LOT / MFG / EXP** and the
**Pharmacode** on pharmaceutical cartons, with PASS / FAIL, PLC reject signal and
result logging. No ROI has to be drawn or taught by hand.

## How it works

| Step | What happens | Time |
|---|---|---|
| 1. Camera (Daheng) | frame pushed to Redis as lossless BMP | – |
| 2. **TEACH** (first good carton after every *Start*, or when the recipe values change) | full OCR (PP-OCR / RapidOCR, CPU) finds the print anywhere in the frame, reads LOT / MFG / EXP, product name and Pharmacode, and compares them with the recipe. Only a carton that fully matches **and** has no print defect becomes the *golden sample* (saved in `golden/`). | ~1 s, once |
| 3. **VERIFY** (every following carton) | locate the print (any position, any print height), compare every character with the golden print (extra / missing ink), ink-blot check, Pharmacode decode | **~15 ms** (max 23 ms measured) |
| 4. Decision | PASS → M21, FAIL → M20 to the PLC (existing `plc_comm`), counters, UI | – |
| 5. Log | every result in `InspectionLog.db` (SQLite), image of every rejected carton in `inspection_images/<date>/` | background |

A defective carton is never taught: if the first cartons after *Start* are bad they are rejected
and the next good one is used.

## Results on the customer's 75 sample images (3 products)

Ground truth was set by visual inspection of every image (14 of them carry deliberate defects:
ink blots, malformed / over-printed digits, wrong character, `MF8` instead of `MFG`, scratch,
print cut by the frame).

| | Result |
|---|---|
| Defective cartons rejected | **14 / 14** (0 escapes) |
| Good cartons accepted | **59 / 61** |
| Good cartons rejected | 2 – the print is cut by the edge of the camera image (trigger timing); reported as *"print partly outside the camera view"* |
| Pharmacode | 75 / 75 decoded correctly (Famodar 1576, Myogesic 1682, Mixif 1474) |
| Verification time | avg 14.8 ms, p95 20.6 ms, max 23.0 ms – **69 / 69 under 25 ms** (4-core PC, includes colour→grey) |

## Settings (`user_settings.json`)

| Key | Default | Meaning |
|---|---|---|
| `inspection_engine` | `"auto"` | `"auto"` = this engine, `"classic"` = previous ROI / Tesseract pipeline |
| `auto_rotation` | `90` | clockwise rotation that makes the print read left-to-right in the camera image |
| `auto_save_pass_images` | `false` | also save images of good cartons |

Expected values come from the existing recipe / `expected_values` (LOT, MFG, EXP, PHARMA).
Re-teach on demand: set Redis key `auto_reteach` = `true` (or press Stop / Start).

## Installation on the line PC

```
install_ocr.bat          (installs rapidocr_onnxruntime + onnxruntime 1.19.2 into .venv)
run_app.bat              (unchanged)
```
`onnxruntime` 1.23 fails to load on some Windows 10 PCs ("DLL initialization routine failed");
1.19.2 is pinned for that reason. The PyInstaller spec (`bluesquare.spec`) includes the OCR models.

## Test tools (no camera needed)

```
python tools/simulate_line.py <images> --lot 10562 --mfg 06-2026 --exp 06-2028 --pharma 1474
python tools/simulate_line.py <images> --truth tools/truth_input_samples.json
python tools/evaluate_fast_verifier.py <images> --truth tools/truth_input_samples.json
python tools/evaluate_auto_inspector.py <images> --truth tools/truth_input_samples.json
```

## Before production sign-off

* Run on the real line PC and camera and confirm the timing there (numbers above are from a 4-core laptop).
* Fix the camera trigger / position so the whole print is always inside the image.
* The character threshold (`diff_area_ratio` = 0.11) was calibrated on 75 images: good prints reached
  0.10, the weakest defect 0.12. Collect a few hundred cartons from the line and re-check the margin.

## Files

| File | Purpose |
|---|---|
| `core/auto_inspector.py` | full OCR inspection (teach, reading, blot check, decision) |
| `core/fast_verifier.py` | golden-sample verification (< 25 ms) |
| `core/pharmacode_locator.py` | Pharmacode locator / decoder (no ROI) |
| `core/dotmatrix_ocr.py` | dot-matrix line helpers |
| `services/auto_inspection_service.py` | teach / verify workflow used by `processing_thread.py` |
| `services/inspection_log_service.py` | SQLite log + reject images |
| `tools/*.py` | offline evaluation / line simulation |
