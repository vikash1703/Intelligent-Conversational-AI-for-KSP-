from pydantic import BaseModel


class TextToSpeechRequest(BaseModel):
    text: str
    language_code: str = "en"  # ISO-639-1 (ElevenLabs), "kn" for Kannada


class SpeechToTextResponse(BaseModel):
    transcript: str
