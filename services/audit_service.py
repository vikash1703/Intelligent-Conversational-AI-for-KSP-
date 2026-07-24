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
