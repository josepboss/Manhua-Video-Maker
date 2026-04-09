import base64
import requests
import logging
import re
from typing import List, Tuple
from app import context

logger = logging.getLogger(__name__)

VISION_CAPABLE_MODELS = {
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "google/gemini-2.0-flash-lite-001",
    "google/gemini-2.0-flash-lite",
    "google/gemini-flash-1.5",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.0-pro-exp",
    "anthropic/claude-3.5-haiku",
}


def get_narrator_prompt(language: str = "English", story_context: str = "") -> str:
    context_block = f"\n\nStory context provided by user:\n{story_context}" if story_context else ""
    return (
        f"You are a narrator for manhua recap videos.{context_block}\n\n"
        f"Use the character names from the story context instead of generic terms like 'the man' or 'the woman'. "
        f"Convert raw dialogue and text into third-person narrative storytelling in {language}. "
        f"Never keep dialogue format. Never use quotes. Merge lines into coherent flowing sentences. "
        f"Remove filler words and repetition. Be concise but dramatic. Always write in third person. "
        f"Your entire response must be in {language} only."
    )


def get_merge_prompt(language: str = "English") -> str:
    return (
        f"You are an expert video script editor. Merge the panel narrations into one "
        f"continuous polished script in {language}. Add pacing: short sentences for action, "
        f"longer for exposition. Target 700-1700 words. Return only the final script in "
        f"{language}, no headings or labels."
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


def call_openrouter_vision(image_path: str, api_key: str, model: str, story_context: str = "", language: str = "English") -> Tuple[str, int]:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    context_prefix = f"Story context: {story_context}\n\n" if story_context else ""
    vision_user_prompt = (
        f"{context_prefix}This is a manhua panel. Describe what is happening "
        f"using the correct character names in one third-person narrative sentence in {language}."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://manhuarecap.local",
        "X-Title": "ManhuaRecap"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_narrator_prompt(language, story_context)},
            {"role": "user", "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                },
                {
                    "type": "text",
                    "text": vision_user_prompt
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


def generate_panel_narration(
    panel_text: str, api_key: str, model: str, narrator_system: str
) -> Tuple[str, int]:
    context_str = context.get_context()
    user_prompt = (
        f"{context_str}\n\n" if context_str else ""
    ) + (
        f"Current panel text:\n{panel_text}\n\n"
        "Write a third-person narrative sentence for this panel only."
    )

    try:
        result, tokens = call_openrouter(narrator_system, user_prompt, api_key, model)
        context.update_context(result[:300])
        return result, tokens
    except Exception as e:
        logger.warning(f"Panel narration failed (retry 1): {e}")
        try:
            result, tokens = call_openrouter(narrator_system, user_prompt, api_key, model)
            context.update_context(result[:300])
            return result, tokens
        except Exception as e2:
            logger.error(f"Panel narration failed (retry 2), skipping: {e2}")
            return "", 0


def generate_script(
    panels: List[Tuple[str, str]],
    api_key: str,
    model: str,
    narration_language: str = "English",
    ocr_lang: str = "en",
    story_context: str = "",
    progress_callback=None
) -> Tuple[str, str, dict]:
    context.reset_context()
    narrations = []
    total_tokens = 0
    total_panels = len(panels)

    narrator_system = get_narrator_prompt(narration_language, story_context)
    merge_system = get_merge_prompt(narration_language)

    use_arabic_strategy = (ocr_lang == "ar" and model in VISION_CAPABLE_MODELS)

    for i, (image_path, text) in enumerate(panels):

        if use_arabic_strategy:
            # Arabic: vision is primary, OCR text is fallback
            narration, tokens = "", 0
            try:
                narration, tokens = call_openrouter_vision(image_path, api_key, model, story_context, narration_language)
                logger.info(f"Arabic vision panel {i}: '{narration[:80]}'")
            except Exception as e:
                logger.warning(f"Arabic vision failed for panel {i}: {e}")

            if not narration and text and text.strip():
                logger.info(f"Arabic vision fallback → OCR text for panel {i}")
                narration, tokens = generate_panel_narration(
                    text, api_key, model, narrator_system
                )

        else:
            # Default: OCR text is primary, vision is fallback for empty panels
            if text and text.strip():
                narration, tokens = generate_panel_narration(
                    text, api_key, model, narrator_system
                )
            elif model in VISION_CAPABLE_MODELS:
                logger.info(f"Panel {i} has no OCR text — using vision fallback")
                try:
                    narration, tokens = call_openrouter_vision(image_path, api_key, model, story_context, narration_language)
                    logger.info(f"Vision fallback panel {i}: '{narration[:80]}'")
                except Exception as e:
                    logger.warning(f"Vision fallback failed for panel {i}: {e}")
                    narration, tokens = "", 0
            else:
                logger.info(f"Skipping panel {i} — no OCR text and model has no vision")
                narration, tokens = "", 0

        total_tokens += tokens
        if narration:
            context.update_context(narration[:300])
            narrations.append(narration)

        if progress_callback:
            pct = int((i + 1) / total_panels * 40)
            progress_callback(pct, f"Narrating panel {i+1}/{total_panels}...")

    if not narrations:
        raise ValueError("No narrations generated — check OCR output and API key")

    combined = "\n\n".join(narrations)
    final_user = f"Here are the panel narrations:\n\n{combined}"

    try:
        final_script, merge_tokens = call_openrouter(
            merge_system, final_user, api_key, model
        )
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
