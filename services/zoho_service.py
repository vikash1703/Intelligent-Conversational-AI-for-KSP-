import logging
import time

import requests
from core.config import settings
from services.auth_service import TokenManager # TokenManager ko import karo
from chat.zia_client import TIMEOUT_SECONDS, MAX_ATTEMPTS, BACKOFF_SECONDS, Budget, record_call

logger = logging.getLogger("ZohoService")

# How much of a retrieved document chunk to surface as a citation excerpt —
# enough to show the reader where the fact came from, not the whole chunk.
_CITATION_EXCERPT_LENGTH = 220


def _call_rag_api(question: str, timeout: float) -> dict:
    token = TokenManager.get_token()

    headers = {
        "CATALYST-ORG": settings.ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {token}",
        "Environment": settings.CATALYST_ENVIRONMENT,
        "Content-Type": "application/json"
    }

    payload = {
        "query": question,
        "documents": settings.ZOHO_DOCUMENT_IDS
    }

    start = time.monotonic()
    try:
        response = requests.post(settings.ZOHO_RAG_ENDPOINT, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        record_call(None)
        raise Exception(f"Network error while connecting to Zoho: {str(e)}")
    latency_ms = (time.monotonic() - start) * 1000

    if response.status_code != 200:
        record_call(None)
        raise Exception(f"Zoho API Error {response.status_code}: {response.text}")
    record_call(latency_ms)

    data = response.json()
    if "response" not in data:
        raise Exception("Invalid response structure from Zoho RAG API")

    citations = [
        {
            "source": "document",
            "document_title": node.get("document_title"),
            "document_id": node.get("document_id"),
            "excerpt": (node.get("content") or "")[:_CITATION_EXCERPT_LENGTH].strip(),
        }
        for node in data.get("retrieved_nodes", [])
    ]

    return {"answer": data["response"], "citations": citations}


def ask_zoho_rag(question: str, budget: "Budget | None" = None) -> dict:
    """Returns {"answer": str, "citations": [...]}. Citations are built from Zoho's
    `retrieved_nodes` — populated only when the answer was actually grounded in an
    uploaded Knowledge Base document (verified live: asking about an IPC section
    returns a node with document_title/document_id/content; a question answered from
    injected DB-context text instead returns an empty list, since that text was never
    an uploaded document). Callers that inject their own case/accused data should add
    a database-source citation on top of this — see insight_service/similarity_service.

    Previously a single attempt with no retry at all — the one call in this whole
    Zia-calling pipeline that had zero resilience to a transient timeout. Now retries
    on network/HTTP failure the same MAX_ATTEMPTS/backoff policy every other Zia
    caller uses (chat/zia_client.py), and accepts an optional request-level `budget`
    so it never blows past the whole turn's time ceiling on its own."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if budget is not None and budget.exceeded():
            logger.warning(f"RAG call abandoned after {attempt - 1} attempt(s) — request time budget exhausted")
            break
        timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
        if timeout <= 0:
            break
        try:
            return _call_rag_api(question, timeout=timeout)
        except Exception as e:
            last_error = e
            logger.warning(f"RAG call attempt {attempt}/{MAX_ATTEMPTS} failed ({e}), retrying")
            if attempt < MAX_ATTEMPTS:
                backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                if budget is None or budget.remaining() > backoff:
                    time.sleep(backoff)

    raise last_error or Exception("RAG call abandoned: request time budget exhausted before a usable response")
