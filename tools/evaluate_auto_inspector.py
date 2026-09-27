"""
Offline accuracy test for the automatic inspector.

Usage:
    python tools/evaluate_auto_inspector.py <images_folder> [--truth truth.json] [--out report.csv]

truth.json format:
{
  "recipes": {"MYO": {"LOT": "03729", "MFG": "01-2026", "EXP": "01-2029", "PHARMA": "1682"}, ...},
  "images":  {"Pic_...-15.bmp": {"recipe": "MYO", "verdict": "PASS"}, ...}
}
Without --truth the script only prints what it reads.
"""
import os
import sys
import csv
import json
import glob
import time
import argparse

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.auto_inspector import AutoInspector, FIELDS  # noqa: E402


def load_image(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--truth")
    ap.add_argument("--out", default="auto_inspector_report.csv")
    ap.add_argument("--rotation", type=int, default=90)
    args = ap.parse_args()

    truth = json.load(open(args.truth, encoding="utf-8")) if args.truth else None
    files = sorted(glob.glob(os.path.join(args.folder, "*.bmp")) + glob.glob(os.path.join(args.folder, "*.png"))
                   + glob.glob(os.path.join(args.folder, "*.jpg")))
    insp = AutoInspector(rotation=args.rotation)
    insp.warmup()

    rows, times = [], []
    conf_mat = {"PASS->PASS": 0, "PASS->FAIL": 0, "FAIL->FAIL": 0, "FAIL->PASS": 0}
    field_ok = {k: 0 for k in FIELDS + ("PHARMA",)}
    field_n = 0
    for f in files:
        name = os.path.basename(f)
        img = load_image(f)
        t = truth["images"].get(name) if truth else None
        expected = truth["recipes"][t["recipe"]] if t else {}
        r = insp.inspect(img, expected)
        times.append(r.timings_ms.get("total", 0))
        row = {"image": name, "verdict": r.verdict,
               **{k: r.fields[k].value for k in FIELDS}, "PHARMA": r.pharma_code,
               "product": r.product_text, "reasons": " | ".join(r.reasons),
               "ms": r.timings_ms.get("total", 0)}
        if t:
            row["truth"] = t["verdict"]
            conf_mat[f"{t['verdict']}->{r.verdict}"] += 1
            if t["verdict"] == "PASS":
                field_n += 1
                for k in FIELDS:
                    field_ok[k] += r.fields[k].value == str(expected.get(k))
                field_ok["PHARMA"] += str(r.pharma_code) == str(expected.get("PHARMA"))
            flag = "" if t["verdict"] == r.verdict else "   <<< WRONG"
        else:
            flag = ""
        print(f"{name[-12:]:>12}  {r.verdict:4}  LOT={r.fields['LOT'].value:8} MFG={r.fields['MFG'].value:9} "
              f"EXP={r.fields['EXP'].value:9} PH={str(r.pharma_code):5} {r.product_text[:18]:18} "
              f"{r.timings_ms.get('total', 0):6.0f}ms  {'; '.join(r.reasons)[:90]}{flag}")
        rows.append(row)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print("\nimages:", len(files), f" avg {np.mean(times):.0f} ms  max {np.max(times):.0f} ms")
    if truth:
        tot = sum(conf_mat.values())
        correct = conf_mat["PASS->PASS"] + conf_mat["FAIL->FAIL"]
        print(f"verdict accuracy: {correct}/{tot}")
        print("  good cartons passed      :", conf_mat["PASS->PASS"])
        print("  good cartons rejected    :", conf_mat["PASS->FAIL"], "(false reject)")
        print("  defective cartons caught :", conf_mat["FAIL->FAIL"])
        print("  defective cartons passed :", conf_mat["FAIL->PASS"], "(ESCAPE - must be 0)")
        print("field accuracy on good cartons:", {k: f"{v}/{field_n}" for k, v in field_ok.items()})
    print("report:", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
