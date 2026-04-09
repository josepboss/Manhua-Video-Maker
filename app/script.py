import base64
import requests
import logging
import re
from typing import List, Tuple
from app import context

logger = logging.getLogger(__name__)

NARRATOR_SYSTEM = (
    "You are a narrator for manhua recap videos. Convert raw dialogue and text into "
    "third-person narrative storytelling. Never keep dialogue format. Never use quotes. "
    "Merge lines into coherent flowing sentences. Remove filler words and repetition. "
    "Be concise but dramatic. Always write in third person."
)

MERGE_SYSTEM = (
    "You are an expert video script editor. Take the panel narrations provided and merge "
    "them into one continuous, polished script. Add pacing: use short punchy sentences for "
    "action scenes and longer sentences for exposition. Target 700-1700 words (5-12 minutes "
    "read time). Return only the final script text, no headings or labels."
)

VISION_PROMPT = (
    "This is a manhua panel. Describe what is happening in one third-person narrative sentence."
)


def call_openrouter(system: str, user: str, api_key: str, model: str) -> Tuple[str, int]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://manhuarecap.local",
        "X-Title": "ManhuaRecap"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "max_tokens": 800
    }
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=60
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    tokens = data.get("usage", {}).get("total_tokens", len(text.split()) * 2)
    return text, tokens


def call_openrouter_vision(image_path: str, api_key: str, model: str) -> Tuple[str, int]:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://manhuarecap.local",
        "X-Title": "ManhuaRecap"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": NARRATOR_SYSTEM},
            {"role": "user", "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                },
                {
                    "type": "text",
                    "text": VISION_PROMPT
                }
            ]}
        ],
        "max_tokens": 800
    }
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=90
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    tokens = data.get("usage", {}).get("total_tokens", len(text.split()) * 2)
    return text, tokens


def generate_panel_narration(panel_text: str, api_key: str, model: str) -> Tuple[str, int]:
    context_str = context.get_context()
    user_prompt = (
        f"{context_str}\n\n" if context_str else ""
    ) + (
        f"Current panel text:\n{panel_text}\n\n"
        "Write a third-person narrative sentence for this panel only."
    )

    try:
        result, tokens = call_openrouter(NARRATOR_SYSTEM, user_prompt, api_key, model)
        context.update_context(result[:300])
        return result, tokens
    except Exception as e:
        logger.warning(f"Panel narration failed (retry 1): {e}")
        try:
            result, tokens = call_openrouter(NARRATOR_SYSTEM, user_prompt, api_key, model)
            context.update_context(result[:300])
            return result, tokens
        except Exception as e2:
            logger.error(f"Panel narration failed (retry 2), skipping: {e2}")
            return "", 0


def generate_script(
    panels: List[Tuple[str, str]],
    api_key: str,
    model: str,
    progress_callback=None
) -> Tuple[str, str, dict]:
    context.reset_context()
    narrations = []
    total_tokens = 0
    total_panels = len(panels)

    for i, (image_path, text) in enumerate(panels):
        if not text or not text.strip():
            logger.info(f"Panel {i} has no OCR text — using vision fallback")
            try:
                narration, tokens = call_openrouter_vision(image_path, api_key, model)
                total_tokens += tokens
                if narration:
                    context.update_context(narration[:300])
                    narrations.append(narration)
                    logger.info(f"Vision fallback panel {i}: '{narration[:80]}'")
            except Exception as e:
                logger.warning(f"Vision fallback failed for panel {i}: {e}")
        else:
            narration, tokens = generate_panel_narration(text, api_key, model)
            total_tokens += tokens
            if narration:
                narrations.append(narration)

        if progress_callback:
            pct = int((i + 1) / total_panels * 40)
            progress_callback(pct, f"Narrating panel {i+1}/{total_panels}...")

    if not narrations:
        raise ValueError("No narrations generated — check OCR output and API key")

    combined = "\n\n".join(narrations)
    final_user = f"Here are the panel narrations:\n\n{combined}"

    try:
        final_script, merge_tokens = call_openrouter(MERGE_SYSTEM, final_user, api_key, model)
        total_tokens += merge_tokens
    except Exception as e:
        logger.error(f"Merge pass failed: {e}")
        final_script = combined

    srt_content = generate_srt(final_script)
    estimated_llm_cost = round(total_tokens / 1_000_000 * 0.15, 6)

    stats = {
        "tokens_used": total_tokens,
        "panels_count": len(narrations),
        "estimated_llm_cost": estimated_llm_cost
    }

    return final_script, srt_content, stats


def generate_srt(script: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    wpm = 150
    srt_lines = []
    current_time = 0.0

    for i, sentence in enumerate(sentences, 1):
        word_count = len(sentence.split())
        duration = (word_count / wpm) * 60
        duration = max(duration, 1.5)

        start = format_srt_time(current_time)
        end = format_srt_time(current_time + duration)
        current_time += duration

        srt_lines.append(f"{i}\n{start} --> {end}\n{sentence}\n")

    return "\n".join(srt_lines)


def format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
