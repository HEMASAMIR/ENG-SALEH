import os

# Limit CPU threading for OpenCV, Tesseract (OpenMP), and NumPy to prevent CPU hogging
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OMP_THREAD_LIMIT"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import time
import sys
import signal
import json
import redis
from camera_thread import CameraThread
from processing_thread import ProcessingThread
from ui_thread import UIThread
from services.camera_service import CameraService
from services.mock_camera_service import MockCameraService
from services.ocr_service import OcrService
from services.recipe_service import RecipeService
from services.redis_service import RedisManager
from core.database import get_session
from core.config import settings
from core.db_init import initialize_database
from core.path_util import get_bin_path
from core.user_settings import (
    load_camera_exposure, save_camera_exposure, load_locator_confidence,
    load_ocr_confidence, load_labels_confidence, load_grayscale_parameter,
    load_threshold_parameter, load_contrast_parameter, load_rotation_parameter,
    load_expected_values, load_brightness_parameter, save_brightness_parameter,
    load_gamma_parameter, save_gamma_parameter, load_local_contrast_parameter,
    save_local_contrast_parameter, load_smoothing_parameter, save_smoothing_parameter,
    load_connect_dots_parameter, save_connect_dots_parameter
)
from services.plc_comm import PlcCommunicator

from ui.main import BlueSquareApp

