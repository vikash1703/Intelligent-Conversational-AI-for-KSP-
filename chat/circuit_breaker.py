"""Per-provider circuit breaker for the Zia -> Groq/Gemini automatic failover
(see chat/llm_provider.py). Each of "zia"/"groq"/"gemini" gets its own
independent breaker state — one provider tripping never affects another's
eligibility, so e.g. Groq being rate-limited doesn't also push Gemini to the
back of any chain.

Trip conditions (either is enough on its own):
  - 2 consecutive call failures for that provider, OR
  - that provider's rolling p95 latency exceeds 15s, OR
  - a 429 rate-limit response (chat/llm_provider.RateLimitError).

Cooldown: 60s for a normal failure/latency trip, 120s for a rate-limit trip
(rate limits reset on a time window, not on "the service recovered" — a
longer wait is more likely to actually clear it). After cooldown, one
lightweight probe request is made before the provider is restored as a
healthy chain member again; a failed probe extends the cooldown and stays on
fallback rather than flip-flopping.

Every trip, failed recovery probe, and restore is logged to AuditLog
(services.audit_service.log_provider_event) — this changes which provider
answers real user questions, so it needs a trace, not silent state.

In-process, module-level state (same convention as chat/zia_client.py's
rolling health history) — resets on restart, which is correct: whether a
provider is currently unhealthy is a live, current-process question, not
something that should persist stale across a restart.
"""
import logging
import os
import time

from chat.zia_client import get_health

logger = logging.getLogger("CircuitBreaker")

# Process-level test hooks for eval/run_chat_eval.py's 4-scenario verification
# run (requirement 7: Zia-primary / Zia-tripped / Zia+Groq-tripped / all-
# tripped) — set before starting the server (not toggleable at runtime from
# outside the process, deliberately: this is for a separately-launched eval
# server, not a production runtime switch). Comma-separated provider names,
# e.g. FORCE_OPEN_PROVIDERS=zia,groq. FORCE_FALLBACK_FOR_TESTING=1 is kept as
# a backward-compatible alias for FORCE_OPEN_PROVIDERS=zia (the only scenario
# that existed before Gemini was added).
_env_forced = {p.strip() for p in os.getenv("FORCE_OPEN_PROVIDERS", "").split(",") if p.strip()}
if os.getenv("FORCE_FALLBACK_FOR_TESTING") == "1":
    _env_forced.add("zia")
_FORCE_OPEN_PROVIDERS: set[str] = _env_forced

COOLDOWN_SECONDS = 60
RATE_LIMIT_COOLDOWN_SECONDS = 120
P95_THRESHOLD_MS = 15000
CONSECUTIVE_FAILURE_THRESHOLD = 2


class _BreakerState:
    __slots__ = ("state", "consecutive_failures", "opened_at", "open_reason")

    def __init__(self):
        self.state = "closed"  # "closed" = healthy/primary-eligible; "open" = tripped to fallback
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self.open_reason: str | None = None  # "failure" | "rate_limit" — decides cooldown length


_states: dict[str, _BreakerState] = {}


def _get(provider: str) -> _BreakerState:
    return _states.setdefault(provider, _BreakerState())


def _log(event: str, reason: str, provider: str) -> None:
    logger.warning(f"Circuit breaker [{provider}] {event}: {reason}")
    # Local import — services.audit_service pulls in core.catalyst_client,
    # which this low-level module has no other reason to depend on, and
    # importing it at module load time risks a cycle if audit_service (or
    # anything it imports) ever needs chat/circuit_breaker in the future.
    from services.audit_service import log_provider_event
    log_provider_event(event, reason, provider=provider)


def record_result(provider: str, success: bool, is_rate_limit: bool = False) -> None:
    """Called from every chat/llm_provider.py failover function after each
    real provider attempt — the single shared place this bookkeeping
    happens, rather than duplicated at each call site. A success also runs
    the latency-trip check (below) so a provider that's technically
    succeeding but consistently slow still trips, not just one that's
    outright failing."""
    st = _get(provider)
    if success:
        st.consecutive_failures = 0
        _check_latency_trip(provider)
        return
    st.consecutive_failures += 1
    if is_rate_limit:
        _trip(provider, "rate-limited (429)", rate_limit=True)
    elif st.consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
        _trip(provider, f"{st.consecutive_failures} consecutive failures")


def _check_latency_trip(provider: str) -> None:
    st = _get(provider)
    if st.state == "open":
        return
    health = get_health(provider)
    if health.get("p95_latency_ms") and health["p95_latency_ms"] > P95_THRESHOLD_MS:
        _trip(provider, f"p95 latency {health['p95_latency_ms']:.0f}ms > {P95_THRESHOLD_MS}ms")


def check_latency_trip(provider: str = "zia") -> None:
    """Public wrapper kept for any caller that wants to force a latency check
    outside the normal record_result(success=True) path (record_result
    already runs this internally on every success, so most callers never
    need to call this directly)."""
    _check_latency_trip(provider)


def _trip(provider: str, reason: str, rate_limit: bool = False) -> None:
    st = _get(provider)
    if st.state == "open":
        return
    st.state = "open"
    st.opened_at = time.monotonic()
    st.open_reason = "rate_limit" if rate_limit else "failure"
    _log("tripped_to_fallback", reason, provider)


def _cooldown_for(st: _BreakerState) -> float:
    return RATE_LIMIT_COOLDOWN_SECONDS if st.open_reason == "rate_limit" else COOLDOWN_SECONDS


def _try_recover(provider: str) -> None:
    st = _get(provider)
    if st.state != "open" or st.opened_at is None:
        return
    if time.monotonic() - st.opened_at < _cooldown_for(st):
        return
    from chat.llm_provider import get_provider
    p = get_provider(provider)
    logger.info(f"Circuit breaker [{provider}] cooldown elapsed — probing")
    try:
        p.complete("Reply with the single word: ok", temperature=0)
        st.state = "closed"
        st.opened_at = None
        st.consecutive_failures = 0
        st.open_reason = None
        _log("restored_to_primary", "probe succeeded after cooldown", provider)
    except Exception as e:
        st.opened_at = time.monotonic()  # extend the cooldown window
        _log("probe_failed_stay_on_fallback", str(e), provider)


def is_open(provider: str) -> bool:
    """True means `provider` is currently tripped — chains reorder it to the
    back rather than removing it (see chat/llm_provider._ordered_chain).
    Checks for cooldown-elapsed recovery first, so a stale "open" state
    doesn't linger past its own cooldown window."""
    if provider in _FORCE_OPEN_PROVIDERS:
        return True
    _try_recover(provider)
    return _get(provider).state == "open"


def force_open_for_testing(provider: str = "zia") -> None:
    """Test-only hook for eval/run_chat_eval.py's forced-fallback scenarios
    (requirement 7b/c/d) — trips a breaker without needing real consecutive
    failures first. Never called from production request-handling code."""
    st = _get(provider)
    st.state = "open"
    st.opened_at = time.monotonic()
    st.open_reason = "failure"
    _log("tripped_to_fallback", "forced open for testing", provider)


# --- Backward-compatible aliases -------------------------------------------
# api/routers/chat.py and older callers used the Zia-only names before this
# module supported multiple providers. Kept as thin wrappers rather than
# rewriting every call site, since "record a Zia result" / "check Zia's own
# latency" are still meaningful, common operations even in the generalized
# multi-provider world.

def record_zia_result(success: bool) -> None:
    record_result("zia", success)
