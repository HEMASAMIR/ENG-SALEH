from dataclasses import dataclass
from typing import Optional
import numpy as np
from datetime import datetime

@dataclass
class CameraFrame:
    """Represents a single frame captured from the camera."""
    image_data: np.ndarray
    timestamp: datetime = datetime.now()
    frame_id: Optional[str] = None
    saved_path: Optional[str] = None
