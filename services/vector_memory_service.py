import logging
import os
import time

# Must be set before importing sentence_transformers/huggingface_hub — otherwise
# every cold start (first embed call after a server restart) does a live HTTP
# round-trip to huggingface.co to check for a newer model version even though
# it's already cached locally. Live-measured: this added ~57s to the first chat
# request after a restart, and depends on external network reachability for a
# model that never needs to change at runtime.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

logger = logging.getLogger("VectorMemoryService")

# chromadb/sentence-transformers pull in torch, which is too large to vendor
# into the Catalyst AppSail deploy bundle (Catalyst's managed Python runtime
# doesn't run pip install server-side — every dependency must be bundled by
# hand, and torch alone is several hundred MB). Deployments that skip these
# packages fall back to no-op semantic recall instead of crashing on import;
# chat still works via chat_history_service's recent-turn window, it just
# loses similarity-based recall of older buried facts. Local dev installs
# both packages via requirements.txt as usual and gets full behavior.
try:
    import chromadb
    from sentence_transformers import SentenceTransformer
    _AVAILABLE = True
except ImportError:
    logger.warning("chromadb/sentence-transformers not installed — semantic chat memory disabled, falling back to recent-turn-only context.")
    _AVAILABLE = False

# Runs entirely locally — no API key, no cloud account, no waiting on external
# credentials (unlike Voice/GCP). Confirmed via Catalyst docs research first:
# QuickML's Knowledge Base/RAG only supports manual, console-side document
# uploads — there is no API to add documents/embeddings at runtime — so there's
# no Catalyst-native way to do this. Same "approved peripheral exception"
# pattern as Voice, just for a different missing capability (dynamic semantic
# memory vs static reference documents).
_MODEL_NAME = "all-MiniLM-L6-v2"  # small (~80MB), fast on CPU, 384-dim — plenty for short chat turns
_COLLECTION_NAME = "chat_memory"
_PERSIST_DIR = "./chroma_data"

_model = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=_PERSIST_DIR)
        _collection = client.get_or_create_collection(_COLLECTION_NAME)
    return _collection


def index_message(session_id: str, role: str, message: str) -> None:
    """Embeds one chat turn and adds it to the local vector index. Called
    alongside (not instead of) chat_history_service.save_message() — Catalyst's
    ChatHistory table stays the source of truth for the full transcript (PDF
    export, audit trail); this index only exists to make old turns
    semantically searchable once a conversation outgrows the recent-window."""
    if not _AVAILABLE:
        return
    try:
        model = _get_model()
        embedding = model.encode(message).tolist()
        order = time.time()
        _get_collection().add(
            ids=[f"{session_id}:{order}"],
            embeddings=[embedding],
            documents=[message],
            metadatas=[{"session_id": session_id, "role": role, "order": order}],
        )
    except Exception as e:
        # Best-effort, same as chat-history persistence — a failed index write
        # shouldn't break the chat turn itself, it just means that one message
        # won't be semantically searchable later.
        logger.error(f"Failed to index message for semantic memory: {str(e)}")


def retrieve_relevant(session_id: str, query_text: str, top_k: int = 5, before_order: float | None = None) -> list:
    """Finds the top_k past turns in this session most semantically similar to
    query_text — this is what lets the assistant recall something said much
    earlier in a long conversation without re-reading the entire transcript on
    every turn. `before_order` excludes anything already covered by a separate
    recent-N-turns window, so the same message never appears twice in the
    prompt."""
    if not _AVAILABLE:
        return []
    try:
        collection = _get_collection()
        total = collection.count()
        if total == 0:
            return []
        model = _get_model()
        query_embedding = model.encode(query_text).tolist()
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k * 3, total),
            where={"session_id": session_id},
        )
        if not results["documents"] or not results["documents"][0]:
            return []

        # Chroma already returns these ordered by similarity (closest first) — take
        # the first top_k that pass the before_order filter, THEN sort chronologically
        # for presentation. Sorting-then-slicing (an earlier bug here) instead picks
        # the most *recent* candidates, which defeats the point of similarity search.
        rows = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if before_order is not None and meta["order"] >= before_order:
                continue
            rows.append({"role": meta["role"], "message": doc, "order": meta["order"]})
            if len(rows) >= top_k:
                break
        rows.sort(key=lambda r: r["order"])
        return rows
    except Exception as e:
        logger.error(f"Semantic memory retrieval failed: {str(e)}")
        return []


def delete_session(session_id: str) -> None:
    """Removes every indexed turn for a deleted conversation — called alongside
    chat_history_service.delete_session() so a deleted chat doesn't keep
    surfacing in a *different* session's semantic-recall results (retrieve_relevant
    already scopes by session_id, but there's no reason to keep the vectors
    around once the transcript they came from is gone)."""
    if not _AVAILABLE:
        return
    try:
        _get_collection().delete(where={"session_id": session_id})
    except Exception as e:
        logger.error(f"Failed to delete semantic memory for session {session_id}: {str(e)}")
