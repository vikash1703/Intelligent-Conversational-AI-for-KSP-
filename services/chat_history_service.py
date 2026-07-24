import logging
from datetime import datetime, timezone

from core.catalyst_client import execute_zcql, insert_row, delete_row, zcql_escape
from core.config import settings
from services.vector_memory_service import index_message, delete_session as _delete_vector_session

logger = logging.getLogger("ChatHistoryService")


def save_message(session_id: str, role: str, message: str) -> None:
    try:
        insert_row(
            settings.CHAT_HISTORY_TABLE,
            {
                # Live column names are "SessionId" and "SetAt" (not SessionID/SentAt) —
                # verified directly against Catalyst.
                "SessionId": session_id,
                "Role": role,
                "Message": message,
                # Catalyst rejects ISO-8601 (T separator, microseconds, offset) for
                # datetime columns — it expects "YYYY-MM-DD HH:MM:SS".
                "SetAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception as e:
        logger.error(f"Failed to persist chat message: {str(e)}")

    # Catalyst's ChatHistory table stays the source of truth (PDF export, audit
    # trail); this side-index is what lets a long conversation stay searchable by
    # meaning instead of only by recency — see vector_memory_service docstring for
    # why this can't be done with Catalyst's own QuickML Knowledge Base.
    index_message(session_id, role, message)


def _fetch_messages(session_id: str, limit: int) -> list:
    safe_session_id = zcql_escape(session_id)
    # Ordered by CREATEDTIME (Catalyst's own millisecond-precision auto column), not
    # SetAt — SetAt only has second-level precision, and a user+assistant pair saved
    # within the same second sort as ties, sometimes putting the assistant's reply
    # before its own prompt in the transcript sent back to the RAG model.
    rows = execute_zcql(
        f"SELECT {settings.CHAT_HISTORY_TABLE}.Role, {settings.CHAT_HISTORY_TABLE}.Message, "
        f"{settings.CHAT_HISTORY_TABLE}.SetAt "
        f"FROM {settings.CHAT_HISTORY_TABLE} "
        f"WHERE {settings.CHAT_HISTORY_TABLE}.SessionId = '{safe_session_id}' "
        f"ORDER BY {settings.CHAT_HISTORY_TABLE}.CREATEDTIME DESC LIMIT {limit}"
    )
    messages = [r.get(settings.CHAT_HISTORY_TABLE, r) for r in rows]
    return list(reversed(messages))


def get_recent_messages(session_id: str, limit: int = 6) -> list:
    """Last `limit` turns for this session, oldest first, used to give the RAG
    call conversational context without the caller repeating themselves. Kept
    deliberately small — this text gets stuffed into the RAG prompt."""
    return _fetch_messages(session_id, max(1, min(int(limit), 20)))


def get_all_messages(session_id: str, limit: int = 300) -> list:
    """Full session transcript, oldest first, used for conversation-history PDF
    export — not for RAG prompts, so no need to keep this small. Capped at 300,
    ZCQL's hard maximum for a LIMIT clause."""
    return _fetch_messages(session_id, max(1, min(int(limit), 300)))


def list_sessions_for_user(user_id: str, limit: int = 200) -> list:
    """Chat-session list for the sidebar history panel (like Claude's own
    "Recents" list) — one entry per past conversation this user has had, most
    recent first. Built from AuditLog, not ChatHistory: ChatHistory has no user
    column at all (ownership was never its job, see save_message), while every
    chat turn already gets a per-user AuditLog row for compliance purposes —
    reusing it here avoids adding a new column/table just to attribute sessions
    to users. Grouped in Python rather than a ZCQL GROUP BY because a session's
    *title* needs its earliest question text, not an aggregate value.

    A session with no turn inside the most recent `limit` rows across ALL of
    this user's history won't be listed — an accepted trade-off for a first
    version, same shape as get_all_messages' own 300-row ZCQL cap."""
    safe_user = zcql_escape(user_id)
    rows = execute_zcql(
        f"SELECT {settings.AUDIT_LOG_TABLE}.session_id, {settings.AUDIT_LOG_TABLE}.query_text, "
        f"{settings.AUDIT_LOG_TABLE}.entry_timestamp "
        f"FROM {settings.AUDIT_LOG_TABLE} WHERE {settings.AUDIT_LOG_TABLE}.user_id = '{safe_user}' "
        f"ORDER BY {settings.AUDIT_LOG_TABLE}.CREATEDTIME DESC LIMIT {max(1, min(int(limit), 300))}"
    )

    sessions: dict = {}
    for r in rows:
        row = r.get(settings.AUDIT_LOG_TABLE, r)
        sid = row.get("session_id")
        if not sid:
            continue
        ts = row.get("entry_timestamp")
        query_text = row.get("query_text") or ""
        if sid not in sessions:
            sessions[sid] = {"session_id": sid, "title": query_text, "last_message_at": ts, "message_count": 0}
        entry = sessions[sid]
        entry["message_count"] += 1
        # Rows arrive newest-first (CREATEDTIME DESC), so the last one seen for
        # a given session is the oldest one in this window — using it as the
        # title means "what did they first ask", not "what did they ask last".
        entry["title"] = query_text
        if ts and (not entry["last_message_at"] or ts > entry["last_message_at"]):
            entry["last_message_at"] = ts

    result = list(sessions.values())
    for s in result:
        s["title"] = (s["title"][:80] + "…") if len(s["title"]) > 80 else s["title"]
    result.sort(key=lambda s: s["last_message_at"] or "", reverse=True)
    return result


def search_sessions_by_content(user_id: str, query: str) -> set:
    """Session ids (owned by user_id) whose transcript contains query as a
    substring — the Chat sidebar's search box filters by title locally
    already (list_sessions_for_user's own title field); this covers the
    "content" half by scanning ChatHistory.Message directly, since AuditLog's
    query_text only ever holds a session's first question, not the full
    transcript. ChatHistory has no user column (see save_message), so this
    intersects with list_sessions_for_user's already-ownership-checked ids
    rather than trying to scope the LIKE query by user itself."""
    if not query or not query.strip():
        return set()
    safe_query = zcql_escape(query.strip())
    rows = execute_zcql(
        f"SELECT {settings.CHAT_HISTORY_TABLE}.SessionId FROM {settings.CHAT_HISTORY_TABLE} "
        f"WHERE {settings.CHAT_HISTORY_TABLE}.Message LIKE '*{safe_query}*' LIMIT 300"
    )
    matched = {r.get(settings.CHAT_HISTORY_TABLE, r)["SessionId"] for r in rows}
    if not matched:
        return set()
    owned = {s["session_id"] for s in list_sessions_for_user(user_id)}
    return matched & owned


def delete_session(session_id: str, user_id: str) -> int:
    """Deletes every ChatHistory row for this session, this user's AuditLog rows
    for it, and clears the semantic-memory index — a full "forget this
    conversation." Ownership-checked first, same as opening a session's
    transcript — a session_id alone (even though it's an unguessable UUID)
    isn't treated as proof it's this user's to delete.

    Loops single-row deletes rather than Catalyst's bulk-delete endpoint: that
    endpoint's exact request-body shape isn't available in any fetchable
    documentation (checked before writing this), while the single-row DELETE
    .../row/{row_id} shape is confirmed. A chat session is at most a few dozen
    turns, so the extra HTTP calls are a fine trade for not guessing wrong.

    Returns the number of ChatHistory rows actually removed. Raises the
    underlying CatalystQueryError if rows were found but NONE could actually be
    deleted (e.g. the Zoho OAuth token's scopes don't include row deletion) —
    live-verified this can happen with a 401 OAUTH_SCOPE_MISMATCH that would
    otherwise be silently swallowed per-row, making the endpoint claim success
    while nothing was actually removed. Returns 0 (no raise) only when the
    session genuinely didn't belong to this user."""
    if not session_belongs_to_user(session_id, user_id):
        return 0

    safe_sid = zcql_escape(session_id)
    history_rows = execute_zcql(
        f"SELECT {settings.CHAT_HISTORY_TABLE}.ROWID FROM {settings.CHAT_HISTORY_TABLE} "
        f"WHERE {settings.CHAT_HISTORY_TABLE}.SessionId = '{safe_sid}' LIMIT 300"
    )
    deleted_count = 0
    last_error = None
    for r in history_rows:
        row = r.get(settings.CHAT_HISTORY_TABLE, r)
        try:
            delete_row(settings.CHAT_HISTORY_TABLE, row["ROWID"])
            deleted_count += 1
        except Exception as e:
            last_error = e
            logger.error(f"Failed to delete ChatHistory row {row.get('ROWID')}: {str(e)}")

    if history_rows and deleted_count == 0:
        # Every single delete failed the same way (e.g. an OAuth scope
        # problem affects every row identically) — surfacing this beats
        # reporting "deleted" while the session is still fully intact.
        raise last_error

    safe_user = zcql_escape(user_id)
    audit_rows = execute_zcql(
        f"SELECT {settings.AUDIT_LOG_TABLE}.ROWID FROM {settings.AUDIT_LOG_TABLE} "
        f"WHERE {settings.AUDIT_LOG_TABLE}.session_id = '{safe_sid}' AND {settings.AUDIT_LOG_TABLE}.user_id = '{safe_user}' LIMIT 300"
    )
    for r in audit_rows:
        row = r.get(settings.AUDIT_LOG_TABLE, r)
        try:
            delete_row(settings.AUDIT_LOG_TABLE, row["ROWID"])
        except Exception as e:
            logger.error(f"Failed to delete AuditLog row {row.get('ROWID')}: {str(e)}")

    _delete_vector_session(session_id)
    return deleted_count


def session_belongs_to_user(session_id: str, user_id: str) -> bool:
    """Ownership check before returning a full transcript by session_id. The id
    itself is an unguessable UUID already, but this closes the gap properly
    rather than relying on that alone — same principle as every other
    resource-ownership check in this codebase."""
    safe_sid = zcql_escape(session_id)
    safe_user = zcql_escape(user_id)
    rows = execute_zcql(
        f"SELECT {settings.AUDIT_LOG_TABLE}.session_id FROM {settings.AUDIT_LOG_TABLE} "
        f"WHERE {settings.AUDIT_LOG_TABLE}.session_id = '{safe_sid}' AND {settings.AUDIT_LOG_TABLE}.user_id = '{safe_user}' LIMIT 1"
    )
    return bool(rows)
