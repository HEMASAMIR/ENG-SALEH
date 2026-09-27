import json
import os
from pathlib import Path

from core.config import settings

DEFAULT_CAMERA_EXPOSURE_US = 10000.0
_SETTINGS_FILENAME = "user_settings.json"


def _settings_path() -> Path:
    """Store settings alongside the SQLite database file."""
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        if not db_path.is_absolute():
            db_path = Path(os.getcwd()) / db_path
        return db_path.parent / _SETTINGS_FILENAME
    return Path(os.getcwd()) / _SETTINGS_FILENAME


def load_user_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_user_settings(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_camera_exposure() -> float:
    data = load_user_settings()
    try:
        value = float(data.get("camera_exposure_us", DEFAULT_CAMERA_EXPOSURE_US))
        if 1 <= value <= 1_000_000:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_CAMERA_EXPOSURE_US


def save_camera_exposure(exposure_us: float) -> None:
    value = float(exposure_us)
    if not (1 <= value <= 1_000_000):
        raise ValueError("Exposure must be between 1 and 1,000,000 µs")
    data = load_user_settings()
    data["camera_exposure_us"] = value
    save_user_settings(data)


DEFAULT_LOCATOR_CONFIDENCE = 0.0


def load_locator_confidence() -> float:
    data = load_user_settings()
    try:
        value = float(data.get("locator_confidence", DEFAULT_LOCATOR_CONFIDENCE))
        if 0.0 <= value <= 1.0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_LOCATOR_CONFIDENCE


def save_locator_confidence(confidence: float) -> None:
    value = float(confidence)
    if not (0.0 <= value <= 1.0):
        raise ValueError("Locator confidence must be between 0.0 and 1.0")
    data = load_user_settings()
    data["locator_confidence"] = value
    save_user_settings(data)


DEFAULT_OCR_CONFIDENCE = 0.0


def load_ocr_confidence() -> float:
    data = load_user_settings()
    try:
        value = float(data.get("ocr_confidence", DEFAULT_OCR_CONFIDENCE))
        if 0.0 <= value <= 1.0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_OCR_CONFIDENCE


def save_ocr_confidence(confidence: float) -> None:
    value = float(confidence)
    if not (0.0 <= value <= 1.0):
        raise ValueError("OCR confidence must be between 0.0 and 1.0")
    data = load_user_settings()
    data["ocr_confidence"] = value
    save_user_settings(data)


DEFAULT_LABELS_CONFIDENCE = 0.0


def load_labels_confidence() -> float:
    data = load_user_settings()
    try:
        value = float(data.get("labels_confidence", DEFAULT_LABELS_CONFIDENCE))
        if 0.0 <= value <= 1.0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_LABELS_CONFIDENCE


def save_labels_confidence(confidence: float) -> None:
    value = float(confidence)
    if not (0.0 <= value <= 1.0):
        raise ValueError("Labels confidence must be between 0.0 and 1.0")
    data = load_user_settings()
    data["labels_confidence"] = value
    save_user_settings(data)


DEFAULT_GRAYSCALE_PARAMETER = 0
DEFAULT_BRIGHTNESS_PARAMETER = 0
DEFAULT_GAMMA_PARAMETER = 1.0
DEFAULT_LOCAL_CONTRAST_PARAMETER = 1.0
DEFAULT_SMOOTHING_PARAMETER = 1
DEFAULT_CONNECT_DOTS_PARAMETER = 2


def load_brightness_parameter(prefix: str = "date") -> int:
    data = load_user_settings()
    key = f"{prefix}_brightness"
    default_val = 5 if prefix == "labels" else 0
    try:
        if key in data:
            val = int(data[key])
        elif f"{prefix}_grayscale" in data:
            val = int(data[f"{prefix}_grayscale"])
        elif prefix == "date" and "grayscale_parameter" in data:
            val = int(data["grayscale_parameter"])
        else:
            val = default_val
        if -100 <= val <= 255:
            return val
    except (TypeError, ValueError):
        pass
    return default_val


def save_brightness_parameter(val: int, prefix: str = "date") -> None:
    value = int(val)
    if not (-100 <= value <= 255):
        raise ValueError("Brightness parameter must be between -100 and 255")
    data = load_user_settings()
    data[f"{prefix}_brightness"] = value
    data[f"{prefix}_grayscale"] = value
    if prefix == "date":
        data["grayscale_parameter"] = value
    save_user_settings(data)


def load_gamma_parameter(prefix: str = "date") -> float:
    data = load_user_settings()
    key = f"{prefix}_gamma"
    default_val = 0.72 if prefix == "labels" else 1.0
    try:
        val = float(data.get(key, default_val))
        if 0.1 <= val <= 5.0:
            return round(val, 2)
    except (TypeError, ValueError):
        pass
    return default_val


def save_gamma_parameter(val: float, prefix: str = "date") -> None:
    val = round(float(val), 2)
    if not (0.1 <= val <= 5.0):
        raise ValueError("Gamma parameter must be between 0.1 and 5.0")
    data = load_user_settings()
    data[f"{prefix}_gamma"] = val
    save_user_settings(data)


def load_local_contrast_parameter(prefix: str = "date") -> float:
    data = load_user_settings()
    key = f"{prefix}_local_contrast"
    default_val = 1.0 if prefix == "labels" else 2.0
    try:
        val = float(data.get(key, default_val))
        if 0.0 <= val <= 20.0:
            return round(val, 2)
    except (TypeError, ValueError):
        pass
    return default_val


def save_local_contrast_parameter(val: float, prefix: str = "date") -> None:
    val = round(float(val), 2)
    if not (0.0 <= val <= 20.0):
        raise ValueError("Local contrast parameter must be between 0.0 and 20.0")
    data = load_user_settings()
    data[f"{prefix}_local_contrast"] = val
    save_user_settings(data)


def load_smoothing_parameter(prefix: str = "date") -> int:
    data = load_user_settings()
    key = f"{prefix}_smoothing"
    default_val = 1
    try:
        val = int(data.get(key, default_val))
        if 0 <= val <= 31:
            return val
    except (TypeError, ValueError):
        pass
    return default_val


def save_smoothing_parameter(val: int, prefix: str = "date") -> None:
    val = int(val)
    if not (0 <= val <= 31):
        raise ValueError("Smoothing parameter must be between 0 and 31")
    data = load_user_settings()
    data[f"{prefix}_smoothing"] = val
    save_user_settings(data)


def load_connect_dots_parameter(prefix: str = "date") -> int:
    data = load_user_settings()
    key = f"{prefix}_connect_dots"
    default_val = 2 if prefix == "labels" else 3
    try:
        val = int(data.get(key, default_val))
        if 0 <= val <= 20:
            return val
    except (TypeError, ValueError):
        pass
    return default_val


def save_connect_dots_parameter(val: int, prefix: str = "date") -> None:
    val = int(val)
    if not (0 <= val <= 20):
        raise ValueError("Connect dots parameter must be between 0 and 20")
    data = load_user_settings()
    data[f"{prefix}_connect_dots"] = val
    save_user_settings(data)


def load_grayscale_parameter(prefix: str = "date") -> int:
    return load_brightness_parameter(prefix)


def save_grayscale_parameter(val: int, prefix: str = "date") -> None:
    save_brightness_parameter(val, prefix)


DEFAULT_THRESHOLD_PARAMETER = 0


def load_threshold_parameter(prefix: str = "date") -> int:
    data = load_user_settings()
    key = f"{prefix}_threshold"
    try:
        if key in data:
            val = int(data[key])
        elif prefix == "date" and "threshold_parameter" in data:
            val = int(data["threshold_parameter"])
        else:
            val = DEFAULT_THRESHOLD_PARAMETER
        if 0 <= val <= 255:
            return val
    except (TypeError, ValueError):
        pass
    return DEFAULT_THRESHOLD_PARAMETER


def save_threshold_parameter(val: int, prefix: str = "date") -> None:
    value = int(val)
    if not (0 <= value <= 255):
        raise ValueError("Threshold parameter must be between 0 and 255")
    data = load_user_settings()
    data[f"{prefix}_threshold"] = value
    if prefix == "date":
        data["threshold_parameter"] = value
    save_user_settings(data)


DEFAULT_CONTRAST_PARAMETER = 1.0


def load_contrast_parameter(prefix: str = "date") -> float:
    data = load_user_settings()
    key = f"{prefix}_contrast"
    if prefix == "labels":
        default_val = 0.5
    elif prefix == "date":
        default_val = 0.8
    elif prefix == "pharma":
        default_val = 3.0
    else:
        default_val = DEFAULT_CONTRAST_PARAMETER
    try:
        val = float(data.get(key, default_val))
        if 0.1 <= val <= 5.0:
            return round(val, 2)
    except (TypeError, ValueError):
        pass
    return default_val


def save_contrast_parameter(prefix_or_val, val_or_prefix=None) -> None:
    if isinstance(prefix_or_val, str):
        prefix = prefix_or_val
        val = float(val_or_prefix)
    else:
        val = float(prefix_or_val)
        prefix = str(val_or_prefix) if val_or_prefix is not None else "date"
    val = round(float(val), 2)
    if not (0.1 <= val <= 5.0):
        raise ValueError("Contrast parameter must be between 0.1 and 5.0")
    data = load_user_settings()
    data[f"{prefix}_contrast"] = val
    save_user_settings(data)


DEFAULT_ROTATION_PARAMETER = 0


def load_rotation_parameter(prefix: str = "date") -> int:
    data = load_user_settings()
    key = f"{prefix}_rotation"
    default_val = 90 if prefix in ("labels", "date") else 0
    try:
        val = int(data.get(key, default_val))
        if val in (0, 90, 180, 270):
            return val
    except (TypeError, ValueError):
        pass
    return default_val


def save_rotation_parameter(prefix_or_val, val_or_prefix=None) -> None:
    if isinstance(prefix_or_val, str):
        prefix = prefix_or_val
        val = int(val_or_prefix)
    else:
        val = int(prefix_or_val)
        prefix = str(val_or_prefix) if val_or_prefix is not None else "date"
    if val not in (0, 90, 180, 270):
        val = 0
    data = load_user_settings()
    data[f"{prefix}_rotation"] = val
    save_user_settings(data)


DEFAULT_EXPECTED_VALUES = {
    "LABELS": "LOT : MFG : EXP :",
    "LOT": "",
    "MFG": "",
    "EXP": "",
    "PHARMA": ""
}


def load_expected_values() -> dict:
    data = load_user_settings()
    exp = data.get("expected_values")
    if isinstance(exp, dict):
        res = dict(DEFAULT_EXPECTED_VALUES)
        res.update(exp)
        return res
    return dict(DEFAULT_EXPECTED_VALUES)


def save_expected_values(expected: dict) -> None:
    if not isinstance(expected, dict):
        return
    data = load_user_settings()
    data["expected_values"] = expected
    save_user_settings(data)


def load_all_user_settings() -> dict:
    return load_user_settings()


def save_all_user_settings(settings_dict: dict) -> None:
    if not isinstance(settings_dict, dict):
        return
    data = load_user_settings()
    data.update(settings_dict)
    save_user_settings(data)


DEFAULT_REJECT_ON_DELAY = 0


def load_reject_on_delay() -> int:
    data = load_user_settings()
    try:
        val = int(data.get("reject_on_delay", DEFAULT_REJECT_ON_DELAY))
        if 0 <= val <= 65535:
            return val
    except (TypeError, ValueError):
        pass
    return DEFAULT_REJECT_ON_DELAY


def save_reject_on_delay(val: int) -> None:
    value = int(val)
    if not (0 <= value <= 65535):
        raise ValueError("Reject on delay must be between 0 and 65535")
    data = load_user_settings()
    data["reject_on_delay"] = value
    save_user_settings(data)






