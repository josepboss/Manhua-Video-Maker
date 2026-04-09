import requests
import logging
import re
from typing import List, Tuple
from app.context import update_context, get_context, reset_context

logger = logging.getLogger(__name__)

NARRATOR_SYSTEM = (
    "You are a narrator for manhua recap videos. Convert raw dialogue and text into "
    "third-person narrative storytelling. Never keep dialogue format. Merge lines into "
    "coherent flowing sentences. Remove filler. Be concise but dramatic."
)

FINAL_PASS_SYSTEM = (
    "You are an expert video script editor. Take the panel narrations provided and merge "
    "them into one continuous, polished script. Add pacing: use short punchy sentences for "
    "action scenes and longer sentences for exposition. Target 700-1700 words (5-12 minutes "
    "read time). Return only the final script text, no headings or labels."
)


def call_openrouter(system: str, user: str, api_key: str, model: str) -> str:
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
    return data["choices"][0]["message"]["content"].strip()


def generate_panel_narration(panel_text: str, api_key: str, model: str) -> str:
    context = get_context()
    user_prompt = ""
    if context:
        user_prompt += context + "\n\n"
    user_prompt += f"Current panel text:\n{panel_text}"

    try:
        result = call_openrouter(NARRATOR_SYSTEM, user_prompt, api_key, model)
        update_context(result[:300])
        return result
    except Exception as e:
        logger.warning(f"Panel narration failed (retry 1): {e}")
        try:
            result = call_openrouter(NARRATOR_SYSTEM, user_prompt, api_key, model)
            update_context(result[:300])
            return result
        except Exception as e2:
            logger.error(f"Panel narration failed (retry 2), skipping: {e2}")
            return ""


def generate_script(
    panels: List[Tuple[str, str]],
    api_key: str,
    model: str,
    progress_callback=None
) -> Tuple[str, str, dict]:
    reset_context()
    narrations = []
    total_tokens = 0
    total_panels = len(panels)

    for i, (image_path, text) in enumerate(panels):
        if not text or not text.strip():
            logger.info(f"Skipping blank panel {i}: no OCR text")
            continue

        narration = generate_panel_narration(text, api_key, model)
        if narration:
            narrations.append(narration)

        if progress_callback:
            progress = int((i + 1) / total_panels * 40)
            progress_callback(progress, f"Narrating panel {i+1}/{total_panels}...")

    if not narrations:
        raise ValueError("No narrations generated — check OCR output and API key")

    combined = "\n\n".join(narrations)
    final_user = f"Here are the panel narrations:\n\n{combined}"

    try:
        final_script = call_openrouter(FINAL_PASS_SYSTEM, final_user, api_key, model)
    except Exception as e:
        logger.error(f"Final pass failed: {e}")
        final_script = combined

    estimated_input_tokens = len(combined.split()) * 1.3
    estimated_output_tokens = len(final_script.split()) * 1.3
    total_tokens = int(estimated_input_tokens + estimated_output_tokens)

    srt_content = generate_srt(final_script)

    stats = {
        "tokens_used": total_tokens,
        "panels_count": len(narrations),
        "estimated_llm_cost": round(total_tokens / 1_000_000 * 0.15, 4)
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
