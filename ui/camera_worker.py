import threading
from typing import Callable
from services.camera_service import CameraService
from repositories.image_repository import ImageRepository

class CameraWorker:
    """Non-PyQt dependent generic worker orchestrator."""
    def __init__(self, camera_service: CameraService, image_repo: ImageRepository, timeout_ms: int = 0xFFFFFFFF):
        self.cam = camera_service
        self.image_repo = image_repo
        self.timeout_ms = 0xFFFFFFFF
        self.running = False
        self._thread = None
        
        # Event callbacks
        self.on_frame_ready: Callable = None
        self.on_status_msg: Callable = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        if self.on_status_msg:
            self.on_status_msg("▶ Waiting for Hardware Trigger...")
            
        while self.running:
            frame_obj = self.cam.grab(self.timeout_ms)
            if not self.running: 
                break
                
            if frame_obj is None:
                if self.on_status_msg:
                    self.on_status_msg("⏳ No trigger — waiting...")
                continue
                
            # Process and Save
            ts = frame_obj.timestamp.strftime("%Y%m%d_%H%M%S_%f")[:20]
            fname = f"cap_{ts}.jpg"
            
            try:
                # Assuming save_result_image works generically for paths
                path = self.image_repo.save_result_image(fname, frame_obj.image_data)
                frame_obj.saved_path = path
                self.image_repo.cleanup_old_captures(self.image_repo.output_dir, 10)
                
                if self.on_status_msg:
                    self.on_status_msg(f"✓ Captured: {fname}")
                    
                if self.on_frame_ready:
                    self.on_frame_ready(frame_obj)
            except Exception as e:
                if self.on_status_msg:
                    self.on_status_msg(f"Error saving: {e}")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)