def main():
    print("--- BLUE SQUARE SYSTEM STARTUP ---")
    
    # 0. Initialize Database (SQLite Auto-creation)
    initialize_database()

    # 1. Initialize Infrastructure (Redis)
    redis_manager = RedisManager()
    if not redis_manager.start():
        print("[Critical] Failed to start Redis. Exiting...")
        sys.exit(1)

    # Initialize Redis client for pipeline state
    r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)
    r.set(settings.START_PIPELINE_KEY, "false")
    r.set(settings.SHUTDOWN_REQUEST_KEY, "false")
    r.delete(settings.RAW_FRAMES_KEY) # clear old frames

    saved_exposure = load_camera_exposure()
    r.set(settings.CAMERA_EXPOSURE_KEY, str(saved_exposure))
    print(f"[Startup] Loaded camera exposure: {saved_exposure} µs")
    
    saved_conf = load_locator_confidence()
    r.set(settings.LOCATOR_MIN_CONFIDENCE_KEY, str(saved_conf))
    print(f"[Startup] Loaded Object Locator min confidence: {saved_conf}")

    # Seed Object Locator from disk to Redis so pipeline has it from frame 1
    from core.object_locator import ObjectLocator
    init_locator = ObjectLocator()
    if init_locator.has_template:
        init_locator.sync_to_redis(r, settings.ROI_LOCATOR_KEY, settings.ROI_LOCATOR_TEMPLATE_KEY)
        print(f"[Startup] Loaded Object Locator template: {init_locator.ref_rect} ({init_locator.ref_w}x{init_locator.ref_h})")
    else:
        print("[Startup] No Object Locator template saved.")
    
    saved_ocr_conf = load_ocr_confidence()
    r.set(settings.OCR_MIN_CONFIDENCE_KEY, str(saved_ocr_conf))
    print(f"[Startup] Loaded OCR min confidence: {saved_ocr_conf}")

    saved_labels_conf = load_labels_confidence()
    r.set(settings.LABELS_MIN_CONFIDENCE_KEY, str(saved_labels_conf))
    print(f"[Startup] Loaded Labels OCR min confidence: {saved_labels_conf}")

    # Contrasts
    for prefix in ['labels', 'date', 'pharma']:
        c_val = load_contrast_parameter(prefix)
        k = settings.ROI_LABELS_CONTRAST if prefix == 'labels' else (settings.ROI_DATE_CONTRAST if prefix == 'date' else settings.ROI_PHARMA_CONTRAST)
        r.set(k, str(c_val))
        print(f"[Startup] Loaded {prefix} contrast: {c_val}")

    # Rotations
    for prefix in ['labels', 'date', 'pharma']:
        rot_val = load_rotation_parameter(prefix)
        k = settings.ROI_LABELS_ROTATION if prefix == 'labels' else (settings.ROI_DATE_ROTATION if prefix == 'date' else settings.ROI_PHARMA_ROTATION)
        r.set(k, str(rot_val))
        print(f"[Startup] Loaded {prefix} rotation: {rot_val}")

    # Preprocessing parameters (Brightness, Gamma, Local Contrast, Smoothing, Connect Dots, Threshold)
    for prefix in ['labels', 'date']:
        b_val = load_brightness_parameter(prefix)
        bk = settings.ROI_DATE_BRIGHTNESS if prefix == 'date' else settings.ROI_LABELS_BRIGHTNESS
        gk = settings.ROI_DATE_GRAYSCALE if prefix == 'date' else settings.ROI_LABELS_GRAYSCALE
        r.set(bk, str(b_val))
        r.set(gk, str(b_val))

        gamma_val = load_gamma_parameter(prefix)
        gamma_k = settings.ROI_DATE_GAMMA if prefix == 'date' else settings.ROI_LABELS_GAMMA
        r.set(gamma_k, str(gamma_val))

        lc_val = load_local_contrast_parameter(prefix)
        lc_k = settings.ROI_DATE_LOCAL_CONTRAST if prefix == 'date' else settings.ROI_LABELS_LOCAL_CONTRAST
        r.set(lc_k, str(lc_val))

        sm_val = load_smoothing_parameter(prefix)
        sm_k = settings.ROI_DATE_SMOOTHING if prefix == 'date' else settings.ROI_LABELS_SMOOTHING
        r.set(sm_k, str(sm_val))

        cd_val = load_connect_dots_parameter(prefix)
        cd_k = settings.ROI_DATE_CONNECT_DOTS if prefix == 'date' else settings.ROI_LABELS_CONNECT_DOTS
        r.set(cd_k, str(cd_val))

        t_val = load_threshold_parameter(prefix)
        tk = settings.ROI_DATE_THRESHOLD if prefix == 'date' else settings.ROI_LABELS_THRESHOLD
        r.set(tk, str(t_val))
        print(f"[Startup] Loaded {prefix} preprocessing: bright={b_val}, gamma={gamma_val}, lc={lc_val}, smooth={sm_val}, dots={cd_val}, thresh={t_val}")

    # Expected Values
    exp_vals = load_expected_values()
    r.set("expected_values", json.dumps(exp_vals))
    print(f"[Startup] Loaded expected values: {exp_vals}")
    
    # 2. Ensure all recipes are inactive on launch
    try:
        with get_session() as session:
            recipe_service = RecipeService(session)
            recipe_service.deactivate_all(user_id=1)
            print("[Startup] All recipes deactivated.")
    except Exception as e:
        print(f"[Startup] Database connection error: {e}")
    
    # 3. Initialize services
    if settings.USE_MOCK_CAMERA:
        print("[Startup] Using Mock Camera Service.")
        camera_service = MockCameraService()
    else:
        print("[Startup] Using Real Daheng Camera Service.")
        camera_service = CameraService()
        
    import tempfile
    import shutil
    tess_path = get_bin_path(os.path.join("tesseract", "tesseract.exe"))
    tessdata_dir = os.path.join(os.path.dirname(tess_path), "tessdata")
    if os.path.isdir(tessdata_dir):
        temp_tessdata_dir = os.path.join(tempfile.gettempdir(), "BlueSquare_tessdata")
        try:
            os.makedirs(temp_tessdata_dir, exist_ok=True)
            for item in os.listdir(tessdata_dir):
                s = os.path.join(tessdata_dir, item)
                d = os.path.join(temp_tessdata_dir, item)
                if os.path.isdir(s):
                    if not os.path.exists(d):
                        shutil.copytree(s, d)
                else:
                    if not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d):
                        shutil.copy2(s, d)
            os.environ["TESSDATA_PREFIX"] = temp_tessdata_dir + os.sep
            print(f"[Startup] Copied tessdata to temp: {temp_tessdata_dir}")
        except Exception as te:
            print(f"[Startup] Warning: Failed to copy tessdata to temp ({te}). Using original directory.")
            os.environ["TESSDATA_PREFIX"] = tessdata_dir + os.sep
    print(f"[Startup] Initializing OCR Service with Tesseract at: {tess_path}")
    ocr_service = OcrService(tesseract_cmd=tess_path)
    
    plc_comm = PlcCommunicator()
    if settings.PLC_ENABLED:
        print("[Startup] Auto-detecting PLC COM port...")
        detected_port = plc_comm.auto_detect_port(settings.PLC_BAUD)
        if detected_port:
            print(f"[Startup] PLC detected and connected on {detected_port}.")
        else:
            print(f"[Startup] PLC not auto-detected. Falling back to configured port: {settings.PLC_PORT}")
            plc_comm.connect_plc(settings.PLC_PORT, settings.PLC_BAUD)

    # 4. Initialize Background Threads
    cam_thread = CameraThread(camera_service)
    proc_thread = ProcessingThread(ocr_service, plc_comm)
    
    proc_thread.start()
    cam_thread.start()

    # 5. Initialize and Run UI (MUST be in main thread)
    print("[Startup] Initializing Blue Square UI...")
    app_controller = BlueSquareApp(plc_comm)
    
    def shutdown_flow():
        print("\n[Shutdown] Closing BLUE SQUARE...")
        try:
            r.set(settings.START_PIPELINE_KEY, "false")
            r.set(settings.SHUTDOWN_REQUEST_KEY, "false")

            from core.user_settings import (
                save_camera_exposure, save_locator_confidence, save_ocr_confidence,
                save_labels_confidence, save_contrast_parameter, save_rotation_parameter,
                save_grayscale_parameter, save_threshold_parameter, save_expected_values
            )
            exposure_raw = r.get(settings.CAMERA_EXPOSURE_KEY)
            if exposure_raw:
                save_camera_exposure(float(exposure_raw))

            loc_conf = r.get(settings.LOCATOR_MIN_CONFIDENCE_KEY)
            if loc_conf:
                save_locator_confidence(float(loc_conf))

            ocr_conf = r.get(settings.OCR_MIN_CONFIDENCE_KEY)
            if ocr_conf:
                save_ocr_confidence(float(ocr_conf))

            lbl_conf = r.get(settings.LABELS_MIN_CONFIDENCE_KEY)
            if lbl_conf:
                save_labels_confidence(float(lbl_conf))

            for prefix in ['labels', 'date', 'pharma']:
                k = settings.ROI_LABELS_CONTRAST if prefix == 'labels' else (settings.ROI_DATE_CONTRAST if prefix == 'date' else settings.ROI_PHARMA_CONTRAST)
                raw_c = r.get(k)
                if raw_c:
                    save_contrast_parameter(prefix, float(raw_c))

                rk = settings.ROI_LABELS_ROTATION if prefix == 'labels' else (settings.ROI_DATE_ROTATION if prefix == 'date' else settings.ROI_PHARMA_ROTATION)
                raw_r = r.get(rk)
                if raw_r:
                    save_rotation_parameter(prefix, int(raw_r))

            for prefix in ['labels', 'date']:
                bk = settings.ROI_DATE_BRIGHTNESS if prefix == 'date' else settings.ROI_LABELS_BRIGHTNESS
                raw_b = r.get(bk)
                if raw_b:
                    save_brightness_parameter(int(raw_b), prefix)

                gamma_k = settings.ROI_DATE_GAMMA if prefix == 'date' else settings.ROI_LABELS_GAMMA
                raw_gamma = r.get(gamma_k)
                if raw_gamma:
                    save_gamma_parameter(float(raw_gamma), prefix)

                lc_k = settings.ROI_DATE_LOCAL_CONTRAST if prefix == 'date' else settings.ROI_LABELS_LOCAL_CONTRAST
                raw_lc = r.get(lc_k)
                if raw_lc:
                    save_local_contrast_parameter(float(raw_lc), prefix)

                sm_k = settings.ROI_DATE_SMOOTHING if prefix == 'date' else settings.ROI_LABELS_SMOOTHING
                raw_sm = r.get(sm_k)
                if raw_sm:
                    save_smoothing_parameter(int(raw_sm), prefix)

                cd_k = settings.ROI_DATE_CONNECT_DOTS if prefix == 'date' else settings.ROI_LABELS_CONNECT_DOTS
                raw_cd = r.get(cd_k)
                if raw_cd:
                    save_connect_dots_parameter(int(raw_cd), prefix)

                tk = settings.ROI_DATE_THRESHOLD if prefix == 'date' else settings.ROI_LABELS_THRESHOLD
                raw_t = r.get(tk)
                if raw_t:
                    save_threshold_parameter(int(raw_t), prefix)

            exp_raw = r.get("expected_values")
            if exp_raw:
                save_expected_values(json.loads(exp_raw))

            print("[Shutdown] Saved all parameter settings.")
        except Exception as e:
            print(f"[Shutdown] Warning: Could not save settings: {e}")
        
        # Stop App Threads
        cam_thread.stop()
        proc_thread.stop()
        
        # Stop PLC
        if plc_comm:
            plc_comm.disconnect_plc()
        
        # Stop Infrastructure
        redis_manager.stop()
        print("[Shutdown] Clean exit complete.")

    # 6. Global Shutdown Request Monitoring (via QTimer)
    from PySide6.QtCore import QTimer
    shutdown_timer = QTimer()
    def check_shutdown():
        try:
            if r.get(settings.SHUTDOWN_REQUEST_KEY) == b"true":
                app_controller.app.quit()
        except Exception:
            pass
    shutdown_timer.timeout.connect(check_shutdown)
    shutdown_timer.start(1000)

    # Signal Handling (for CLI interrupts)
    def signal_handler(sig, frame):
        app_controller.app.quit()

    signal.signal(signal.SIGINT, signal_handler)

    # Run UI Loop
    exit_code = app_controller.run()
    
    # Post-UI Shutdown
    shutdown_flow()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
