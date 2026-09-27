import cv2
import numpy as np
from typing import List, Tuple, Optional
from core.image_processing import deskew_image

def scan_barcode_profile(binary_image: np.ndarray) -> List[Tuple[bool, int]]:
    """Scan simple barcode profile from a binary image using vectorized NumPy logic."""
    h, w = binary_image.shape

    # Count dark pixels per column
    col_sum = np.sum(binary_image == 0, axis=0)

    # Column is a bar if enough dark pixels
    threshold = h * 0.5
    profile_bin = (col_sum > threshold).astype(np.int8)  # 1 = bar, 0 = space

    # Vectorized Run-Length Encoding
    # Find indices where values change
    changes = np.diff(profile_bin, prepend=1 - profile_bin[0], append=1 - profile_bin[-1])
    change_indices = np.where(changes != 0)[0]
    
    # Calculate widths and corresponding values (bar/space)
    widths = np.diff(change_indices)
    values = profile_bin[change_indices[:-1]]
    
    # Convert to expected format: List[Tuple[is_bar, width]]
    runs = [(val == 1, int(width)) for val, width in zip(values, widths)]

    # Remove edge bars (quiet zone issues) if they are bars
    if runs and runs[0][0]:
        runs = runs[1:]
    if runs and runs[-1][0]:
        runs = runs[:-1]

    return runs

def classify_bars(bar_widths: List[int]) -> List[str]:
    """Classify bars into 'wide' or 'narrow'."""
    widths = np.array(bar_widths, dtype=np.float32)

    # Normalize relative to smallest bar
    if len(widths) > 0 and np.min(widths) > 0:
        widths /= np.min(widths)

    # Quantize to remove noise
    widths = np.round(widths)

    # Classify
    return ['wide' if w > 1.5 else 'narrow' for w in widths]

def decode_pharmacode_bars(bar_types: List[str]) -> int:
    """Decode pharmacode value from list of bar types."""
    value = 0
    for bar in bar_types:
        value *= 2
        value += 1 if bar == 'narrow' else 2
    return value

def _decode_bars_from_img(img: np.ndarray) -> Optional[str]:
    """Helper to upscale, binarize with multi-threshold fallback, and decode pharmacode."""
    if img is None or img.size == 0:
        return None
    scale = 3
    upscaled = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY) if len(upscaled.shape) == 3 else upscaled
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # Multi-threshold: Auto Otsu first, then fixed candidate thresholds
    for th in [0, 110, 130, 90, 150]:
        if th == 0:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            _, binary = cv2.threshold(gray, th, 255, cv2.THRESH_BINARY)

        # Ensure bars are black
        if np.mean(binary) < 127:
            binary = 255 - binary

        binary = cv2.medianBlur(binary, 3)
        runs = scan_barcode_profile(binary)
        bar_widths = [width for (is_bar, width) in runs if is_bar]

        if 3 <= len(bar_widths) <= 20:
            bar_types = classify_bars(bar_widths)
            val = decode_pharmacode_bars(bar_types)
            return str(val)

    return None

def read_pharma_code(cropped_image: np.ndarray, debug_prefix: str = "pharma") -> Tuple[Optional[str], float]:
    """Read pharmacode from a cropped image, returning the string value and deskew angle."""
    if cropped_image is None or cropped_image.size == 0:
        return None, 0.0

    # 1. Try with gentle deskew
    deskewed, angle = deskew_image(cropped_image)
    val = _decode_bars_from_img(deskewed)
    if val:
        return val, angle

    # 2. Fallback: try raw crop directly if deskew did not produce valid bars
    if angle != 0.0:
        val_raw = _decode_bars_from_img(cropped_image)
        if val_raw:
            return val_raw, 0.0

    return None, angle
