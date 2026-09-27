import cv2
import numpy as np



def deskew_image(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Deskew the given image using fast downsampled angle detection."""
    (h, w) = image.shape[:2]
    if h == 0 or w == 0:
        return image, 0.0

    # 1. Downsample for fast angle detection
    target_h = 100
    scale = target_h / h
    small = cv2.resize(image, (int(w * scale), target_h), interpolation=cv2.INTER_NEAREST)
    
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = 255 - bw  # invert if text/bars are dark

    # Use (x, y) coordinates for OpenCV minAreaRect
    coords = np.column_stack(np.where(bw > 0)[::-1])
    if len(coords) == 0:
        return image, 0.0
    
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    
    # Normalize angle for OpenCV 4.5+ (angle is in [0, 90])
    if angle > 45.0:
        angle = angle - 90.0
    elif angle < -45.0:
        angle = angle + 90.0

    # Deskew is strictly for subtle tilt correction (e.g. 0.5 to 30 degrees).
    # Ignore negligible angles (<0.5 deg) to prevent blur, and ignore extreme angles (>30 deg).
    if abs(angle) < 0.5 or abs(angle) > 30.0:
        return image, 0.0

    # 2. Apply rotation to FULL-SIZE image
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    deskewed = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    return deskewed, angle

def preprocess_img(
    img: np.ndarray,
    brightness: int = 0,
    gamma: float = 1.0,
    local_contrast: float = 1.6,
    smoothing: int = 0,
    connect_dots: int = 0,
    threshold: int = 0,
    gray_offset: int = None,
    gray_thresh: int = None
) -> np.ndarray:
    """
    Preprocess the image for industrial OCR.
    Pipeline stages applied before reading:
      1. Deskew
      2. Grayscale conversion
      3. Brightness adjustment (-100 to +100, default 0)
      4. Gamma correction (0.1 to 3.0, default 1.0)
      5. Quiet-zone margin padding
      6. Local contrast enhancement (CLAHE clipLimit 0.0 to 10.0, default 1.6)
      7. Smart upscale
      8. Smoothing filter (Gaussian blur with odd kernel size, default 0)
      9. Threshold binarization (0 = Auto Otsu, 1..254 = Manual, 255 = Off / Grayscale)
      10. Connect dots (morphological closing on dot-matrix inkjet text, default 0)
    """
    if img is None or img.size == 0:
        return img

    # Backwards compatibility
    if gray_offset is not None and brightness == 0:
        brightness = gray_offset
    if gray_thresh is not None and threshold == 0:
        threshold = gray_thresh

    # 1. Gentle deskew only if tilt is noticeable (avoid micro-rotation blur on nearly straight text)
    try:
        deskewed, angle = deskew_image(img)
        if 1.5 <= abs(angle) < 15.0:
            img = deskewed
    except Exception:
        pass

    # 2. Grayscale conversion
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # 3. Brightness adjustment (-100 to +100)
    if brightness != 0:
        gray = cv2.convertScaleAbs(gray, alpha=1.0, beta=brightness)

    # 4. Gamma correction (0.1 to 3.0)
    if gamma > 0 and abs(gamma - 1.0) > 0.01:
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)], dtype=np.uint8)
        gray = cv2.LUT(gray, table)

    # 5. Robust background detection from border pixels (avoids false-inverting light packaging)
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    border_median = float(np.median(border))

    # Only invert if background is genuinely dark (e.g. dark ampoule / black blister pack)
    if border_median < 65 and np.mean(gray) < 65:
        gray = 255 - gray
        border_median = 255 - border_median

    bg_val = int(border_median)

    # 6. Quiet-zone margin padding (15px top/bottom, 20px left/right)
    pad_y, pad_x = 15, 20
    padded = cv2.copyMakeBorder(gray, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=bg_val)

    # 7. Local contrast enhancement (CLAHE)
    if local_contrast and local_contrast > 0:
        clahe = cv2.createCLAHE(clipLimit=float(local_contrast), tileGridSize=(8, 8))
        enhanced = clahe.apply(padded)
    else:
        enhanced = padded

    # 8. Smart upscale: ensure text height is optimal (>=250px) for Tesseract LSTM
    ph, pw = enhanced.shape[:2]
    if ph < 250 or pw < 400:
        scale = max(2.5, 250.0 / ph) if ph > 0 else 2.0
        scaled = cv2.resize(enhanced, (int(pw * scale), int(ph * scale)), interpolation=cv2.INTER_CUBIC)
    else:
        scaled = enhanced

    # 9. Smoothing filter
    if smoothing > 0:
        k = smoothing if smoothing % 2 == 1 else smoothing + 1
        scaled = cv2.GaussianBlur(scaled, (k, k), 0)

    # 10. Threshold parameter
    if threshold == 255:
        bw = scaled
    elif threshold > 0:
        _, bw = cv2.threshold(scaled, threshold, 255, cv2.THRESH_BINARY)
    else:
        # Auto mode: Background-aware Otsu binarization
        # On white pharmaceutical packaging (bg_val > 150), standard Otsu is skewed high (160-180),
        # eroding delicate strokes (e.g. inner 'V' of 'M', loop of '0').
        # We adjust the threshold downward to protect fine character geometry.
        otsu_val, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if bg_val > 150 and otsu_val > 125:
            target_th = int(min(otsu_val * 0.72, 115))
            _, bw = cv2.threshold(scaled, target_th, 255, cv2.THRESH_BINARY)
        else:
            _, bw = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 11. Connect dots (Morphological Closing on dot-matrix inkjet characters)
    if connect_dots > 0 and threshold != 255:
        inv_bw = 255 - bw
        k_cd = connect_dots if connect_dots % 2 == 1 else connect_dots + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_cd, k_cd))
        closed = cv2.morphologyEx(inv_bw, cv2.MORPH_CLOSE, kernel)
        bw = 255 - closed

    return cv2.merge([bw, bw, bw])

