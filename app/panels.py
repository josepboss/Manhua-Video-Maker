import cv2
import numpy as np
from pathlib import Path
import logging
from typing import List

logger = logging.getLogger(__name__)


def convert_pdf_to_images(pdf_path: str, output_dir: str) -> List[str]:
    from pdf2image import convert_from_path
    images = convert_from_path(pdf_path, dpi=150)
    image_paths = []
    for i, img in enumerate(images):
        out_path = Path(output_dir) / f"page_{i:04d}.png"
        img.save(str(out_path), "PNG")
        image_paths.append(str(out_path))
    return image_paths


def detect_panels(image_path: str, output_dir: str, job_id: str, page_idx: int) -> List[str]:
    img = cv2.imread(image_path)
    if img is None:
        logger.error(f"Could not read image: {image_path}")
        return [image_path]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(binary, kernel, iterations=2)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    height, width = img.shape[:2]
    min_area = (height * width) * 0.02

    panels = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area >= min_area and w > 50 and h > 50:
            panels.append((x, y, w, h))

    panels = merge_overlapping_panels(panels)

    if len(panels) < 2:
        logger.info(f"Page {page_idx}: fewer than 2 panels detected, using whole page")
        out_path = Path(output_dir) / f"panel_{page_idx:04d}_000.png"
        cv2.imwrite(str(out_path), img)
        return [str(out_path)]

    panels.sort(key=lambda b: (b[1] // 50, b[0]))

    panel_paths = []
    for i, (x, y, w, h) in enumerate(panels):
        margin = 5
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(width, x + w + margin)
        y2 = min(height, y + h + margin)
        panel_img = img[y1:y2, x1:x2]
        out_path = Path(output_dir) / f"panel_{page_idx:04d}_{i:03d}.png"
        cv2.imwrite(str(out_path), panel_img)
        panel_paths.append(str(out_path))

    return panel_paths


def merge_overlapping_panels(panels: List[tuple]) -> List[tuple]:
    if not panels:
        return panels
    merged = True
    result = list(panels)
    while merged:
        merged = False
        new_result = []
        used = [False] * len(result)
        for i in range(len(result)):
            if used[i]:
                continue
            x1, y1, w1, h1 = result[i]
            box1 = (x1, y1, x1 + w1, y1 + h1)
            for j in range(i + 1, len(result)):
                if used[j]:
                    continue
                x2, y2, w2, h2 = result[j]
                box2 = (x2, y2, x2 + w2, y2 + h2)
                if boxes_overlap(box1, box2):
                    nx = min(box1[0], box2[0])
                    ny = min(box1[1], box2[1])
                    nx2 = max(box1[2], box2[2])
                    ny2 = max(box1[3], box2[3])
                    box1 = (nx, ny, nx2, ny2)
                    used[j] = True
                    merged = True
            new_result.append((box1[0], box1[1], box1[2] - box1[0], box1[3] - box1[1]))
            used[i] = True
        result = new_result
    return result


def boxes_overlap(b1: tuple, b2: tuple) -> bool:
    return not (b1[2] <= b2[0] or b2[2] <= b1[0] or b1[3] <= b2[1] or b2[3] <= b1[1])


def process_images_to_panels(image_paths: List[str], panels_dir: str, job_id: str) -> List[str]:
    Path(panels_dir).mkdir(parents=True, exist_ok=True)
    all_panels = []
    for idx, image_path in enumerate(image_paths):
        try:
            panels = detect_panels(image_path, panels_dir, job_id, idx)
            all_panels.extend(panels)
        except Exception as e:
            logger.error(f"Panel detection failed for {image_path}: {e}")
            all_panels.append(image_path)
    return all_panels
