import os


class Settings:

    def __init__(self):
        # Read sensitive settings from environment first.
        self.DATABASE_URL = os.getenv(
            "DATABASE_URL",
            "sqlite:///BlueSquare.db"
        )
        self.HASH_SECRET_KEY = os.getenv(
            "HASH_SECRET_KEY",
            "development-only-secret-change-me"
        )
        
        # Clinical Security Settings
        self.AUTH_MAX_FAILED_ATTEMPTS = 5
        self.AUTH_PASSWORD_EXPIRY_DAYS = 90
        self.AUTH_MIN_PASSWORD_LENGTH = 8
        self.AUTH_SESSION_TIMEOUT_MINUTES = int(
            os.getenv("AUTH_SESSION_TIMEOUT_MINUTES", "10")
        )
        self.SQL_ECHO = os.getenv("SQL_ECHO", "false").strip().lower() in (
            "1", "true", "yes", "on"
        )

        # Redis Settings
        self.REDIS_HOST = "localhost"
        self.REDIS_PORT = 6379
        self.REDIS_DB = 0
        self.RAW_FRAMES_KEY = "raw_frames"
        self.RESULTS_KEY = "results"
        self.MAX_FRAMES = 100

        # ROI & Adjustments (New)
        self.ROI_LOCATOR_KEY = "roi_locator"
        self.ROI_LOCATOR_TEMPLATE_KEY = "roi_locator_template"
        self.LOCATOR_RESULT_KEY = "locator_result"
        self.LOCATOR_MIN_CONFIDENCE = 0.0
        self.LOCATOR_MIN_CONFIDENCE_KEY = "locator_min_confidence"
        self.ROI_DATE_KEY = "roi_date"
        self.OCR_MIN_CONFIDENCE = 0.0
        self.OCR_MIN_CONFIDENCE_KEY = "ocr_min_confidence"
        self.LABELS_MIN_CONFIDENCE = 0.0
        self.LABELS_MIN_CONFIDENCE_KEY = "labels_min_confidence"
        self.ROI_PHARMA_KEY = "roi_pharma"
        self.ROI_LABELS_KEY = "roi_labels"
        self.ROI_DATE_CONTRAST = "roi_date_contrast"
        self.ROI_PHARMA_CONTRAST = "roi_pharma_contrast"
        self.ROI_LABELS_CONTRAST = "roi_labels_contrast"
        self.ROI_DATE_ROTATION = "roi_date_rotation"
        self.ROI_PHARMA_ROTATION = "roi_pharma_rotation"
        self.ROI_LABELS_ROTATION = "roi_labels_rotation"
        self.ROI_DATE_GRAYSCALE = "roi_date_grayscale"
        self.ROI_DATE_THRESHOLD = "roi_date_threshold"
        self.ROI_DATE_BRIGHTNESS = "roi_date_brightness"
        self.ROI_DATE_GAMMA = "roi_date_gamma"
        self.ROI_DATE_LOCAL_CONTRAST = "roi_date_local_contrast"
        self.ROI_DATE_SMOOTHING = "roi_date_smoothing"
        self.ROI_DATE_CONNECT_DOTS = "roi_date_connect_dots"
        self.ROI_LABELS_GRAYSCALE = "roi_labels_grayscale"
        self.ROI_LABELS_THRESHOLD = "roi_labels_threshold"
        self.ROI_LABELS_BRIGHTNESS = "roi_labels_brightness"
        self.ROI_LABELS_GAMMA = "roi_labels_gamma"
        self.ROI_LABELS_LOCAL_CONTRAST = "roi_labels_local_contrast"
        self.ROI_LABELS_SMOOTHING = "roi_labels_smoothing"
        self.ROI_LABELS_CONNECT_DOTS = "roi_labels_connect_dots"
        self.LABELS_RESULT_KEY = "labels_result"
        self.START_PIPELINE_KEY = "start_pipeline"
        self.TRIGGER_REQUEST_KEY = "camera_trigger"
        self.ERROR_LED_TRIGGER_KEY = "error_led_trigger"
        self.INSPECTION_STATUS_KEY = "inspection_status"
        self.SHUTDOWN_REQUEST_KEY = "shutdown_requested"
        self.CAMERA_EXPOSURE_KEY = "camera_exposure"

        # Camera mode (Default to False to use the real camera; set to True in user_settings.json for mock mode)
        self.USE_MOCK_CAMERA = False

        # Inspection engine (user_settings.json):
        #   "auto"    = automatic OCR + fast golden-sample verification (no ROIs to teach)
        #   "classic" = previous ROI / Tesseract pipeline
        self.INSPECTION_ENGINE = "auto"
        self.AUTO_ROTATION = 90              # clockwise degrees that make the LOT/MFG/EXP print read left-to-right
        self.AUTO_SAVE_PASS_IMAGES = False   # rejected cartons are always saved
        self.AUTO_RETEACH_KEY = "auto_reteach"
        self.AUTO_STATUS_KEY = "auto_status"
        try:
            import json
            db_name = "BlueSquare.db"
            if self.DATABASE_URL.startswith("sqlite:///"):
                db_name = self.DATABASE_URL.replace("sqlite:///", "", 1)
            settings_path = os.path.join(os.path.dirname(os.path.abspath(db_name)), "user_settings.json")
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "use_mock_camera" in data:
                        self.USE_MOCK_CAMERA = bool(data["use_mock_camera"])
                    if str(data.get("inspection_engine", "")).lower() in ("auto", "classic"):
                        self.INSPECTION_ENGINE = str(data["inspection_engine"]).lower()
                    if "auto_rotation" in data:
                        self.AUTO_ROTATION = int(data["auto_rotation"]) % 360
                    if "auto_save_pass_images" in data:
                        self.AUTO_SAVE_PASS_IMAGES = bool(data["auto_save_pass_images"])
                    if "auth_max_failed_attempts" in data:
                        self.AUTH_MAX_FAILED_ATTEMPTS = int(data["auth_max_failed_attempts"])
                    if "auth_password_expiry_days" in data:
                        self.AUTH_PASSWORD_EXPIRY_DAYS = int(data["auth_password_expiry_days"])
                    if "auth_session_timeout_minutes" in data:
                        self.AUTH_SESSION_TIMEOUT_MINUTES = int(data["auth_session_timeout_minutes"])
        except Exception:
            pass

        # YOLO 
        self.yolo_path = "best.pt"

        # PLC Settings
        self.PLC_PORT = os.getenv("PLC_PORT", "COM3")
        self.PLC_BAUD = int(os.getenv("PLC_BAUD", "9600"))
        self.PLC_SLAVE_ID = int(os.getenv("PLC_SLAVE_ID", "1"))
        self.PLC_ENABLED = os.getenv("PLC_ENABLED", "true").lower() == "true"

settings = Settings()