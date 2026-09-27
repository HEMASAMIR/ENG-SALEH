import sys
import time
import subprocess
import cv2
import numpy as np
from typing import Optional, List, Tuple
from datetime import datetime

from core.camera_logic import CameraInterface
from models.camera_frame import CameraFrame

# Hardware dependency
try:
    import gxipy as gx
    GX_OK = True
except Exception:
    GX_OK = False

def _force_release_sdk():
    """Force the Daheng SDK to fully close and reinitialize."""
    if not GX_OK:
        return
    try:
        from gxipy.DeviceManager import DeviceManager as _DM
        from gxipy.gxwrapper import gx_close_lib
        
        # Reset DeviceManager singleton
        if hasattr(_DM, '_DeviceManager__instance_num'):
            _DM._DeviceManager__instance_num = 0
            
        import ctypes
        gx_close_lib()
        print("[CameraService] SDK Resources Force-Released.")
    except Exception as e:
        print(f"[CameraService] SDK Release Warning: {e}")
    time.sleep(1.0)

def _kill_galaxy_viewer():
    """Kill GxViewer / GalaxyViewer processes and ensure no ghost handles remain."""
    for proc_name in ["GxViewer", "GalaxyViewer", "GxCapture", "GxRectifyTool"]:
        try:
            subprocess.run(["taskkill", "/F", "/IM", f"{proc_name}.exe"], capture_output=True, timeout=2)
        except Exception: pass
    time.sleep(1.0) # Increased delay to allow OS to release handles

