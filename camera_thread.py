import time
import cv2
import redis
import json
from datetime import datetime
from threading import Thread
from core.config import settings
from core.user_settings import load_camera_exposure
from services.camera_service import CameraService

class CameraThread(Thread):
    def __init__(self, camera_service: CameraService):
        super().__init__()
        self.camera_service = camera_service
        self.redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB
        )
        self.running = False
        self.led_until = 0  # Timestamp when LED should turn OFF
        self._current_exposure = None  # Track applied exposure to avoid redundant writes

    def _get_initial_exposure(self) -> float:
        exposure_raw = self.redis_client.get(settings.CAMERA_EXPOSURE_KEY)
        if exposure_raw:
            try:
                return float(exposure_raw)
            except (ValueError, TypeError):
                pass
        return load_camera_exposure()

    def run(self):
        self.running = True
        initial_exposure = self._get_initial_exposure()
        # Try to connect to camera
        success, msg = self.camera_service.connect(exposure_us=int(initial_exposure))
        print(f"[CameraThread] Connection status: {success}, Message: {msg}")
        
        if not success:
            print(f"[CameraThread] CRITICAL ERROR: Could not connect to camera.")
            self.running = False
            return

        self._current_exposure = initial_exposure

        # Grab initial frame on startup
        print("[CameraThread] Grabbing initial startup frame...")
        initial_frame = self.camera_service.grab_software()
        if initial_frame:
            print("[CameraThread] Startup frame captured.")
            self._push_to_redis(initial_frame)
        else:
            print("[CameraThread] WARNING: Could not capture startup frame.")

        print("[CameraThread] Entering main loop (Listening for triggers)...")
        while self.running:
            try:
                # 1. Check for manual software trigger request
                manual_trigger = self.redis_client.get(settings.TRIGGER_REQUEST_KEY)
                if manual_trigger and manual_trigger.decode() == "true":
                    self.redis_client.set(settings.TRIGGER_REQUEST_KEY, "false")
                    print("[CameraThread] MANUAl TRIGGER SIGNAL DETECTED")
                    frame_obj = self.camera_service.grab_software(timeout_ms=1000)
                    if frame_obj:
                        print("[CameraThread] Manual frame pushed to Redis.")
                        self._push_to_redis(frame_obj)
                    else:
                        print("[CameraThread] Manual frame grab returned None.")
                    continue

                # 2. Hardware Trigger (Listen ALWAYS for live feed)
                frame_obj = self.camera_service.grab(timeout_ms=200)
                if frame_obj:
                    print(f"[CameraThread] HARDWARE TRIGGER CAPTURE @ {frame_obj.timestamp}")
                    self._push_to_redis(frame_obj)

                # 3. Check for LED Pulse Trigger
                led_trigger = self.redis_client.get(settings.ERROR_LED_TRIGGER_KEY)
                if led_trigger and led_trigger.decode() == "true":
                    self.redis_client.set(settings.ERROR_LED_TRIGGER_KEY, "false")
                    print("[CameraThread] ERROR LED PULSE TRIGGERED")
                    self.camera_service.set_output(True)
                    self.led_until = time.time() + 0.2  # 200ms pulse
                
                # 4. Handle active LED timeout
                if self.led_until > 0 and time.time() > self.led_until:
                    self.camera_service.set_output(False)
                    self.led_until = 0

                # 5. Check for exposure change request
                exposure_raw = self.redis_client.get(settings.CAMERA_EXPOSURE_KEY)
                if exposure_raw:
                    try:
                        requested_exposure = float(exposure_raw)
                        if requested_exposure != self._current_exposure:
                            self.camera_service.set_exposure(requested_exposure)
                            self._current_exposure = requested_exposure
                    except (ValueError, TypeError):
                        pass

                time.sleep(0.01)
            except Exception as e:
                print(f"[CameraThread] loop error: {e}")
                time.sleep(1)

    def _push_to_redis(self, frame_obj):
        """Helper to encode and push frame to Redis."""
        try:
            # lossless BMP: JPEG blurs the small dot-matrix print and costs more time to encode/decode
            ret, buffer = cv2.imencode('.bmp', frame_obj.image_data)
            if ret:
                frame_bytes = buffer.tobytes()
                payload = {
                    "timestamp": frame_obj.timestamp.isoformat(),
                    "frame_data": frame_bytes.hex()
                }
                data_json = json.dumps(payload)
                self.redis_client.lpush(settings.RAW_FRAMES_KEY, data_json)
                # only the newest frame is ever read: keep a few (BMP frames are large)
                self.redis_client.ltrim(settings.RAW_FRAMES_KEY, 0, 2)
                # print(f"[CameraThread] Pushed frame to Redis. (Key: {settings.RAW_FRAMES_KEY})")
            else:
                print("[CameraThread] cv2.imencode failed!")
        except Exception as e:
            print(f"[CameraThread] _push_to_redis error: {e}")

    def stop(self):
        self.running = False
        self.camera_service.disconnect()
