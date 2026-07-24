from fastapi import APIRouter, Depends
from schemas.translate_dto import TranslateRequest, TranslateResponse
from schemas.auth_dto import CurrentUser
from chat.llm_provider import translate_with_failover
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()

@router.post("/", response_model=TranslateResponse)
def translate_message(request: TranslateRequest, current_user: CurrentUser = Depends(get_current_user)):
    if not request.text or not request.text.strip():
        raise AppException("Text cannot be empty", status_code=400)

    try:
        # Routed through the Gemini-primary/Zia-fallback "translation" chain
        # (see chat/llm_provider.py) instead of calling Zia directly — this
        # standalone endpoint had the same "waits on Zia with no fallback at
        # all" gap chat.py's own translation calls used to have.
        translated, _provider, _reason, _latency_ms = translate_with_failover(
            request.text, request.source_lang, request.target_lang,
        )
        return TranslateResponse(translated_text=translated)
    except Exception as e:
        raise AppException(str(e), status_code=500)