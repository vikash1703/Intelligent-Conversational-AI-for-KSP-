import logging

import requests

from core.config import settings

logger = logging.getLogger("VoiceService")

# Peripheral, replaceable layer — Catalyst has no native STT/TTS (confirmed with
# Zoho SME support, see project memory). Originally built against Google Cloud;
# switched to ElevenLabs per user decision — a single API key is far simpler to
# provision than a full GCP project + billing + service-account key for the same
# capability, and ElevenLabs' Scribe model has explicit Kannada support (needed
# for this bilingual product) alongside English.

_BASE_URL = "https://api.elevenlabs.io/v1"
_STT_MODEL = "scribe_v1"
_TTS_MODEL = "eleven_multilingual_v2"
# ElevenLabs requires picking one of your account's actual voices — this is the
# stock/sample voice ID shown in their own API docs examples. Swap for a voice
# ID from the user's ElevenLabs account (Voice Library) if a specific voice is
# wanted later; nothing else in this module needs to change for that.
_DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"

_TIMEOUT_S = 30


def transcribe_audio(audio_bytes: bytes, language_code: str | None = None) -> dict:
    """language_code is ISO-639-1/639-3 (e.g. "en", "kn") if known — ElevenLabs
    auto-detects the language when omitted, per its own docs, so this is passed
    through only when the caller actually knows it (left as None here so the
    officer can speak English, Hindi, or Kannada without picking a mode first).

    Returns {"text": ..., "language_code": ...} — the response's own detected
    language_code (ISO-639-3, e.g. "eng"/"hin"/"kan") is what lets the caller
    reply back in the same language the officer actually spoke, instead of
    always answering in English regardless of the input language."""
    data = {"model_id": _STT_MODEL}
    if language_code:
        data["language_code"] = language_code

    try:
        response = requests.post(
            f"{_BASE_URL}/speech-to-text",
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            files={"file": ("audio.webm", audio_bytes)},
            data=data,
            timeout=_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"ElevenLabs speech-to-text network error: {str(e)}") from e

    if response.status_code != 200:
        raise Exception(f"ElevenLabs speech-to-text error {response.status_code}: {response.text}")

    body = response.json()
    return {"text": body.get("text", ""), "language_code": body.get("language_code")}


def synthesize_speech(text: str, language_code: str = "en") -> bytes:
    # ElevenLabs' TTS endpoint doesn't take a separate language_code parameter the
    # way Google Cloud's did — eleven_multilingual_v2 detects the target language
    # from the text itself. language_code is kept in the signature (unused here)
    # so api/routers/voice.py doesn't need to change its request shape.
    try:
        response = requests.post(
            f"{_BASE_URL}/text-to-speech/{_DEFAULT_VOICE_ID}",
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": _TTS_MODEL},
            timeout=_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"ElevenLabs text-to-speech network error: {str(e)}") from e

    if response.status_code != 200:
        raise Exception(f"ElevenLabs text-to-speech error {response.status_code}: {response.text}")

    return response.content
