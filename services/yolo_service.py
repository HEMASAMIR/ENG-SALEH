from ultralytics import YOLO
import numpy as np
from typing import List
from models.detection import DetectionResult, BoundingBox
from core.config import settings

class YoloService:
    def __init__(self):
        self.model = YOLO(settings.yolo_path)

    def detect(self, image: np.ndarray) -> List[DetectionResult]:
        """Run YOLO inference and return standard DetectionResult objects."""
        results = self.model(image)
        detections = []
        
        for result in results:
            names = result.names
            for box in result.boxes:
                cls_id = int(box.cls[0])
                cls_name = names[cls_id]
                conf = float(box.conf[0])
                
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                
                detections.append(DetectionResult(
                    class_name=cls_name,
                    confidence=conf,
                    box=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)
                ))
                
        return detections
