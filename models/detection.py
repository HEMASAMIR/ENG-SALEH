from dataclasses import dataclass
from typing import Optional, List

@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int

@dataclass
class DetectionResult:
    class_name: str
    confidence: float
    box: BoundingBox
    texts: Optional[List[str]] = None
    pharma_value: Optional[str] = None
    deskew_angle: float = 0.0
