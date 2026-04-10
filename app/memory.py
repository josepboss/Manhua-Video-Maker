import json
import os
import logging

logger = logging.getLogger(__name__)

MEMORY_BASE = "app/memory"
os.makedirs(MEMORY_BASE, exist_ok=True)


def _user_memory_dir(user_id: str) -> str:
    d = os.path.join(MEMORY_BASE, user_id)
    os.makedirs(d, exist_ok=True)
    return d


def get_memory_path(manga_title: str, user_id: str = "shared") -> str:
    safe_title = manga_title.replace(" ", "_").replace("/", "-")
    return os.path.join(_user_memory_dir(user_id), f"{safe_title}.json")


def load_memory(manga_title: str, user_id: str = "shared") -> dict:
    path = get_memory_path(manga_title, user_id)
    if not os.path.exists(path):
        return {"manga_title": manga_title, "chapters": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_memory(manga_title: str, memory: dict, user_id: str = "shared"):
    path = get_memory_path(manga_title, user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def list_memories(user_id: str = "shared") -> list:
    d = _user_memory_dir(user_id)
    memories = []
    for fname in os.listdir(d):
        if fname.endswith(".json"):
            title = fname[:-5].replace("_", " ")
            mem = load_memory(title, user_id)
            chapters = mem.get("chapters", {})
            memories.append({"manga_title": title, "chapters_count": len(chapters)})
    return memories


def delete_memory(manga_title: str, user_id: str = "shared"):
    path = get_memory_path(manga_title, user_id)
    if os.path.exists(path):
        os.remove(path)


def get_context_for_chapter(manga_title: str, current_chapter: int, user_id: str = "shared") -> str:
    memory = load_memory(manga_title, user_id)
    chapters = memory.get("chapters", {})

    if not chapters:
        return ""

    previous = sorted(
        [(int(k), v) for k, v in chapters.items() if int(k) < current_chapter],
        key=lambda x: x[0]
    )

    if not previous:
        return ""

    recent = previous[-3:]
    context_parts = ["=== سياق الفصول السابقة ==="]

    for chapter_num, data in recent:
        context_parts.append(
            f"\nالفصل {chapter_num}:\n"
            f"الملخص: {data.get('summary', '')}\n"
            f"الشخصيات: {', '.join(data.get('characters', []))}\n"
            f"الأحداث الرئيسية: {', '.join(data.get('key_events', []))}\n"
            f"نهاية الفصل: {data.get('cliffhanger', '')}"
        )

    return "\n".join(context_parts)


def save_chapter_memory(manga_title: str, chapter_num: int, script: str,
                        api_key: str, model: str, user_id: str = "shared"):
    import requests

    prompt = (
        f"اقرأ هذا السكريبت لفصل من مانهوا وأخرج منه المعلومات التالية بصيغة JSON فقط بدون أي نص إضافي:\n\n"
        f"{{\n"
        f'  "summary": "ملخص من 3-4 جمل للأحداث الرئيسية",\n'
        f'  "characters": ["قائمة بأسماء الشخصيات التي ظهرت"],\n'
        f'  "key_events": ["حدث 1", "حدث 2", "حدث 3"],\n'
        f'  "cliffhanger": "آخر حدث أو التشويق في نهاية الفصل"\n'
        f"}}\n\n"
        f"السكريبت:\n{script[:3000]}"
    )

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://manhuarecap.local",
            "X-Title": "ManhuaRecap"
        }
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500
            },
            timeout=30
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        chapter_data = json.loads(text)

        memory = load_memory(manga_title, user_id)
        memory["chapters"][str(chapter_num)] = chapter_data
        save_memory(manga_title, memory, user_id)
        logger.info(f"Memory saved for {manga_title} ch{chapter_num} (user={user_id})")

    except Exception as e:
        logger.error(f"Failed to save chapter memory: {e}")
        memory = load_memory(manga_title, user_id)
        memory["chapters"][str(chapter_num)] = {
            "summary": script[:500],
            "characters": [],
            "key_events": [],
            "cliffhanger": ""
        }
        save_memory(manga_title, memory, user_id)
