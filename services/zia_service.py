import logging
import time

import requests
from core.config import settings
from services.auth_service import TokenManager
from chat.zia_client import TIMEOUT_SECONDS, MAX_ATTEMPTS, BACKOFF_SECONDS, Budget, record_call

logger = logging.getLogger("ZiaService")

# Live-verified: this model occasionally (not consistently — same input succeeds on a
# later retry) misreads short non-English-script input as a prompt-injection attempt
# and refuses instead of translating, e.g. Kannada "ನಮಸ್ಕಾರ" (Namaste). This is NOT an
# HTTP error — the call still returns 200 OK with the refusal text sitting where the
# translation should be — so it has to be caught by matching the refusal wording
# itself, not a status code, and retried like any other transient LLM flake.
_REFUSAL_MARKERS = ("protected instructions", "i can't help with requests")


def _is_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _call_translate_api(text: str, source_lang: str, target_lang: str, emphasize: bool = False, timeout: float = TIMEOUT_SECONDS) -> str:
    headers = {
        "CATALYST-ORG": settings.ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {TokenManager.get_token()}",
        "Environment": settings.CATALYST_ENVIRONMENT,
        "Content-Type": "application/json"
    }

    # emphasize is used for exactly one caller: the chat language-override
    # feature's single retry after a translated answer failed its post-
    # generation script check (see chat.language.script_matches) — a plain
    # re-ask of the same prompt would likely repeat the same partial-script
    # result, so this strengthens the INSTRUCTION WRAPPER (not the text being
    # translated, which stays untouched) to explicitly rule out the failure
    # mode already observed: a mixed-script or partially-English response.
    emphasis_note = (
        " Your entire response must be written using ONLY the target language's own script — "
        "no English words, no Latin-script transliteration, no mixed script of any kind."
        if emphasize else ""
    )

    # Deliberately a single user message, no system role — live A/B tested against
    # the refusal in the module docstring: a system-role "You are an expert
    # translator... Return ONLY..." prompt reproducibly (25/25 in testing) triggered
    # a false prompt-injection refusal on short Kannada input like "ನಮಸ್ಕಾರ", while
    # folding the same instruction into a single user message reproducibly avoided it
    # (6/6). The model still reasons step-by-step before answering — same as the
    # </think>-prefixed responses already handled below, just via this path more often.
    prompt = (
        f"Translate the following {source_lang} text into {target_lang}.{emphasis_note} "
        f"Reply with ONLY the translated text, nothing else.\n\nText: {text}"
    )

    payload = {
        "model": "crm-di-glm47b_30b_it",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
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
        raise Exception(f"LLM Translation Error: {response.status_code} - {response.text}")
    record_call(latency_ms)

    raw_response = response.json().get("response", "")

    # --- THE MAGIC FIX ---
    # Agar response mein </think> hai, toh uske baad wala hissa hi asli answer hai
    if "</think>" in raw_response:
        return raw_response.split("</think>")[-1].strip()
    # Agar </think> nahi hai, par lines mein answer aaya hai, toh aakhiri line pakdo
    lines = raw_response.strip().split('\n')
    return lines[-1].strip()


def translate_text(text: str, source_lang: str = "en", target_lang: str = "kn", emphasize: bool = False, budget: "Budget | None" = None) -> str:
    """Retries on BOTH failure modes seen live against this endpoint: a network-level
    timeout/error (the endpoint is slow, 7-15s/call even on success, and occasionally
    doesn't answer at all) and a false-positive refusal (see _REFUSAL_MARKERS above).
    Originally only refusals were retried here — network errors raised immediately on
    the first attempt with zero retries, which meant most real-world failures (this
    endpoint times out far more often than it refuses) never got a second chance.

    emphasize is a separate concern from the refusal/network retries this
    function already does — it's for the chat language-override feature's own
    post-generation script-validation retry (see api/routers/chat.py), which
    needs a strictly stronger instruction wrapper on a SECOND, INTENTIONAL
    call, not another blind repeat of the same prompt that just under-
    delivered.

    `budget`, when passed, caps the per-attempt timeout to whatever's left in
    the whole request's time ceiling (chat/zia_client.Budget) and stops
    retrying once that ceiling is reached, same as call_llm_with_retry."""
    last_result = ""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if budget is not None and budget.exceeded():
            logger.warning(f"Translation abandoned after {attempt - 1} attempt(s) — request time budget exhausted")
            break
        timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
        if timeout <= 0:
            break
        try:
            result = _call_translate_api(text, source_lang, target_lang, emphasize=emphasize, timeout=timeout)
        except Exception as e:
            last_error = e
            logger.warning(f"Translation attempt {attempt}/{MAX_ATTEMPTS} failed ({e}), retrying")
            if attempt < MAX_ATTEMPTS:
                backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                if budget is None or budget.remaining() > backoff:
                    time.sleep(backoff)
            continue

        if not _is_refusal(result):
            return result

        last_result = result
        last_error = None
        logger.warning(
            f"Translation attempt {attempt}/{MAX_ATTEMPTS} got a false-positive "
            f"refusal for {text[:50]!r}, retrying"
        )

    if last_error:
        raise Exception(f"Error calling LLM after {MAX_ATTEMPTS} attempts: {str(last_error)}")
    if last_result:
        raise Exception(
            f"Translation failed after {MAX_ATTEMPTS} attempts — model kept refusing a "
            f"legitimate translation request. Last response: {last_result!r}"
        )
    raise Exception("Translation abandoned: request time budget exhausted before a usable response")
