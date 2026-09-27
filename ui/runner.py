import os
import cv2
import time
from repositories.image_repository import ImageRepository
from services.yolo_service import YoloService
from services.ocr_service import OcrService
from core.image_processing import preprocess_img
from core.pharmacode import read_pharma_code

class Runner:
    def __init__(self, output_dir: str = ".\\results"):
        """Initialize runner with dependencies."""
        self.image_repo = ImageRepository(input_dir=".\\new_test", output_dir=output_dir)
        self.yolo_service = YoloService(r".\runs\detect\runs\pharma_dates_v1\weights\best.pt")
        self.ocr_service = OcrService()

    def run(self):
        """Execute the main processing loop over input images."""
        paths = self.image_repo.get_input_images()
        print(f"Found {len(paths)} images to process.")

        for i, path in enumerate(paths):
            try:
                img = self.image_repo.read_image(path)
            except FileNotFoundError as e:
                print(e)
                continue

            start_time = time.time()
            detections = self.yolo_service.detect(img)
            filename = os.path.basename(path)

            for detection in detections:
                box = detection.box
                cropped_image = img[box.y1:box.y2, box.x1:box.x2]

                if detection.class_name == "date_block":
                    processed = preprocess_img(cropped_image)
                    processed = cv2.rotate(processed, cv2.ROTATE_90_CLOCKWISE)
                    
                    texts = self.ocr_service.extract_text(processed)
                    print(f"Detected Text: {texts}")

                    if texts:
                        line_height = 20
                        for line_idx, text_line in enumerate(texts):
                            if text_line and text_line.strip():
                                y_pos = max(box.y1 - 10 - (line_height * line_idx), 0)
                                (label_width, label_height), _ = cv2.getTextSize(text_line, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                                cv2.rectangle(img, (box.x1, y_pos - label_height - 5), (box.x1 + label_width, y_pos + 5), (255, 255, 255), -1)
                                cv2.putText(img, text_line, (box.x1, y_pos),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    
                    cv2.rectangle(img, (box.x1, box.y1), (box.x2, box.y2), (0, 255, 0), 2)

                elif detection.class_name == "pharma_code":
                    # Padded crop
                    pad = 10
                    h, w = img.shape[:2]
                    px1 = max(0, box.x1 - pad)
                    py1 = max(0, box.y1 - pad)
                    px2 = min(w, box.x2 + pad)
                    py2 = min(h, box.y2 + pad)

                    padded_crop = img[py1:py2, px1:px2]
                    
                    value, angle = read_pharma_code(
                        padded_crop,
                        debug_prefix=f"debug_{os.path.splitext(filename)[0]}"
                    )
                    
                    label = str(value) if value is not None else "?"
                    cv2.putText(img, label, (px1, max(py1 - 10, 0)),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 140, 0), 2)
                    cv2.rectangle(img, (px1, py1), (px2, py2), (255, 140, 0), 2)

            final_save_path = self.image_repo.save_result_image(f"final_{i}.png", img)
            print(f"Saved: {final_save_path}. Time: {(time.time() - start_time) * 1000:.2f}ms")

        print("Processing complete!")

if __name__ == "__main__":
    runner = Runner()
    runner.run()
