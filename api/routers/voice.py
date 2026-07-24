from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import Response
from schemas.auth_dto import CurrentUser
from schemas.voice_dto import TextToSpeechRequest
from services.voice_service import transcribe_audio, synthesize_speech
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language_code: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    audio_bytes = await audio.read()
    try:
        result = transcribe_audio(audio_bytes, language_code=language_code)
    except Exception as e:
        raise AppException(f"Speech-to-text failed: {str(e)}", status_code=500)
    if not result["text"]:
        raise AppException("Could not transcribe audio — no speech detected", status_code=422)
    return {"transcript": result["text"], "detected_language": result["language_code"]}


@router.post("/speak")
def speak(
    request: TextToSpeechRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        audio_bytes = synthesize_speech(request.text, language_code=request.language_code)
    except Exception as e:
        raise AppException(f"Text-to-speech failed: {str(e)}", status_code=500)
    return Response(content=audio_bytes, media_type="audio/mpeg")