class CameraService(CameraInterface):
    """Clean service facade for Daheng Camera using hardware triggers."""
    def __init__(self):
        self.dm = None
        self.cam = None
        self.st = None
        self.ok = False
        self._trigger_line = "Line0"

    def scan(self) -> List[str]:
        if not GX_OK:
            return []
        try:
            dm = gx.DeviceManager()
            n, lst = dm.update_device_list()
            return [f"{d['model_name']}  SN:{d['sn']}" for d in lst] if n else []
        except Exception:
            return []

    def connect(self, index: int = 0, trigger_line: str = "Line0",
                exposure_us: int = 10000, gain_db: float = 0.0) -> Tuple[bool, str]:
        if not GX_OK:
            return False, "gxipy library not installed."

        self._trigger_line = trigger_line
        self.disconnect()
        _kill_galaxy_viewer()

        last_err = "Unknown error"
        for attempt in range(3):
            try:
                dm = gx.DeviceManager()
                self.dm = dm
                n, lst = dm.update_device_list()
                if n == 0:
                    return False, "No Daheng camera connected."
                
                target_idx = min(index, n - 1)
                d_info = lst[target_idx]
                device_sn = d_info.get('sn', '')
                acc_status = d_info.get('access_status', 0)
                
                if acc_status in [2, 4]:
                    return False, "Camera is held by another process."

                cam_obj = None
                try:
                    cam_obj = dm.open_device_by_sn(device_sn, gx.GxAccessMode.CONTROL)
                except Exception as e1:
                    last_err = str(e1)
                    try:
                        cam_obj = dm.open_device_by_index(target_idx + 1, gx.GxAccessMode.CONTROL)
                    except Exception as e2:
                        last_err = str(e2)

                if cam_obj is None:
                    if attempt < 2:
                        time.sleep(1.0)
                        continue
                    return False, f"Failed to open camera: {last_err}"

                self.cam = cam_obj

                # Hardware Trigger Mode
                self.cam.TriggerMode.set(gx.GxSwitchEntry.ON)
                line_map = {
                    "Line0": gx.GxTriggerSourceEntry.LINE0,
                    "Line1": gx.GxTriggerSourceEntry.LINE1,
                    "Line2": gx.GxTriggerSourceEntry.LINE2,
                    "Line3": gx.GxTriggerSourceEntry.LINE3,
                }
                self.cam.TriggerSource.set(
                    line_map.get(trigger_line, gx.GxTriggerSourceEntry.LINE0))
                self.cam.TriggerActivation.set(gx.GxTriggerActivationEntry.RISINGEDGE)

                # Digital Output (Line1 for Reject)
                try:
                    self.cam.LineSelector.set(gx.GxLineSelectorEntry.LINE1)
                    self.cam.LineMode.set(gx.GxLineModeEntry.OUTPUT)
                    self.cam.LineSource.set(gx.GxLineSourceEntry.USER_OUTPUT0)
                    self.cam.UserOutputSelector.set(gx.GxUserOutputSelectorEntry.USER_OUTPUT0)
                    self.cam.UserOutputValue.set(False)
                except Exception: 
                    pass

                # Exposure & Gain
                if self.cam.ExposureTime.is_writable():
                    self.cam.ExposureTime.set(float(exposure_us))
                if self.cam.Gain.is_writable():
                    self.cam.Gain.set(float(gain_db))

                # Stream
                self.st = self.cam.data_stream[0]
                self.cam.stream_on()
                self.ok = True
                model = self.cam.DeviceModelName.get()
                return True, f"Connected to {model}"

            except Exception as e:
                last_err = str(e)
                if self.cam:
                    try: self.cam.close_device()
                    except Exception: pass
                self.cam = None
                self.st = None
                self.ok = False
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                break

        return False, f"Failed connection: {last_err}"

    def grab(self, timeout_ms: int = 1000) -> Optional[CameraFrame]:
        """Wait for a trigger (hardware or software), then grab one frame."""
        if not self.ok or self.st is None: 
            print("[CameraService] grab: NOT OK or st is None")
            return None
        try:
            raw = self.st.get_image(timeout_ms)
            if raw is None:
                # This is normal for a timeout during trigger waiting
                return None
                
            status = raw.get_status()
            if status != gx.GxFrameStatusList.SUCCESS:
                print(f"[CameraService] grab: Frame status failed: {status}")
                return None
            
            rgb = raw.convert("RGB")
            arr = rgb.get_numpy_array()
            if arr is None:
                print("[CameraService] grab: numpy array is None")
                return None
                
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            print(f"[CameraService] grab: SUCCESS ({bgr.shape[1]}x{bgr.shape[0]})")
            return CameraFrame(image_data=bgr, timestamp=datetime.now())
        except Exception as e:
            print(f"[CameraService] grab: Exception: {e}")
            return None

    def grab_software(self, timeout_ms: int = 2000) -> Optional[CameraFrame]:
        """Force a software trigger capture."""
        if not self.ok or self.cam is None: 
            print("[CameraService] grab_software: NOT OK or cam is None")
            return None
        
        frame = None
        old_source = None
        try:
            # 1. Save current source (returns a tuple like (1, 'Line0'))
            old_raw = self.cam.TriggerSource.get()
            old_source = old_raw[0] if isinstance(old_raw, tuple) else old_raw
            print(f"[CameraService] grab_software: Switching to SOFTWARE from source index {old_source}")
            
            # 2. Switch to Software
            self.cam.TriggerSource.set(gx.GxTriggerSourceEntry.SOFTWARE)
            
            # 3. Send command
            print("[CameraService] grab_software: Sending TriggerSoftware command...")
            self.cam.TriggerSoftware.send_command()
            
            # 4. Grab the resulting frame
            frame = self.grab(timeout_ms)
            
        except Exception as e:
            print(f"[CameraService] grab_software Exception during capture: {e}")
        
        # 5. Restore original source (Always try to restore)
        if old_source is not None:
            try:
                self.cam.TriggerSource.set(old_source)
            except Exception as e:
                print(f"[CameraService] grab_software: Could not restore source: {e}")
            
        if frame:
            print("[CameraService] grab_software: Capture SUCCESS")
        else:
            print("[CameraService] grab_software: Capture FAILED (timeout/no frame)")
            
        return frame

    def set_exposure(self, exposure_us: float) -> bool:
        """Change the camera exposure time (in microseconds) at runtime."""
        if not self.ok or self.cam is None:
            return False
        try:
            if self.cam.ExposureTime.is_writable():
                self.cam.ExposureTime.set(float(exposure_us))
                print(f"[CameraService] Exposure set to {exposure_us} µs")
                return True
            else:
                print("[CameraService] ExposureTime is not writable.")
                return False
        except Exception as e:
            print(f"[CameraService] set_exposure error: {e}")
            return False

    def set_output(self, value: bool):
        """Toggle the Digital Output (Line1 / UserOutput0)."""
        if not self.ok or self.cam is None:
            return
        try:
            # We already set LineSelector to Line1 and LineSource to UserOutput0 in connect()
            self.cam.UserOutputValue.set(value)
        except Exception as e:
            print(f"[CameraService] set_output Error: {e}")

    def disconnect(self):
        self.ok = False
        if self.cam:
            try: self.cam.stream_off()
            except Exception: pass
        if self.st:
            try: self.st.close()
            except Exception: pass
        if self.cam:
            try: self.cam.close_device()
            except Exception: pass
        self.cam = None
        self.st  = None
        self.dm  = None
        _force_release_sdk()
