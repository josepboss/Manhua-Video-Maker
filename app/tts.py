import re
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


def split_text(text: str, max_chars: int = 4500) -> list:
    sentences = re.split(r'(?<=[.،؟!])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) > max_chars:
            if current:
                chunks.append(current.strip())
            current = s
        else:
            current += " " + s
    if current:
        chunks.append(current.strip())
    return chunks


def get_azure_voice(settings: dict) -> str:
    lang = settings.get("narration_language", "English")
    if lang == "Arabic":
        return settings.get("azure_voice_name", "ar-SA-HamedNeural")
    return settings.get("azure_voice_name", "en-US-AndrewNeural")


def generate_audio_per_panel(
    panel_texts: list,
    job_id: str,
    settings: dict,
    audio_dir: str
) -> tuple:
    Path(audio_dir).mkdir(parents=True, exist_ok=True)
    provider = settings.get("tts_provider", "openai")
    total_chars = 0
    panel_audio_paths = []
    all_bytes = []

    for i, text in enumerate(panel_texts):
        if not text or not text.strip():
            text = "..."

        panel_path = str(Path(audio_dir) / f"panel_{i:04d}.mp3")

        if provider == "elevenlabs":
            audio_bytes = generate_elevenlabs_tts(
                text,
                settings.get("elevenlabs_api_key", ""),
                settings.get("elevenlabs_voice_id", "")
            )
        elif provider == "azure":
            audio_bytes = generate_azure_tts(
                text,
                settings.get("azure_tts_key", ""),
                settings.get("azure_tts_region", ""),
                get_azure_voice(settings)
            )
        else:
            audio_bytes = generate_openai_tts(
                text,
                settings.get("openai_tts_key", ""),
                settings.get("openai_tts_voice", "onyx")
            )

        with open(panel_path, "wb") as f:
            f.write(audio_bytes)

        panel_audio_paths.append(panel_path)
        all_bytes.append(audio_bytes)
        total_chars += len(text)
        logger.info(f"Panel TTS {i+1}/{len(panel_texts)} done ({len(text)} chars)")

    concat_path = str(Path(audio_dir) / "narration.mp3")
    with open(concat_path, "wb") as f:
        f.write(b"".join(all_bytes))

    tts_cost = estimate_tts_cost(provider, total_chars)
    return panel_audio_paths, concat_path, total_chars, tts_cost


def generate_audio(script: str, job_id: str, settings: dict, audio_dir: str) -> tuple:
    Path(audio_dir).mkdir(parents=True, exist_ok=True)
    output_path = str(Path(audio_dir) / "narration.mp3")
    provider = settings.get("tts_provider", "openai")
    char_count = len(script)

    if provider == "elevenlabs":
        audio_bytes = generate_elevenlabs_tts(
            script,
            settings.get("elevenlabs_api_key", ""),
            settings.get("elevenlabs_voice_id", "")
        )
    elif provider == "azure":
        audio_bytes = generate_azure_tts(
            script,
            settings.get("azure_tts_key", ""),
            settings.get("azure_tts_region", ""),
            get_azure_voice(settings)
        )
    else:
        audio_bytes = generate_openai_tts(
            script,
            settings.get("openai_tts_key", ""),
            settings.get("openai_tts_voice", "onyx")
        )

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    tts_cost = estimate_tts_cost(provider, char_count)
    return output_path, char_count, tts_cost


def generate_openai_tts(text: str, api_key: str, voice: str = "onyx") -> bytes:
    if not api_key:
        raise ValueError("OpenAI TTS API key not configured")
    response = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": "tts-1",
            "input": text,
            "voice": voice
        },
        timeout=120
    )
    response.raise_for_status()
    return response.content


def generate_elevenlabs_tts(text: str, api_key: str, voice_id: str) -> bytes:
    if not api_key or not voice_id:
        raise ValueError("ElevenLabs API key and voice ID are required")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        },
        json={
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        },
        timeout=180
    )
    response.raise_for_status()
    return response.content


def generate_azure_tts(
    text: str,
    api_key: str,
    region: str,
    voice_name: str = "en-US-AndrewNeural"
) -> bytes:
    if not api_key or not region:
        raise ValueError("Azure TTS key and region are required")

    token_url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    token_resp = requests.post(
        token_url,
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=10
    )
    token_resp.raise_for_status()
    token = token_resp.text

    chunks = split_text(text, max_chars=4500)
    logger.info(f"Azure TTS: splitting into {len(chunks)} chunk(s)")

    audio_parts = []
    for i, chunk in enumerate(chunks):
        escaped = (
            chunk
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        ssml = (
            f"<speak version='1.0' xml:lang='ar-SA' xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice name='{voice_name}'>{escaped}</voice>"
            f"</speak>"
        )
        tts_resp = requests.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3"
            },
            data=ssml.encode("utf-8"),
            timeout=180
        )
        tts_resp.raise_for_status()
        audio_parts.append(tts_resp.content)
        logger.info(f"Azure TTS: chunk {i + 1}/{len(chunks)} done ({len(chunk)} chars)")

    return b"".join(audio_parts)


def estimate_tts_cost(provider: str, char_count: int) -> float:
    rates = {
        "openai": 0.015 / 1000,
        "elevenlabs": 0.30 / 1000,
        "azure": 0.016 / 1000
    }
    rate = rates.get(provider, 0.015 / 1000)
    return round(char_count * rate, 6)
