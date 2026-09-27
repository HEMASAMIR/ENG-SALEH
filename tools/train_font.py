"""
Build the printer-font character library (core/font_library.npz) from good cartons.

Usage:
    python tools/train_font.py <images_folder> --truth truth.json
        (uses every image whose truth verdict is PASS, recipes from the truth file)
    python tools/train_font.py <images_folder> --lot 10562 --mfg 06-2026 --exp 06-2028 [--pharma 1474]
        (all images in the folder must be good cartons of that recipe)

The first image of every recipe that the full OCR passes is taught as golden sample; every
good image is then aligned to it and its characters are added to the library.
Run again whenever new products / print settings are added (the library only grows).
"""
import os
import sys
import json
import glob
import argparse

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.auto_inspector import AutoInspector, FIELDS          # noqa: E402
from core.fast_verifier import FastVerifier                     # noqa: E402
from core.font_classifier import FontLibrary, normalize_box    # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--truth")
    ap.add_argument("--lot"); ap.add_argument("--mfg"); ap.add_argument("--exp"); ap.add_argument("--pharma")
    ap.add_argument("--rotation", type=int, default=90)
    ap.add_argument("--out", default=None, help="default: core/font_library.npz")
    ap.add_argument("--append", action="store_true", help="add to the existing library")
    args = ap.parse_args()

    files = sorted(f for f in glob.glob(os.path.join(args.folder, "*.*"))
                   if f.lower().endswith((".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff")))
    groups = {}
    if args.truth:
        truth = json.load(open(args.truth, encoding="utf-8"))
        files.sort(key=lambda p: truth["images"].get(os.path.basename(p), {}).get("n", 0))
        for f in files:
            t = truth["images"].get(os.path.basename(f))
            if t and t["verdict"] == "PASS":
                groups.setdefault(t["recipe"], ([], truth["recipes"][t["recipe"]]))[0].append(f)
    else:
        exp = {k: v for k, v in (("LOT", args.lot), ("MFG", args.mfg), ("EXP", args.exp),
                                 ("PHARMA", args.pharma)) if v}
        groups["recipe"] = (files, exp)

    insp = AutoInspector(rotation=args.rotation)
    insp.warmup()
    samples = []
    for rec, (fl, exp) in groups.items():
        v = FastVerifier()
        taught = None
        for f in fl:
            img = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_GRAYSCALE)
            if taught is None:
                r = insp.inspect(img, exp)
                if r.verdict != "PASS":
                    continue
                v.teach(img, r, recipe=rec, rotation=args.rotation, expected=exp)
                taught = os.path.basename(f)
            res = v.verify(img, collect_chars=True)
            if res.verdict != "PASS" and not all(x.startswith(("LOT:", "MFG:", "EXP:")) for x in res.reasons):
                continue            # not aligned (print missing / out of view): skip this image
            n = 0
            for fld in FIELDS:
                for (ch, crop, thr, gm) in res.char_crops.get(fld, []):
                    if not ch.isalnum():
                        continue
                    vec = normalize_box(crop, thr)
                    if vec is not None:
                        samples.append((ch, vec)); n += 1
            print(f"{rec}: {os.path.basename(f)}  +{n} characters")
        print(f"{rec}: golden {taught}")

    lib = FontLibrary(path=args.out)
    if not args.append:
        lib.labels, lib.vectors = [], None
    lib.add(samples)
    lib.save(args.out)
    counts = {c: lib.labels.count(c) for c in sorted(set(lib.labels))}
    print(f"\nlibrary saved: {lib.path}\n{len(lib.labels)} samples: {counts}")


if __name__ == "__main__":
    main()
