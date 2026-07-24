"""Regex/keyword routing that skips the classification LLM call entirely for
a handful of unambiguous, high-frequency message shapes. This is the
highest-leverage latency fix alongside merging classify+extract (see
chat/router.py's module docstring): every message that matches one of these
patterns needs ZERO Zia calls just to figure out its intent, versus one
classification call before this existed.

Chat.jsx's slash commands (/case, /section, /stats — see SLASH_COMMANDS
there) already convert their argument into plain natural-language phrasing
client-side before the message ever reaches this backend, so they don't need
their own separate detection here: /case becomes "Summarize crime number
{arg}" (caught by _CRIME_NO_RE below), /section becomes "What is Section
{arg}?" (caught by _SECTION_RE), and /stats becomes "How many {arg} cases
have been registered?" (caught by _HOW_MANY_RE).
"""
import re

from chat.entity_extractor import KNOWN_DISTRICTS

# CrimeNo's real format is 18 digits (1-digit category + 4-digit district +
# 4-digit station + 4-digit year + 5-digit serial, per the ER diagram) — a
# bare long digit run anywhere in the message is a strong, unambiguous
# CASE_LOOKUP signal. Same pattern api/routers/chat.py's _CRIME_NO_IN_TEXT
# already uses to pull a crime number out of free-text question wording.
_CRIME_NO_RE = re.compile(r"\b\d{15,20}\b")

# Mirrors legal_kb_service.py's own section-reference patterns closely enough
# to ROUTE correctly — that module does its own precise matching when it
# actually answers the question (see its _IPC_SECTION_RE/_BNS_SECTION_RE/
# _BARE_SECTION_RE), this only needs to recognize "this looks like a legal-
# section question" well enough to skip the classification call.
_SECTION_RE = re.compile(r"\b(?:ipc|bns)\s*(?:section)?\s*\d+[a-z]*\b|\bsection\s+\d+[a-z]*\b", re.IGNORECASE)

# /stats's exact client-side phrasing, plus the same natural phrasing a user
# might type directly without the slash command.
_HOW_MANY_RE = re.compile(r"\bhow many\s+(.+?)\s+cases\b", re.IGNORECASE)

_LANG_OVERRIDE_RE = re.compile(
    r"\b(?:answer|reply|respond)\s+in\s+(hindi|kannada|english)\b|\bin\s+(hindi|kannada|english)\s+please\b",
    re.IGNORECASE,
)
_LANG_NAME_TO_CODE = {"english": "en", "hindi": "hi", "kannada": "kn"}

EMPTY_ENTITIES = {
    "crime_type": None, "district": None, "district_2": None, "date_from": None, "date_to": None,
    "accused_name": None, "victim_gender": None, "aggregation": None,
}


def detect_language_override(message: str) -> tuple[str | None, str]:
    """Regex-only equivalent of classify_intent's LLM-based override
    detection, used only on the fast-path branches that skip the
    classification call entirely — catches the common English phrasings
    ("answer in Hindi", "reply in Kannada", "in English please"). A native-
    script instruction ("ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸಿ") won't match here, but the
    frontend already translates the whole incoming message to English before
    it reaches this endpoint in the normal case, so a native-script override
    phrase reaching here at all is already the rare exception, not the norm."""
    match = _LANG_OVERRIDE_RE.search(message)
    if not match:
        return None, message
    lang_word = (match.group(1) or match.group(2)).lower()
    lang_code = _LANG_NAME_TO_CODE.get(lang_word)
    cleaned = message[:match.start()] + message[match.end():]
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,?")
    return lang_code, cleaned or message


def _result(intent: str, lang_code: str | None, cleaned_message: str) -> dict:
    return {
        "intent": intent,
        "confidence": 1.0,
        "response_language": lang_code,
        # No LLM call happened here to detect this (see module docstring) —
        # None means "unknown", not "English"; api/routers/chat.py's
        # auto-language resolution just has nothing to go on for this turn,
        # same as any other classification-unavailable case.
        "input_language": None,
        "cleaned_message": cleaned_message,
        # No LLM call at all was involved — "regex" rather than "zia"/"groq"
        # keeps this honest in the provider-transparency UI/response field
        # (see ChatResponse.provider_used) instead of implying either LLM
        # provider did this classification.
        "provider": "regex",
        **EMPTY_ENTITIES,
    }


def fast_path_classification(message: str) -> dict | None:
    """Returns a classify_intent()-shaped dict for an unambiguous message
    shape, or None to fall through to the real LLM classifier. Deliberately
    narrow and conservative — this is not an attempt to replace the
    classifier for anything even slightly ambiguous, only to skip it for the
    cases where a regex match is already as reliable as an LLM call would be.

    confidence is fixed at 1.0 (a regex match here is a certainty, not a
    probabilistic guess). The "how many X cases" path also fills in
    crime_type/aggregation directly from the match — the caller still runs
    it through the same validate_crime_type() whitelist check the LLM-
    extracted path already used, so an unrecognized crime type is handled
    identically either way."""
    lang_code, cleaned = detect_language_override(message)

    if _CRIME_NO_RE.search(message):
        return _result("CASE_LOOKUP", lang_code, cleaned)

    if _SECTION_RE.search(message):
        return _result("LEGAL_REFERENCE", lang_code, cleaned)

    how_many = _HOW_MANY_RE.search(cleaned)
    if how_many:
        # Live-verified real bug (2026-07-23): this fast path only ever
        # fills in crime_type + aggregation="count" — it has no way to also
        # extract a district, unlike the full LLM classifier. "How many
        # murder cases were registered in Bengaluru Urban?" matched this
        # regex, silently dropped "Bengaluru Urban" (EMPTY_ENTITIES leaves
        # district=None), and the composed answer went on to state the
        # UNFILTERED state-wide total while still mentioning "Bengaluru
        # Urban" in its own phrasing (borrowed from the original question
        # text, not from the actual computed — unfiltered — result). Rather
        # than teach this regex path a second, harder extraction, just don't
        # take the fast path at all when a known district name appears
        # anywhere in the message — fall through to the real classifier,
        # which already extracts district correctly (confirmed via direct
        # A/B test), for this one narrower, less time-critical case.
        if not any(d.lower() in cleaned.lower() for d in KNOWN_DISTRICTS):
            result = _result("AGGREGATE_QUERY", lang_code, cleaned)
            result["crime_type"] = how_many.group(1).strip()
            result["aggregation"] = "count"
            return result

    return None
