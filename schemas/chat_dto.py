from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    question: str
    crime_no: Optional[str] = None
    session_id: Optional[str] = None  # omit to start a new conversation

class ChatResponse(BaseModel):
    answer: str
    session_id: str
    citations: list[dict] = []
    # Crime numbers referenced by a list-type AGGREGATE_QUERY answer — the
    # frontend renders these as clickable chips that open the Cases page,
    # distinct from citations (which describe where the ANSWER's facts came
    # from, not specific cases the answer names).
    sources: list[str] = []
    # Presentation-only metadata, not part of the answer's grounding: which of
    # the 5 router intents this turn was ultimately handled as (None if
    # classification failed and the legacy grounded-RAG fallback ran), and
    # total server-side turnaround time. Purely additive — chat/router.py's
    # own classification logic is unchanged, this just reports its result.
    intent: Optional[str] = None
    latency_ms: Optional[float] = None
    # Language-override fields. `answer` above is ALWAYS the English canonical
    # text (unchanged contract — the eval harness's keyword checks depend on
    # this staying English regardless of the question's own language).
    # response_language is the resolved reply language for this turn (None =
    # no override in play at all, existing frontend auto-detect/UI-toggle
    # behavior applies unchanged; "en"/"hi"/"kn" = an explicit override is
    # active, either just detected this turn or sticky from an earlier one).
    # translated_answer is the server-translated text in that language when
    # response_language is non-English (None when response_language is None
    # or "en" — nothing to add beyond `answer` itself). language_notice is a
    # user-visible note for the one failure path requirement 5 asks for: the
    # translated text didn't validate as the right script even after one
    # retry, so English was shown instead — this must never happen silently.
    response_language: Optional[str] = None
    translated_answer: Optional[str] = None
    language_notice: Optional[str] = None
    # The backend classifier's own detection of what language the question
    # was actually WRITTEN in (chat/router.py's classify_intent) — any
    # language, by its common English name (e.g. "Hindi", "Kannada",
    # "French"), regardless of script (a romanized/Latin-script Hindi
    # question still detects as "Hindi"). Added 2026-08-22 so the frontend
    # can pick the reply language from the backend's real language-model
    # detection instead of guessing from Unicode script alone — Latin-script
    # ("Kolar mein kitne cases hue?") input was previously mis-detected as
    # English client-side and answered without translation, even though the
    # backend correctly recognized it as Hindi all along.
    input_language: Optional[str] = None
    # True only when chat/zia_client.is_degraded() found Zia's rolling recent
    # latency/failure-rate unhealthy at the START of this turn — the
    # frontend uses this to show a subtle "AI service is responding slowly"
    # banner. None/False the rest of the time; never flips retroactively
    # based on what happened during the turn itself, only what was already
    # known beforehand.
    service_degraded: Optional[bool] = None
    # Which provider actually produced `answer`'s content, for the chat UI's
    # "via Zia" / "via Gemini" / "via Groq" indicator (an accountability
    # feature per design, not something to hide) — "zia"/"groq"/"gemini" for
    # an LLM-generated answer, "cache" for a pre-warmed/previously-cached
    # legal KB hit, "raw_data" for the total-failure structured-data fallback
    # (see raw_fallback below), or None for a deterministic reply
    # (OUT_OF_SCOPE, a legal KB hit computed fresh this turn, a clarifying
    # "did you mean" response, or a zero-count aggregate) where no LLM
    # provider was involved in generating the content at all.
    provider_used: Optional[str] = None
    # Why `provider_used` isn't this turn's task-primary provider (Zia) —
    # e.g. "zia failed: <error>" or "zia unavailable (circuit breaker open)".
    # None when provider_used IS the primary (no failover happened this
    # turn) or when provider_used is None/"cache"/"raw_data" (nothing to
    # explain — no failover chain was walked). Explainable-AI/accountability
    # metadata, shown in the UI badge's tooltip.
    fallback_reason: Optional[str] = None
    # True only when every provider in the composition chain failed this turn
    # AND a deterministic, ungrounded-by-AI substitute was shown instead —
    # the raw aggregate-query result description, or (for a case lookup) the
    # raw structured case record with field labels. The frontend renders a
    # "AI composition unavailable — showing raw data" banner when this is
    # true. Never true for the plain apology text used when no raw substitute
    # exists at all (e.g. an open-ended legal question with no KB match) —
    # that path already has its own honest message, not raw data to point to.
    raw_fallback: Optional[bool] = None
    # Wall-clock duration of just the winning provider's own call (not the
    # whole turn — see latency_ms above for that) — lets the UI/eval harness
    # attribute latency to a specific provider rather than only the whole
    # request.
    provider_latency_ms: Optional[float] = None

class ChatFeedbackRequest(BaseModel):
    session_id: str
    question: str
    answer: str
    rating: str  # "up" or "down"

class ClearLanguagePreferenceRequest(BaseModel):
    session_id: str

class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    last_message_at: Optional[str] = None
    message_count: int

class ChatMessageOut(BaseModel):
    role: str
    message: str
    set_at: Optional[str] = None