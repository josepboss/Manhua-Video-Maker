import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ocr_instances = {}

LANG_MAP = {
    "en": "en",
    "ar": "arabic",
    "ch": "ch",
    "fr": "french"
}


def get_ocr_instance(lang: str = "en"):
    from paddleocr import PaddleOCR
    paddle_lang = LANG_MAP.get(lang, "en")
    if paddle_lang not in _ocr_instances:
        logger.info(f"Loading PaddleOCR model for language: {paddle_lang}")
        _ocr_instances[paddle_lang] = PaddleOCR(
            use_angle_cls=True,
            lang=paddle_lang,
            show_log=False
        )
    return _ocr_instances[paddle_lang]


def extract_text(image_path: str, lang: str = "en") -> str:
    try:
        ocr = get_ocr_instance(lang)
        result = ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            logger.info(f"Panel OCR raw result: '' (no result) [{Path(image_path).name}]")
            return ""

        lines = []
        for line in result[0]:
            if line and len(line) >= 2:
                text_info = line[1]
                if text_info and len(text_info) >= 2:
                    text = text_info[0]
                    confidence = text_info[1]
                    if confidence > 0.3 and len(text.strip()) > 1:
                        lines.append(text.strip())

        raw = " ".join(lines)
        cleaned = clean_text(raw)
        logger.info(f"Panel OCR raw result: '{cleaned[:100]}' [{Path(image_path).name}]")
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
