import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


def get_azure_voice(settings: dict) -> str:
    lang = settings.get("narration_language", "English")
    if lang == "Arabic":
        return settings.get("azure_voice_name", "ar-SA-HamedNeural")
    return settings.get("azure_voice_name", "en-US-AndrewNeural")


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

    ssml = (
        f"<speak version='1.0' xml:lang='en-US'>"
        f"<voice name='{voice_name}'>{text}</voice>"
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
    return tts_resp.content


def estimate_tts_cost(provider: str, char_count: int) -> float:
    rates = {
        "openai": 0.015 / 1000,
        "elevenlabs": 0.30 / 1000,
        "azure": 0.016 / 1000
    }
    rate = rates.get(provider, 0.015 / 1000)
    return round(char_count * rate, 6)
