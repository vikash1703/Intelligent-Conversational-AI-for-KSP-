import json
import logging
from datetime import datetime, timezone

from core.catalyst_client import insert_row, execute_zcql, zcql_escape
from core.config import settings

logger = logging.getLogger("AuditService")

# Marker prefix distinguishing a classification-log row's response_text from a
# normal chat turn's plain-text answer, without needing a new AuditLog column —
# the live table's schema is fixed (log_id/user_id/role_name/session_id/
# query_text/response_text/ip_address/entry_timestamp, see project memory) and
# this project's established convention is to adapt to the live schema rather
# than assume a Catalyst-console change that hasn't happened. A later query
# filtering AuditLog for evaluation can pull out just these rows by matching
# this prefix, then json.loads the rest of response_text.
_INTENT_LOG_MARKER = "[intent_classification] "
# Same marker-prefix convention, for the Chat page's thumbs up/down —
# accountability trail of which answers officers found useful, without a new
# AuditLog column.
_FEEDBACK_LOG_MARKER = "[chat_feedback] "
# Same marker-prefix convention, for the chat language-override feature's
# "sticky" per-session preference (see get_session_language_preference) —
# one new row per explicit override or clear action, not a single mutable
# field, since AuditLog rows are append-only; "current" preference is
# whichever of these rows is most recent for the session.
_LANGUAGE_PREF_MARKER = "[language_preference] "
# Same marker-prefix convention, for the per-provider circuit breaker (see
# chat/circuit_breaker.py, generalized 2026-07-23 to cover zia/groq/gemini
# independently) — every trip to fallback, failed recovery probe, and
# restore-to-primary gets its own row, so an operator/reviewer can see
# exactly when, why, and for WHICH provider the active LLM routing changed,
# not just infer it from response latency after the fact.
_PROVIDER_EVENT_MARKER = "[provider_event] "
# Same marker-prefix convention, for the FIR Registration module (added
# 2026-08-28) — the accountability trail the user explicitly asked for:
# every registration ATTEMPT (success and failure both) gets its own row,
# with the full submitted payload preserved in response_text, so a real FIR
# is always traceable back to exactly who filed it, when, and what they
# submitted — not just the fact that a CaseMaster row exists.
_FIR_REGISTRATION_MARKER = "[fir_registration] "
# Same marker-prefix convention, for FIR amendments (added 2026-08-28) — a
# separate marker from registration so the two are independently greppable:
# "how many times has this crime_no been amended, and by whom" is a
# different accountability question than "who originally filed it."
_FIR_AMENDMENT_MARKER = "[fir_amendment] "
# Same marker-prefix convention, for the Chargesheet Draft feature (added
# 2026-08-29) — one row per generation attempt, so a chargesheet draft
# actually used in court can always be traced back to who generated it and
# when, per the feature's explicit audit requirement.
_CHARGESHEET_DRAFT_MARKER = "[chargesheet_draft] "


def log_provider_event(event: str, reason: str, provider: str = "zia", user_id: str = "system", session_id: str = "system") -> None:
    """Best-effort, never raises — a failure to LOG a breaker trip must never
    itself take down the breaker or the request that triggered it. `event` is
    one of "tripped_to_fallback"/"restored_to_primary"/"probe_failed_stay_on_fallback".
    `provider` defaults to "zia" so any pre-existing call site (written
    before Groq/Gemini support existed) keeps working unchanged."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": "system",
                "session_id": session_id,
                "query_text": "",
                "response_text": _PROVIDER_EVENT_MARKER + json.dumps({"event": event, "provider": provider, "reason": reason}),
                "ip_address": "internal",
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist provider event log: {str(e)}")


def log_intent_classification(
    user_id: str,
    role_name: str,
    session_id: str,
    message: str,
    intent: str | None,
    confidence: float,
    latency_ms: float,
    ip_address: str,
) -> None:
    """Persists one router classification event for later evaluation — same
    table/mechanism as log_chat_query (best-effort, never raises), reusing
    query_text for the message classified and response_text for a structured
    JSON payload (see _INTENT_LOG_MARKER above) rather than a second answer."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": session_id,
                "query_text": message,
                "response_text": _INTENT_LOG_MARKER + json.dumps({
                    "intent": intent,
                    "confidence": confidence,
                    "latency_ms": round(latency_ms, 1),
                }),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist intent classification log: {str(e)}")


