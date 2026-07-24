"""Disk-persisted answer cache keyed by normalized question text, checked
BEFORE intent classification in send_message — a hit skips the
classification call, the regex fast-path, and the KB/RAG lookup entirely,
returning a fully-composed answer in milliseconds regardless of Zia's
current health. 7-day TTL; survives process restarts (a plain JSON file,
same convention as this project's other flat-file data — no new dependency
for what's fundamentally a small, infrequently-written key/value store).

Pre-warmed at startup (see main.py) with every IPC/BNS section in the legal
KB plus every procedure/concept question (services/legal_kb_service.
get_prewarm_questions()) — these are pure deterministic KB lookups, so
pre-warming costs no Zia calls at all, just populates the cache with answers
that were already instant to compute.
"""
import json
import logging
import re
import time
from pathlib import Path

from services.legal_kb_service import answer_legal_query, get_prewarm_questions

logger = logging.getLogger("AnswerCache")

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "cache" / "legal_kb_cache.json"
_TTL_SECONDS = 7 * 24 * 3600

_cache: dict[str, dict] = {}
_loaded = False


def normalize_question(question: str) -> str:
    """Lowercase, punctuation-stripped, whitespace-collapsed — deliberately a
    simple exact-ish key, not fuzzy/semantic matching. Catches the realistic
    variance that actually matters (capitalization, a trailing "?", double
    spaces) without pretending to understand that two differently-worded
    questions mean the same thing."""
    q = re.sub(r"[^\w\s]", "", question.lower())
    return re.sub(r"\s+", " ", q).strip()


def _load() -> None:
    global _cache, _loaded
    if _loaded:
        return
    _loaded = True
    if not _CACHE_PATH.exists():
        _cache = {}
        return
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Answer cache file unreadable, starting empty: {e}")
        _cache = {}


def _save() -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except OSError as e:
        logger.warning(f"Could not persist answer cache to disk: {e}")


def get_cached_answer(question: str) -> dict | None:
    """Returns {"answer": str, "citations": list, "intent": str} on a live
    (not-yet-expired) hit, else None. Never raises — a corrupt/missing cache
    file just means every question falls through to the real pipeline, same
    as a cold cache."""
    _load()
    entry = _cache.get(normalize_question(question))
    if entry is None:
        return None
    if time.time() - entry["cached_at"] > _TTL_SECONDS:
        return None
    return {"answer": entry["answer"], "citations": entry["citations"], "intent": entry["intent"]}


def set_cached_answer(question: str, answer: str, citations: list, intent: str, persist: bool = True) -> None:
    _load()
    _cache[normalize_question(question)] = {
        "answer": answer, "citations": citations, "intent": intent, "cached_at": time.time(),
    }
    if persist:
        _save()


def prewarm() -> int:
    """Populates the cache with every KB question up front so the most likely
    demo questions never touch Zia at all, even on a cold cache file. Safe to
    call on every process start — re-warming an already-fresh entry is a
    harmless overwrite with the same content, just a refreshed cached_at."""
    _load()
    count = 0
    for question in get_prewarm_questions():
        kb_result = answer_legal_query(question)
        if not kb_result:
            continue
        citation = {"source": "legal_kb"}
        citation.update({k: v for k, v in kb_result.items() if k != "answer"})
        set_cached_answer(question, kb_result["answer"], [citation], "LEGAL_REFERENCE", persist=False)
        count += 1
    _save()
    logger.info(f"Answer cache pre-warmed with {count} legal KB questions")
    return count
