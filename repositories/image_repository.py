import os
import cv2
import numpy as np
from typing import List

class ImageRepository:
    def __init__(self, input_dir: str = ".\\new_test", output_dir: str = ".\\results"):
        self.input_dir = input_dir
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def get_input_images(self) -> List[str]:
        """Return a list of absolute paths for images in the input directory."""
        if not os.path.exists(self.input_dir):
            return []
        
        return [
            os.path.join(self.input_dir, i) 
            for i in os.listdir(self.input_dir)
            if i.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp'))
        ]

    def read_image(self, file_path: str) -> np.ndarray:
        """Read image from file path (unicode path safe)."""
        try:
            with open(file_path, "rb") as f:
                file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is None:
            raise FileNotFoundError(f"Failed to load image at {file_path}")
        return img

    def save_result_image(self, filename: str, image: np.ndarray) -> str:
        """Save a processed image to the results directory (unicode path safe)."""
        save_path = os.path.join(self.output_dir, filename)
        try:
            ext = os.path.splitext(save_path)[1]
            ret, buf = cv2.imencode(ext, image)
            if ret:
                with open(save_path, "wb") as f:
                    f.write(buf.tobytes())
            else:
                cv2.imwrite(save_path, image)
        except Exception:
            cv2.imwrite(save_path, image)
        return save_path

    def cleanup_old_captures(self, folder: str, keep: int = 10) -> None:
        """Keep only a certain number of recent captures in the specified folder (Phase 2)."""
        try:
            files = [
                os.path.join(folder, f) 
                for f in os.listdir(folder) 
                if f.lower().endswith(('.jpg', '.jpeg', '.png'))
            ]
            if len(files) > keep:
                files.sort(key=os.path.getmtime)
                for f in files[:-keep]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        except Exception:
            pass
