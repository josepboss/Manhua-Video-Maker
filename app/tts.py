import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_audio(script: str, job_id: str, settings: dict, audio_dir: str) -> tuple:
    Path(audio_dir).mkdir(parents=True, exist_ok=True)
    output_path = str(Path(audio_dir) / "narration.mp3")
    provider = settings.get("tts_provider", "openai")
    char_count = len(script)

    if provider == "elevenlabs":
        audio_bytes = tts_elevenlabs(script, settings)
    elif provider == "azure":
        audio_bytes = tts_azure(script, settings)
    else:
        audio_bytes = tts_openai(script, settings)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    tts_cost = estimate_tts_cost(provider, char_count)

    return output_path, char_count, tts_cost


def tts_openai(script: str, settings: dict) -> bytes:
    api_key = settings.get("openai_tts_key", "")
    if not api_key:
        raise ValueError("OpenAI TTS API key not configured")
    voice = settings.get("openai_tts_voice", "onyx")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "tts-1",
        "input": script,
        "voice": voice,
        "response_format": "mp3"
    }
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        json=payload,
        headers=headers,
        timeout=120
    )
    resp.raise_for_status()
    return resp.content


def tts_elevenlabs(script: str, settings: dict) -> bytes:
    api_key = settings.get("elevenlabs_api_key", "")
    voice_id = settings.get("elevenlabs_voice_id", "")
    if not api_key or not voice_id:
        raise ValueError("ElevenLabs API key and voice ID required")

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "text": script,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        json=payload,
        headers=headers,
        timeout=180
    )
    resp.raise_for_status()
    return resp.content


def tts_azure(script: str, settings: dict) -> bytes:
    import azure.cognitiveservices.speech as speechsdk
    import io
    import os
    import tempfile

    azure_key = settings.get("azure_tts_key", "")
    azure_region = settings.get("azure_tts_region", "")
    voice_name = settings.get("azure_voice_name", "en-US-AndrewNeural")

    if not azure_key or not azure_region:
        raise ValueError("Azure TTS key and region required")

    speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
    speech_config.speech_synthesis_voice_name = voice_name
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
    )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    audio_config = speechsdk.audio.AudioOutputConfig(filename=tmp_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = synthesizer.speak_text_async(script).get()

    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        raise RuntimeError(f"Azure TTS cancelled: {cancellation.reason} - {cancellation.error_details}")

    with open(tmp_path, "rb") as f:
        audio_bytes = f.read()
    os.unlink(tmp_path)
    return audio_bytes


def estimate_tts_cost(provider: str, char_count: int) -> float:
    rates = {
        "openai": 0.015 / 1000,
        "elevenlabs": 0.30 / 1000,
        "azure": 0.016 / 1000
    }
    rate = rates.get(provider, 0.015 / 1000)
    return round(char_count * rate, 4)