def log_chat_feedback(
    user_id: str,
    role_name: str,
    session_id: str,
    question: str,
    answer: str,
    rating: str,
    ip_address: str,
) -> None:
    """Persists one thumbs up/down rating on an assistant answer — same
    table/marker-prefix mechanism as log_intent_classification. query_text
    holds the question that produced the rated answer (so this row is
    findable/readable in context); response_text holds the marker + a JSON
    payload with the rating and an answer preview (not the full answer —
    that's already stored verbatim in the matching log_chat_query row for
    this same session/question, no need to duplicate it in full here)."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": session_id,
                "query_text": question,
                "response_text": _FEEDBACK_LOG_MARKER + json.dumps({
                    "rating": rating,
                    "answer_preview": answer[:200],
                }),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist chat feedback log: {str(e)}")


def log_language_preference(
    user_id: str,
    role_name: str,
    session_id: str,
    language: str | None,
    ip_address: str,
) -> None:
    """Persists a "sticky" reply-language preference change for a session —
    either a real explicit override (language is "en"/"hi"/"kn", set the
    moment classify_intent detects one in a message) or an explicit clear
    (language is None, the Chat page's "clear" button on the language badge).
    Same table/marker-prefix mechanism as log_intent_classification/
    log_chat_feedback. Reading this back (get_session_language_preference)
    only ever looks at the MOST RECENT such row for the session, so a clear
    genuinely un-stickies a prior override rather than the override
    resurfacing on the next read."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": session_id,
                "query_text": "",
                "response_text": _LANGUAGE_PREF_MARKER + json.dumps({"language": language}),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist language preference log: {str(e)}")


def get_session_language_preference(session_id: str) -> str | None:
    """The session's current sticky reply-language override, or None if
    never set (or most recently cleared) — see log_language_preference.
    Scoped by session_id in the ZCQL WHERE clause (always a UUID, safe to
    interpolate) rather than any free-text field, same reasoning as
    chat_history_service.search_sessions_by_content. Returns None (treated as
    "no sticky preference", not an error) on a lookup failure — a transient
    Catalyst hiccup should degrade to the existing auto-detect/UI-fallback
    behavior, never block the turn."""
    try:
        safe_sid = zcql_escape(session_id)
        rows = execute_zcql(
            f"SELECT {settings.AUDIT_LOG_TABLE}.response_text "
            f"FROM {settings.AUDIT_LOG_TABLE} WHERE {settings.AUDIT_LOG_TABLE}.session_id = '{safe_sid}' "
            f"ORDER BY {settings.AUDIT_LOG_TABLE}.CREATEDTIME DESC LIMIT 50"
        )
    except Exception as e:
        logger.warning(f"Could not read back session language preference: {e}")
        return None

    for r in rows:
        row = r.get(settings.AUDIT_LOG_TABLE, r)
        text = row.get("response_text", "") or ""
        if text.startswith(_LANGUAGE_PREF_MARKER):
            try:
                payload = json.loads(text[len(_LANGUAGE_PREF_MARKER):])
            except json.JSONDecodeError:
                continue
            return payload.get("language")
    return None


def log_fir_registration(
    user_id: str,
    role_name: str,
    ip_address: str,
    success: bool,
    payload: dict,
    crime_no: str | None = None,
    error: str | None = None,
) -> None:
    """Persists one FIR registration attempt — success AND failure both (per
    the module's explicit accountability requirement), including the full
    submitted payload (query_text) so what the officer actually typed is
    preserved verbatim, not just a summary. response_text carries the
    outcome: the generated crime_no on success, or the error on failure.
    session_id has no real session concept for a one-shot form submission —
    "fir" is used as a fixed, greppable value rather than a fabricated UUID."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": "fir",
                "query_text": json.dumps(payload, default=str)[:9000],
                "response_text": _FIR_REGISTRATION_MARKER + json.dumps({
                    "success": success,
                    "crime_no": crime_no,
                    "error": error,
                }),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist FIR registration audit log: {str(e)}")


def log_fir_amendment(
    user_id: str,
    role_name: str,
    ip_address: str,
    crime_no: str,
    success: bool,
    payload: dict,
    error: str | None = None,
) -> None:
    """Persists one FIR amendment attempt — same success/failure-both,
    full-payload-preserved pattern as log_fir_registration, its own marker
    so amendments and original registrations are independently traceable
    for the same crime_no."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": "fir",
                "query_text": json.dumps(payload, default=str)[:9000],
                "response_text": _FIR_AMENDMENT_MARKER + json.dumps({
                    "success": success,
                    "crime_no": crime_no,
                    "error": error,
                }),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist FIR amendment audit log: {str(e)}")


def log_chargesheet_draft_generation(
    user_id: str,
    role_name: str,
    ip_address: str,
    crime_no: str,
    success: bool,
    error: str | None = None,
) -> None:
    """Persists one chargesheet-draft generation attempt — same marker-
    prefix, best-effort-never-raises pattern as log_fir_registration/
    log_fir_amendment. session_id fixed to "chargesheet" for the same reason
    "fir" is used there: no real session concept for a one-shot action."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": "chargesheet",
                "query_text": crime_no,
                "response_text": _CHARGESHEET_DRAFT_MARKER + json.dumps({
                    "success": success, "crime_no": crime_no, "error": error,
                }),
                "ip_address": ip_address,
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist chargesheet draft audit log: {str(e)}")


def log_chat_query(
    user_id: str,
    role_name: str,
    session_id: str,
    query_text: str,
    response_text: str,
    ip_address: str,
) -> None:
    """Persist one NL-query audit trail row to Catalyst. Failures are logged, never
    raised, so a Catalyst outage cannot take down chat request handling.

    Column names match the live AuditLog table (log_id/user_id/role_name/session_id/
    query_text/response_text/ip_address/entry_timestamp) — this table is designed to
    audit officer natural-language queries and chatbot responses, not generic HTTP
    request/response metadata."""
    try:
        insert_row(
            settings.AUDIT_LOG_TABLE,
            {
                "user_id": user_id,
                "role_name": role_name,
                "session_id": session_id,
                "query_text": query_text,
                "response_text": response_text,
                "ip_address": ip_address,
                # Catalyst rejects ISO-8601 (T separator, microseconds, offset) for
                # datetime columns — it expects "YYYY-MM-DD HH:MM:SS".
                "entry_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist audit log: {str(e)}")
