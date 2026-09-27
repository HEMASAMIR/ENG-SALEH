# Blue Square – AUTO OCR Inspection Engine

Automatic inspection of the inkjet (dot-matrix) print **LOT / MFG / EXP** and the
**Pharmacode** on pharmaceutical cartons, with PASS / FAIL, PLC reject signal and
result logging. No ROI has to be drawn or taught by hand.

## How it works

| Step | What happens | Time |
|---|---|---|
| 1. Camera (Daheng) | frame pushed to Redis as lossless BMP (JPEG blurred the dot print) | – |
| 2. **TEACH** – first good carton after every *Start* or recipe change | full OCR (PP-OCR / RapidOCR, CPU) finds the print anywhere in the frame, reads LOT / MFG / EXP, product name and Pharmacode, compares them with the recipe. Only a carton that fully matches **and** has no print defect becomes the *golden sample* (saved in `golden/`). Defective cartons are rejected and never taught. | ~1–1.6 s, once |
| 3. **VERIFY** – every following carton | locate the print (any position, any print height), compare every character with the golden print (extra / missing ink), ink-blot check, print-in-view check, Pharmacode decode | **~13–15 ms** |
| 4. **OCR confirmation** – only when a character is close to the limit | the line is read by the OCR recogniser (lines already located, no text detection) and compared with the recipe; catches a digit printed like another digit (e.g. `6` printed like `8`) which differs from the golden print by only a few dots | +25–200 ms, ~25 % of cartons |
| 5. Decision | PASS → M21, FAIL → M20 to the PLC (existing `plc_comm`), counters, UI | – |
| 6. Log | every result in `InspectionLog.db` (SQLite), image of every rejected carton in `inspection_images/<date>/` | background |

## Results on the customer's 75 sample images (3 products, 14 deliberately defective)

Ground truth set by visual inspection of every image. Defects in the set: ink blots on
letters / digits, malformed or over-printed digits (`6` printed like `8`), wrong LOT digit,
`MF8` instead of `MFG`, scratch through a digit, print cut by the frame.

**Full application run** (Redis + mock camera fed with the 75 images + processing thread + UI + log,
each result identified by the pixels of its saved frame):

| | Result |
|---|---|
| Defective cartons rejected | **14 / 14** – 0 escapes |
| Good cartons accepted | 57 / 61 |
| Good cartons rejected | 28, 51: print cut by the edge of the camera image (trigger timing) → *"print partly outside the camera view"*; 19: full OCR did not find the print while teaching (the next carton was taught); 74: 12 % ink difference on the `F` of `MFG` (limit 11 %) |
| Pharmacode | 75 / 75 decoded correctly offline (Famodar 1576, Myogesic 1682, Mixif 1474) |
| Decision time | 55 / 74 cartons under 25 ms; cartons that needed OCR confirmation 36–214 ms |

**Robustness test** – every OCR-verified good carton used in turn as golden sample
(846 good-carton checks, 192 defective-carton checks): **0 defective cartons passed**,
good cartons rejected 7.4 % – of which 3.5 % are the two cut-off images; 4 % otherwise.
Without the OCR confirmation the fast check alone let 18 / 192 defective checks pass
(the `6`-printed-like-`8` cartons), which is why the confirmation is on by default.

## Settings (`user_settings.json`)

| Key | Default | Meaning |
|---|---|---|
| `inspection_engine` | `"auto"` | `"auto"` = this engine, `"classic"` = previous ROI / Tesseract pipeline |
| `auto_rotation` | `90` | clockwise rotation that makes the print read left-to-right in the camera image |
| `auto_escalation_threshold` | `0.03` | golden-difference above which a line is also read by OCR before PASS; `"off"` = fast check only (all cartons < 25 ms, but subtle digit defects can pass) |
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
python tools/evaluate_auto_inspector.py <images> --truth tools/truth_input_samples.json
```

## Before production sign-off

* Run on the real line PC and camera; confirm the times there (numbers above: 4-core laptop).
* Check that the reject station is far enough from the camera for the OCR-confirmed cartons
  (up to ~0.2 s), or set `auto_escalation_threshold` to `"off"` if every decision must be < 25 ms.
* Fix the camera trigger / position so the whole print is always inside the image.
* Collect a few hundred cartons from the line and re-check the limits (character difference 11 %,
  escalation 3 %) – they were set on 75 images.

## Files

| File | Purpose |
|---|---|
| `core/auto_inspector.py` | full OCR inspection (teach, reading, blot check, decision) |
| `core/fast_verifier.py` | golden-sample verification |
| `core/pharmacode_locator.py` | Pharmacode locator / decoder (no ROI) |
| `core/dotmatrix_ocr.py` | dot-matrix line helpers |
| `core/font_classifier.py` | printer-font character classifier – experimental, **not used** (unstable across golden samples in tests) |
| `services/auto_inspection_service.py` | teach / verify / OCR-confirmation workflow used by `processing_thread.py` |
| `services/inspection_log_service.py` | SQLite log + reject images |
| `tools/*.py` | offline evaluation, line simulation, font-library builder (experimental) |
