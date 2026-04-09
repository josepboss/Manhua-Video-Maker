import requests
import os
import uuid
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": ""
}

AUTO_SELECTORS = [
    ".reading-content img",
    ".chapter-content img",
    ".page-break img",
    ".wp-manga-chapter-img",
    "#readerarea img",
    ".reader-area img",
    "div.text-left img",
    ".container-chapter-reader img",
]


def fetch_chapter(url: str, selector: str = "") -> dict:
    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        headers = {**HEADERS, "Referer": base_url}

        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "html.parser")
        images = []

        if selector:
            images = _extract_images(soup, selector, base_url)

        if not images:
            for auto_sel in AUTO_SELECTORS:
                images = _extract_images(soup, auto_sel, base_url)
                if len(images) >= 3:
                    logger.info(f"Auto-detected selector: {auto_sel}")
                    break

        if not images:
            return {"success": False, "error": "No images found. Try providing a CSS selector manually."}

        job_id = str(uuid.uuid4())[:8]
        save_dir = os.path.join("app", "uploads", job_id)
        os.makedirs(save_dir, exist_ok=True)

        downloaded = []
        for i, img_url in enumerate(images):
            try:
                img_res = requests.get(
                    img_url,
                    headers={**headers, "Referer": url},
                    timeout=15
                )
                img_res.raise_for_status()

                content_type = img_res.headers.get("Content-Type", "")
                ext = "png" if "png" in content_type or "png" in img_url.lower() else "jpg"
                filename = f"{str(i + 1).zfill(3)}.{ext}"
                filepath = os.path.join(save_dir, filename)

                with open(filepath, "wb") as f:
                    f.write(img_res.content)

                downloaded.append(filepath)
                logger.info(f"Downloaded {filename} from {img_url}")

            except Exception as e:
                logger.warning(f"Failed to download image {i + 1}: {e}")
                continue

        if not downloaded:
            return {"success": False, "error": "Images found but failed to download. Site may require authentication."}

        return {
            "success": True,
            "job_id": job_id,
            "image_count": len(downloaded),
            "image_paths": downloaded
        }

    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Failed to fetch page: {str(e)}"}
    except Exception as e:
        logger.error(f"Scraper error: {e}")
        return {"success": False, "error": str(e)}


def _extract_images(soup, selector: str, base_url: str) -> list:
    imgs = soup.select(selector)
    urls = []
    for img in imgs:
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("data-original")
        )
        if src:
            src = src.strip()
            if src.startswith("http"):
                urls.append(src)
            elif src:
                urls.append(urljoin(base_url, src))
    return urls
