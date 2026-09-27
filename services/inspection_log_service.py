"""
Inspection result log: every carton's result in SQLite (InspectionLog.db) and the
image of every rejected carton (optionally also passed ones) as lossless PNG.

Writing happens on a background thread so it never delays the reject decision.
"""
import os
import sys
import json
import queue
import sqlite3
import threading
from datetime import datetime

import cv2

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    recipe      TEXT,
    mode        TEXT,
    verdict     TEXT NOT NULL,
    lot         TEXT,
    mfg         TEXT,
    exp         TEXT,
    pharma      TEXT,
    product     TEXT,
    reasons     TEXT,
    time_ms     REAL,
    image_path  TEXT
);
CREATE INDEX IF NOT EXISTS idx_inspections_ts ON inspections(ts);
CREATE INDEX IF NOT EXISTS idx_inspections_verdict ON inspections(verdict);
"""


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InspectionLogService:
    def __init__(self, db_path: str = None, image_dir: str = None, save_pass_images: bool = False,
                 max_queue: int = 200):
        base = _base_dir()
        self.db_path = db_path or os.path.join(base, "InspectionLog.db")
        self.image_dir = image_dir or os.path.join(base, "inspection_images")
        self.save_pass_images = save_pass_images
        self._q = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._worker, name="InspectionLog", daemon=True)
        self._thread.start()

    def log(self, frame, record: dict):
        """Queue one result. Drops the entry (with a message) if the writer falls far behind."""
        save_img = record.get("verdict") != "PASS" or self.save_pass_images
        try:
            self._q.put_nowait((frame.copy() if (save_img and frame is not None) else None, dict(record)))
        except queue.Full:
            print("[InspectionLog] queue full - result not logged")

    def _worker(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.commit()
        while True:
            frame, rec = self._q.get()
            try:
                ts = rec.get("ts") or datetime.now().isoformat(timespec="milliseconds")
                img_path = ""
                if frame is not None:
                    day = ts[:10]
                    folder = os.path.join(self.image_dir, day)
                    os.makedirs(folder, exist_ok=True)
                    name = ts.replace(":", "").replace("-", "").replace(".", "_") + f"_{rec.get('verdict', '')}.png"
                    img_path = os.path.join(folder, name)
                    cv2.imwrite(img_path, frame)
                conn.execute(
                    "INSERT INTO inspections (ts, recipe, mode, verdict, lot, mfg, exp, pharma, product, reasons,"
                    " time_ms, image_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts, rec.get("recipe"), rec.get("mode"), rec.get("verdict"), rec.get("LOT"), rec.get("MFG"),
                     rec.get("EXP"), str(rec.get("PHARMA", "")), rec.get("product"),
                     json.dumps(rec.get("reasons", []), ensure_ascii=False), rec.get("time_ms"), img_path))
                conn.commit()
            except Exception as e:
                print(f"[InspectionLog] write error: {e}")
