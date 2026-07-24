from typing import Literal

from pydantic import BaseModel, Field

class TranslateRequest(BaseModel):
    # Extended from English+Kannada (Red-tier spec) to also include Hindi, per the
    # user's voice-input requirement — an officer can speak in any of the three and
    # get a same-language reply. A garbage lang code like "xx"/"yy" used to sail
    # straight through to the LLM, which would spend 3 retries x 15s timeout each
    # (~45s) confused by a nonsense target language before failing with a 500;
    # restricting to real values rejects that instantly with a 422 instead.
    text: str = Field(max_length=2000)
    source_lang: Literal["en", "kn", "hi"] = "en"
    target_lang: Literal["en", "kn", "hi"] = "kn"

class TranslateResponse(BaseModel):
    translated_text: str