"""
Run the AUTO inspection service on a folder of camera images, exactly as on the line:
the first good carton is taught, every following carton is verified.

Usage:
    python tools/simulate_line.py <images_folder> --lot 03729 --mfg 01-2026 --exp 01-2029 --pharma 1682
    python tools/simulate_line.py <images_folder> --truth truth.json      (one run per recipe)

Prints one line per carton (mode, verdict, time, reason) and a summary.
"""
import os
import sys
import json
import glob
import argparse
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--lot"); ap.add_argument("--mfg"); ap.add_argument("--exp"); ap.add_argument("--pharma")
    ap.add_argument("--truth", help="truth.json: runs each recipe on its own images and scores the verdicts")
    ap.add_argument("--rotation", type=int, default=90)
    args = ap.parse_args()

    from services.auto_inspection_service import AutoInspectionService
    import services.auto_inspection_service as ais
    # keep the simulation's golden samples / logs out of the application folder
    tmp = tempfile.mkdtemp(prefix="bluesquare_sim_")
    ais._base_dir = lambda: tmp
    svc = AutoInspectionService(rotation=args.rotation, log_results=False)
    svc._ready.wait(120)

    files = sorted(glob.glob(os.path.join(args.folder, "*.*")))
    files = [f for f in files if f.lower().endswith((".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"))]
    runs = []
    if args.truth:
        truth = json.load(open(args.truth, encoding="utf-8"))
        files.sort(key=lambda p: truth["images"].get(os.path.basename(p), {}).get("n", 0))
        for rec, exp in truth["recipes"].items():
            fl = [f for f in files if truth["images"].get(os.path.basename(f), {}).get("recipe") == rec]
            runs.append((rec, {k: exp[k] for k in ("LOT", "MFG", "EXP", "PHARMA") if k in exp}, fl))
    else:
        exp = {k: v for k, v in (("LOT", args.lot), ("MFG", args.mfg), ("EXP", args.exp),
                                 ("PHARMA", args.pharma)) if v}
        runs.append(("recipe", exp, files))
        truth = None

    verify_ms, conf = [], {"PASS->PASS": 0, "PASS->FAIL": 0, "FAIL->FAIL": 0, "FAIL->PASS": 0}
    for rec, exp, fl in runs:
        print(f"\n=== {rec}  expected {exp}  ({len(fl)} images) ===")
        first = True
        for f in fl:
            img = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
            out = svc.process(img, exp, force_teach=first)
            first = False
            name = os.path.basename(f)
            t = truth["images"].get(name) if truth else None
            flag = ""
            if t:
                conf[f"{t['verdict']}->{out['auto_verdict']}"] += 1
                flag = "" if t["verdict"] == out["auto_verdict"] else "   <<< WRONG"
            if out["auto_mode"] == "VERIFY":
                verify_ms.append(out["auto_total_ms"])
            print(f"{(t or {}).get('n', name[-10:]):>4} {out['auto_mode']:6} {out['auto_verdict']:4} "
                  f"{out['auto_total_ms']:7.1f} ms  LOT={out['LOT']:<18} MFG={out['MFG']:<18} EXP={out['EXP']:<18} "
                  f"PH={out['PHARMA_CODE']:<5} {'; '.join(out.get('auto_reasons', []))[:70]}{flag}")

    if verify_ms:
        print(f"\nfast verification: {len(verify_ms)} cartons  avg {np.mean(verify_ms):.1f} ms  "
              f"p95 {np.percentile(verify_ms, 95):.1f} ms  max {np.max(verify_ms):.1f} ms  "
              f"under 25 ms: {sum(1 for x in verify_ms if x < 25)}/{len(verify_ms)}")
    if truth:
        print("good cartons passed      :", conf["PASS->PASS"])
        print("good cartons rejected    :", conf["PASS->FAIL"])
        print("defective cartons caught :", conf["FAIL->FAIL"])
        print("defective cartons passed :", conf["FAIL->PASS"], "(must be 0)")


if __name__ == "__main__":
    main()
