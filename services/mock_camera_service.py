import cv2
import os
import time
from datetime import datetime
from typing import Optional, List
from core.camera_logic import CameraInterface
from models.camera_frame import CameraFrame

class MockCameraService(CameraInterface):
    """
    Mock camera service that reads images from a local directory.
    Simulates hardware triggers at ~1 FPS.
    """
    def __init__(self, frames_dir: str = "test_frames"):
        # Resolve path relative to the project root (New folder)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.frames_dir = os.path.join(project_root, frames_dir)
        self.frame_files = []
        self.current_idx = 0
        self.connected = False
        self.last_grab_time = 0
        self._load_frames()

    def _load_frames(self):
        if os.path.exists(self.frames_dir):
            self.frame_files = sorted([
                os.path.join(self.frames_dir, f) 
                for f in os.listdir(self.frames_dir) 
                if f.lower().endswith(('.bmp', '.jpg', '.png'))
            ])
        print(f"[MockCameraService] Loaded {len(self.frame_files)} frames from {self.frames_dir}")

    def connect(self, index: int = 0, trigger_line: str = "Line0", 
                exposure_us: int = 10000, gain_db: float = 0.0) -> tuple[bool, str]:
        if not self.frame_files:
            return False, f"No frames found in {self.frames_dir}"
        self.connected = True
        return True, "Mock camera connected successfully"

    def grab(self, timeout_ms: int = 5000) -> Optional[CameraFrame]:
        """Simulate hardware trigger by waiting 1 second between frames."""
        if not self.connected:
            return None

        # Simulate 1 FPS
        now = time.time()
        elapsed = now - self.last_grab_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        frame = self._read_next_frame()
        self.last_grab_time = time.time()
        return frame

    def grab_software(self, timeout_ms: int = 0xFFFFFFFF) -> Optional[CameraFrame]:
        """Immediate grab."""
        if not self.connected:
            return None
        return self._read_next_frame()

    def _read_next_frame(self) -> Optional[CameraFrame]:
        if not self.frame_files:
            return None

        file_path = self.frame_files[self.current_idx]
        img = None
        try:
            import numpy as np
            with open(file_path, "rb") as f:
                file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"[MockCameraService] Error reading image with unicode path: {e}")
        
        # Increment index and loop back
        self.current_idx = (self.current_idx + 1) % len(self.frame_files)

        if img is not None:
            return CameraFrame(image_data=img, timestamp=datetime.now())
        return None

    def trigger_software(self) -> bool:
        """Mock software trigger."""
        if not self.connected:
            return False
        # In mock, we don't need to do anything special, 
        # as next grab() or grab_software() will just work.
        return True

    def set_exposure(self, exposure_us: float) -> bool:
        """Mock exposure change — no-op."""
        print(f"[MockCameraService] Exposure set to {exposure_us} µs (mock)")
        return True

    def disconnect(self) -> None:
        self.connected = False
        self.current_idx = 0
