"""
lightning-ocr · Image preprocessing
Deskew, enhance, denoise — improves OCR accuracy on real-world scans.
"""
from __future__ import annotations

import io
import logging
from typing import Tuple

log = logging.getLogger("lightning_ocr.preprocess")


def deskew_image(image_bytes: bytes, max_angle: float = 15.0) -> bytes:
    """
    Deskew a scanned image. Returns corrected PNG bytes.
    Uses projection profile method — fast, no ML required.
    """
    try:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        arr = np.array(img)

        # Binarize
        threshold = 128
        binary = (arr < threshold).astype(np.uint8)

        # Try angles from -max_angle to +max_angle in 0.5 degree steps
        best_angle = 0.0
        best_score = 0.0
        angles = [i * 0.5 for i in range(int(-max_angle * 2), int(max_angle * 2) + 1)]

        for angle in angles:
            rotated = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            rot_arr = np.array(rotated)
            rot_binary = (rot_arr < threshold).astype(np.uint8)

            # Score = sum of squared row sums (peaks = good alignment)
            row_sums = rot_binary.sum(axis=1).astype(float)
            score = (row_sums ** 2).sum()

            if score > best_score:
                best_score = score
                best_angle = angle

        if abs(best_angle) < 0.5:
            return image_bytes  # Already straight

        # Apply deskew
        corrected = img.rotate(best_angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
        buf = io.BytesIO()
        corrected.save(buf, format="PNG")
        log.info("Deskewed image by %.1f degrees", best_angle)
        return buf.getvalue()
    except ImportError:
        log.warning("numpy/Pillow not available for deskew, returning original")
        return image_bytes
    except Exception as exc:
        log.warning("Deskew failed: %s, returning original", exc)
        return image_bytes


def enhance_image(image_bytes: bytes) -> bytes:
    """
    Enhance image for better OCR: contrast stretch, sharpen, denoise.
    Returns enhanced PNG bytes.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # Auto contrast
        from PIL import ImageOps
        img = ImageOps.autocontrast(img, cutoff=1)

        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)

        # Denoise (median filter) — only for large images where text strokes
        # are thick enough to survive; small text gets destroyed otherwise.
        w, h = img.size
        if w >= 600 and h >= 300:
            img = img.filter(ImageFilter.MedianFilter(size=3))

        # Increase contrast slightly
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        log.warning("Image enhancement failed: %s, returning original", exc)
        return image_bytes


def binarize_image(image_bytes: bytes, method: str = "otsu") -> bytes:
    """
    Convert to binary (black/white) for cleaner OCR.
    Methods: 'otsu' (auto threshold), 'adaptive' (local threshold).
    """
    try:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        arr = np.array(img)

        if method == "adaptive":
            # Local adaptive thresholding
            from scipy.ndimage import uniform_filter
            local_mean = uniform_filter(arr.astype(float), size=25)
            binary = ((arr > local_mean - 10) * 255).astype(np.uint8)
        else:
            # Otsu's method (simple version)
            hist, _ = np.histogram(arr.ravel(), bins=256, range=(0, 256))
            total = arr.size
            sum_total = np.dot(np.arange(256), hist)
            sum_bg = 0.0
            weight_bg = 0
            max_variance = 0.0
            threshold = 0

            for i in range(256):
                weight_bg += hist[i]
                if weight_bg == 0:
                    continue
                weight_fg = total - weight_bg
                if weight_fg == 0:
                    break
                sum_bg += i * hist[i]
                mean_bg = sum_bg / weight_bg
                mean_fg = (sum_total - sum_bg) / weight_fg
                variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
                if variance > max_variance:
                    max_variance = variance
                    threshold = i

            binary = ((arr > threshold) * 255).astype(np.uint8)

        result = Image.fromarray(binary, mode="L")
        buf = io.BytesIO()
        result.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        log.warning("numpy not available for binarization, returning original")
        return image_bytes
    except Exception as exc:
        log.warning("Binarization failed: %s, returning original", exc)
        return image_bytes


def auto_preprocess(image_bytes: bytes, enhance: bool = True, deskew: bool = True) -> bytes:
    """
    Apply automatic preprocessing pipeline.
    Returns optimized bytes for OCR.
    """
    result = image_bytes
    if deskew:
        result = deskew_image(result)
    if enhance:
        result = enhance_image(result)
    return result
