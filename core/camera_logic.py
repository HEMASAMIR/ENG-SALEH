from abc import ABC, abstractmethod
from typing import Optional
from models.camera_frame import CameraFrame

class CameraInterface(ABC):
    """Abstract interface for a camera to decouple hardware concerns."""

    @abstractmethod
    def connect(self, index: int = 0, trigger_line: str = "Line0", exposure_us: int = 10000, gain_db: float = 0.0) -> tuple[bool, str]:
        pass

    @abstractmethod
    def grab(self, timeout_ms: int = 0xFFFFFFFF) -> Optional[CameraFrame]:
        """Wait indefinitely for a hardware trigger and grab one frame."""
        pass
        
    @abstractmethod
    def grab_software(self, timeout_ms: int = 0xFFFFFFFF) -> Optional[CameraFrame]:
        """Grab one frame via software trigger (Free Run mode)."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass
