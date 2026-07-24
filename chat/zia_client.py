"""Shared timeout/retry/backoff policy and a rolling per-provider health
tracker for every call this project makes to an LLM provider (Zia's
classification/entity-extraction/follow-up-rewriting/aggregate-answer/
translation calls, the RAG endpoint, and now Groq's fallback calls — see
chat/llm_provider.py). Previously each of chat/llm_client.py,
services/zia_service.py, and services/zoho_service.py had its own copy of
"30s timeout, retry logic" (or, for ask_zoho_rag, no retry at all), which
meant tuning this required editing three places and made total per-request
wait time unpredictable (a single call could burn 90s across 3 retries, and
nothing tracked how many calls one /chat/message turn actually made).

Live-measured against the real Zia endpoint (2026-07-22): a simple question
took 43s (one classification-call timeout+retry), and an answer-in-Hindi
request took 122s and then failed outright after all 3 retries on the
classification call alone timed out. That's the problem this module exists
to bound. The health tracker below is now keyed per-provider ("zia"/"groq")
so chat/circuit_breaker.py can compare them independently rather than one
shared pool conflating two completely different services' behavior.
"""
import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger("ZiaClient")

# Reduced from the previous 30s/3-attempts (up to 90s for one call alone).
# Trade-off, stated plainly: this endpoint's own healthy-case latency is
# documented elsewhere in this project at 7-15s, so a 12s per-attempt timeout
# WILL sometimes retry a call that was merely slow, not actually dead — that
# is an accepted cost of bounding worst-case wait time, not a free win.
TIMEOUT_SECONDS = 12
MAX_ATTEMPTS = 3  # initial attempt + 2 retries
BACKOFF_SECONDS = [1, 2]  # wait before retry 1, then before retry 2

# Whole-request ceiling — independent of any single call's own retry budget.
# Once a request's elapsed time crosses this, callers should stop spending
# further Zia calls and return the best partial result they already have
# (see api/routers/chat.py's use of Budget.exceeded()).
REQUEST_CEILING_SECONDS = 25


class Budget:
    """One instance per incoming /chat/message request, threaded through every
    downstream call so a slow first call can't silently let the whole request
    run past REQUEST_CEILING_SECONDS just because each individual call's own
    retry logic still had attempts left."""

    def __init__(self, total_seconds: float = REQUEST_CEILING_SECONDS):
        self._deadline = time.monotonic() + total_seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def exceeded(self) -> bool:
        return self.remaining() <= 0

    def call_timeout(self, default: float = TIMEOUT_SECONDS) -> float:
        """The timeout to use for the NEXT call attempt — never longer than
        what's actually left in the budget, so a single call can't blow past
        the request ceiling on its own. Returns 0 if the budget is already
        exhausted; callers must check that before attempting the call at all."""
        return max(0.0, min(default, self.remaining()))

    def child(self, fraction: float) -> "Budget":
        """A new Budget scoped to `fraction` of what's currently left in this
        one — used by chat/llm_provider.py's failover loops so one provider's
        own internal retry loop (each of ZiaProvider/GroqProvider/
        GeminiProvider retries up to MAX_ATTEMPTS times against whatever
        budget it's handed) can't silently consume the ENTIRE remaining
        request budget and starve every later provider in the chain of a
        real chance to answer.

        Live-verified this was a real bug, not a hypothetical: Zia's own
        call_llm_with_retry burned the full ~25s REQUEST_CEILING_SECONDS
        across just 2 internal retry attempts (each hitting its own 12s
        per-attempt timeout) when Zia was slow-but-not-instantly-failing —
        by the time complete_with_failover's outer loop moved on to try
        Gemini, budget.exceeded() was already True and Gemini was never
        actually attempted, defeating the whole point of the failover chain
        for exactly Zia's most common failure mode (slow, not dead).
        Allocating each chain position a fair fraction of whatever's left
        (1/providers_remaining, so the LAST provider in the chain always
        gets everything that's left) fixes this without needing any
        provider's own retry logic to know it's part of a chain at all."""
        return Budget(self.remaining() * fraction)


# Rolling window of the last N call outcomes (latency in ms, or None for a
# failed/timed-out attempt) PER PROVIDER — a simple in-process deque, same
# convention as this project's other in-process caches (permission_service.py's
# RolePermission cache, entity_extractor.py's crime-types/date-span cache).
# Resets on process restart, which is fine: "is this provider degraded right
# now" only ever needs to reflect this process's own recent, live experience.
_HISTORY_SIZE = 20
_latency_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=_HISTORY_SIZE))

# Degraded means either calls are running much slower than Zia's own
# documented healthy baseline (7-15s), or a large fraction of recent calls
# are failing/timing out outright — either signal alone is enough, since a
# slow-but-succeeding streak and a fast-but-failing streak are both
# genuinely bad for an interactive chat turn.
_DEGRADED_AVG_LATENCY_MS = 15000
_DEGRADED_FAILURE_RATE = 0.3


def record_call(latency_ms: float | None, provider: str = "zia") -> None:
    """latency_ms is None for a failed/timed-out attempt, otherwise the
    wall-clock duration of that one attempt (not the whole retry loop) —
    called once per individual HTTP attempt, from within call_llm_with_retry/
    translate_text/ask_zoho_rag/GroqProvider, so the health signal reflects
    real network behavior rather than this app's own retry-count choices.
    `provider` defaults to "zia" so every pre-existing call site (written
    before Groq support existed) keeps working unchanged."""
    _latency_history[provider].append(latency_ms)


def get_health(provider: str = "zia") -> dict:
    history = _latency_history.get(provider)
    if not history:
        return {"degraded": False, "avg_latency_ms": None, "p95_latency_ms": None, "failure_rate": 0.0, "sample_size": 0}
    successes = sorted(v for v in history if v is not None)
    failure_rate = 1 - (len(successes) / len(history))
    avg_latency_ms = (sum(successes) / len(successes)) if successes else None
    p95_latency_ms = successes[int(len(successes) * 0.95)] if successes else None
    if successes and p95_latency_ms is None:
        p95_latency_ms = successes[-1]
    degraded = failure_rate > _DEGRADED_FAILURE_RATE or (avg_latency_ms is not None and avg_latency_ms > _DEGRADED_AVG_LATENCY_MS)
    return {
        "degraded": degraded,
        "avg_latency_ms": round(avg_latency_ms, 1) if avg_latency_ms is not None else None,
        "p95_latency_ms": round(p95_latency_ms, 1) if p95_latency_ms is not None else None,
        "failure_rate": round(failure_rate, 2),
        "sample_size": len(history),
    }


def is_degraded(provider: str = "zia") -> bool:
    return get_health(provider)["degraded"]
