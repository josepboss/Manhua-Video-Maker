import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ocr_instance = None


def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    return _ocr_instance


def extract_text(image_path: str) -> str:
    try:
        ocr = get_ocr()
        result = ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return ""

        lines = []
        for line in result[0]:
            if line and len(line) >= 2:
                text_info = line[1]
                if text_info and len(text_info) >= 2:
                    text = text_info[0]
                    confidence = text_info[1]
                    if confidence > 0.5 and len(text.strip()) > 1:
                        lines.append(text.strip())

        raw = " ".join(lines)
        cleaned = clean_text(raw)
        return cleaned
    except Exception as e:
        logger.error(f"OCR failed for {image_path}: {e}")
        return ""


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s.,!?'\"-]", "", text)
    words = text.split()
    words = [w for w in words if len(w) > 1 or w.lower() in {"a", "i"}]
    return " ".join(words).strip()
