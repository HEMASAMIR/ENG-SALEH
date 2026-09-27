"""
Offline test of the fast (golden-sample) verifier.

Usage:
    python tools/evaluate_fast_verifier.py <images_folder> --truth truth.json [--out report.csv]

truth.json: same format as for evaluate_auto_inspector.py. For every recipe the first
image whose truth verdict is PASS is used as the golden sample (taught with the full OCR).
"""
import os
import sys
import csv
import json
import glob
import argparse

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.auto_inspector import AutoInspector  # noqa: E402
from core.fast_verifier import FastVerifier  # noqa: E402


def load_image(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out", default="fast_verifier_report.csv")
    ap.add_argument("--rotation", type=int, default=90)
    ap.add_argument("--repeat", type=int, default=3, help="timing repeats per image")
    args = ap.parse_args()

    truth = json.load(open(args.truth, encoding="utf-8"))
    files = sorted(glob.glob(os.path.join(args.folder, "*.*")),
                   key=lambda p: truth["images"].get(os.path.basename(p), {}).get("n", 0))
    files = [f for f in files if os.path.basename(f) in truth["images"]]

    insp = AutoInspector(rotation=args.rotation)
    insp.warmup()
    verifiers = {}
    for f in files:
        t = truth["images"][os.path.basename(f)]
        rec = t["recipe"]
        if rec in verifiers or t["verdict"] != "PASS":
            continue
        img = load_image(f)
        r = insp.inspect(img, truth["recipes"][rec])
        if r.verdict != "PASS":
            continue
        v = FastVerifier()
        v.teach(img, r, recipe=rec, rotation=args.rotation, expected=truth["recipes"][rec])
        verifiers[rec] = (v, os.path.basename(f))
        print(f"taught {rec} from {os.path.basename(f)}  lines={r.line_texts}  pharma={r.pharma_code}")

    conf = {"PASS->PASS": 0, "PASS->FAIL": 0, "FAIL->FAIL": 0, "FAIL->PASS": 0}
    times, rows = [], []
    for f in files:
        name = os.path.basename(f)
        t = truth["images"][name]
        if t["recipe"] not in verifiers:
            continue
        v, golden = verifiers[t["recipe"]]
        img = load_image(f)
        best = None
        for _ in range(args.repeat):
            r = v.verify(img)
            best = r if best is None or r.timings_ms["total"] < best.timings_ms["total"] else best
        r = best
        times.append(r.timings_ms["total"])
        conf[f"{t['verdict']}->{r.verdict}"] += 1
        flag = "" if t["verdict"] == r.verdict else "   <<< WRONG"
        tag = " (golden)" if name == golden else ""
        print(f"{t.get('n', ''):>3} {t['recipe']} truth={t['verdict']:4} got={r.verdict:4} "
              f"blk={r.block_score:.2f} LOT={r.field_scores.get('LOT', 0):.2f} MFG={r.field_scores.get('MFG', 0):.2f} "
              f"EXP={r.field_scores.get('EXP', 0):.2f} "
              f"diff={[r.worst_window.get(k, {}).get('diff', 0) for k in ('LOT', 'MFG', 'EXP')]} "
              f"sy={r.timings_ms.get('scale_y', '')} PH={r.pharma_code} {r.timings_ms['total']:5.1f}ms "
              f"{'; '.join(r.reasons)[:80]}{flag}{tag}")
        rows.append({"image": name, "truth": t["verdict"], "verdict": r.verdict, **r.timings_ms,
                     "block": r.block_score, **{f"score_{k}": s for k, s in r.field_scores.items()},
                     "pharma": r.pharma_code, "reasons": " | ".join(r.reasons)})

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        keys = sorted({k for row in rows for k in row})
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader(); w.writerows(rows)

    print(f"\nimages: {len(times)}  avg {np.mean(times):.1f} ms  p95 {np.percentile(times, 95):.1f} ms  "
          f"max {np.max(times):.1f} ms")
    print("  good cartons passed      :", conf["PASS->PASS"])
    print("  good cartons rejected    :", conf["PASS->FAIL"], "(false reject)")
    print("  defective cartons caught :", conf["FAIL->FAIL"])
    print("  defective cartons passed :", conf["FAIL->PASS"], "(ESCAPE - must be 0)")
    print("report:", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
