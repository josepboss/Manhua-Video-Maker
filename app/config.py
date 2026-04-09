import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).parent / "settings.json"

DEFAULT_SETTINGS = {
    "openrouter_api_key": "",
    "openrouter_model": "openai/gpt-4o-mini",
    "tts_provider": "openai",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "azure_tts_key": "",
    "azure_tts_region": "",
    "azure_voice_name": "en-US-AndrewNeural",
    "openai_tts_key": "",
    "openai_tts_voice": "onyx",
    "video_format": "landscape",
    "watermark_text": "ManhuaRecap"
}


def get_settings() -> dict:
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
    with open(SETTINGS_PATH, "r") as f:
        data = json.load(f)
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data)
    return merged


def save_settings(settings: dict) -> None:
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(merged, f, indent=2)
