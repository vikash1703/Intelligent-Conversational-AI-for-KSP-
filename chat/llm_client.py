import logging
import time

import requests
from core.config import settings
from services.auth_service import TokenManager
from chat.zia_client import TIMEOUT_SECONDS, MAX_ATTEMPTS, BACKOFF_SECONDS, Budget, record_call

logger = logging.getLogger("ChatLlmClient")

# Same false-positive-refusal behavior documented in services/zia_service.py:
# this model occasionally (not consistently — same input can succeed on a
# later retry) misreads a prompt as a prompt-injection attempt and refuses
# instead of doing the actual task. Still a 200 OK with the refusal text
# sitting where the real answer should be, so it has to be caught by matching
# the wording, not a status code. Live-verified hitting this in
# _compose_aggregate_answer (api/routers/chat.py) on a real, benign
# AGGREGATE_QUERY answer-composition call — not unique to translation.
_REFUSAL_MARKERS = ("protected instructions", "i can't help with requests")


def is_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def call_llm(prompt: str, temperature: float, timeout: float = TIMEOUT_SECONDS) -> str:
    """Raw chat-completion call to Zia's glm/chat endpoint — deliberately NOT
    ask_zoho_rag()/the rag/answer endpoint, which treats its whole input as a
    document-search query against the uploaded Knowledge Base. That's the wrong
    tool for classification/rewriting/extraction (extensively documented
    elsewhere in this project: RAG-endpoint prompts are unreliable for anything
    that isn't a real document lookup, e.g. the "Given this context:" and
    follow-up-framing bugs). Same endpoint zia_service.py uses for translation
    and chat/router.py uses for intent classification — shared here so a third
    (and now fourth) caller (entity_extractor.py, the AGGREGATE_QUERY answer
    composer) doesn't need its own copy of this HTTP-calling logic.

    Every attempt (success or failure) is recorded to the shared rolling
    health tracker (chat/zia_client.record_call) regardless of caller, so
    get_health()/is_degraded() reflects real, live behavior against this
    endpoint from every code path that uses it."""
    headers = {
        "CATALYST-ORG": settings.ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {TokenManager.get_token()}",
        "Environment": settings.CATALYST_ENVIRONMENT,
        "Content-Type": "application/json",
    }
    payload = {
        "model": "crm-di-glm47b_30b_it",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    start = time.monotonic()
    try:
        response = requests.post(settings.ZIA_TRANSLATE_ENDPOINT, json=payload, headers=headers, timeout=timeout)
    except Exception:
        record_call(None)
        raise
    latency_ms = (time.monotonic() - start) * 1000
    if response.status_code != 200:
        record_call(None)
        raise Exception(f"LLM call error {response.status_code}: {response.text}")
    record_call(latency_ms)
    raw = response.json().get("response", "")
    # This model still "thinks out loud" on this endpoint regardless of the
    # task — same reasoning-trace prefix zia_service.py strips before reading
    # the actual answer.
    if "</think>" in raw:
        raw = raw.split("</think>")[-1]
    return raw.strip()


def call_llm_with_retry(prompt: str, temperature: float, budget: Budget | None = None) -> str:
    """call_llm() with the same dual retry zia_service.translate_text()
    already established: both a network/timeout failure AND a false-positive
    refusal (see _REFUSAL_MARKERS above) get retried up to MAX_ATTEMPTS
    (initial + 2 retries, 1s/2s backoff between them — see chat/zia_client.py),
    since real-world failures are a mix of both and only retrying one mode
    means the other still slips through unretried. Raises the last error (or
    a refusal-specific exception) if every attempt fails — callers that need
    a non-raising fallback (e.g. an answer composer falling back to a plainer
    description) should catch this themselves, same as any other call_llm use.

    `budget`, when passed, caps BOTH the per-attempt timeout (never longer
    than what's actually left in the whole request's time ceiling) and
    whether a retry is attempted at all — once the budget is exhausted, this
    stops immediately rather than starting a call that would blow past the
    request's overall deadline anyway. Passing no budget preserves the old
    unbounded-by-request-ceiling behavior (still capped at TIMEOUT_SECONDS
    per attempt, still MAX_ATTEMPTS total), for the few callers that don't
    have a per-request deadline to share (e.g. startup cache pre-warming)."""
    last_error = None
    last_refusal = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if budget is not None and budget.exceeded():
            logger.warning(f"LLM call abandoned after {attempt - 1} attempt(s) — request time budget exhausted")
            break
        timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
        if timeout <= 0:
            break
        try:
            result = call_llm(prompt, temperature, timeout=timeout)
        except Exception as e:
            last_error = e
            logger.warning(f"LLM call attempt {attempt}/{MAX_ATTEMPTS} failed ({e}), retrying")
            if attempt < MAX_ATTEMPTS:
                backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                if budget is None or budget.remaining() > backoff:
                    time.sleep(backoff)
            continue

        if not is_refusal(result):
            return result

        last_refusal = result
        last_error = None
        logger.warning(f"LLM call attempt {attempt}/{MAX_ATTEMPTS} got a false-positive refusal, retrying")

    if last_error:
        raise last_error
    if last_refusal:
        raise Exception(f"LLM kept refusing a legitimate request after {MAX_ATTEMPTS} attempts: {last_refusal!r}")
    raise Exception("LLM call abandoned: request time budget exhausted before a usable response")
