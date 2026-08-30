"""Multi-provider LLM abstraction.

Originally built with Zia (Zoho Catalyst) as primary everywhere and Groq/
Gemini as pure failover. **Reordered 2026-07-23, same day, on the user's
explicit live-testing decision**: with real GROQ_API_KEY/GEMINI_API_KEY in
place, live requests against this environment's Zia endpoint were taking
20-30s and frequently timing out outright (directly observed: a 26-second
turn that still ended in "I'm having trouble reaching the AI service"; the
eval harness's own Zia-primary run scored only 48% overall with FOLLOW_UP/
OUT_OF_SCOPE at 0% because Zia's OWN classification calls were timing out
before ever reaching Groq/Gemini). Zia's hosted RAG/document-KB retrieval is
still genuinely useful when it responds, so it's kept as the LAST-resort
fallback rather than removed — just no longer the thing every turn waits on
first.

Current priority, used for DIFFERENT tasks — this is a deliberate routing
choice, not an accident of whichever was implemented first:

- Groq (LPU inference hardware, several hundred tok/sec, first-class JSON
  mode) is PRIMARY for intent classification + entity extraction — fast,
  structured, English-only, internal tasks where raw throughput matters most
  and output is a fixed JSON shape the caller validates anyway.
- Gemini Flash is PRIMARY for ANSWER COMPOSITION (any language, including
  Hindi/Kannada) and is classification's second-tier fallback if Groq itself
  is rate-limited. Groq is deliberately never in the composition chain — its
  open models are not trusted for Kannada/Hindi output quality, and a
  user-facing answer is a much higher bar than a JSON classification a
  caller validates before trusting. This constraint is independent of the
  primary-provider reorder above and still applies.

Per-task routing table (exact, see chat/circuit_breaker.py for the trip/
cooldown/recovery rules that decide which providers in a chain are actually
tried first each turn):

    | Task                          | Primary | Fallback 1 | Fallback 2 |
    |--------------------------------|---------|------------|------------|
    | classification (+ extraction)  | Groq    | Gemini     | Zia        |
    | composition (any language)     | Gemini  | Zia        | —          |
    | translation                    | Gemini  | Zia        | —          |

Both fallback keys are read from GROQ_API_KEY / GEMINI_API_KEY only
(core/config.py -> .env, see .env.example for placeholders) — never
hardcoded, never committed. If either is unset, that provider's available()
is False and it's skipped in the chain (see _ordered_chain) — a deployment
with NEITHER key falls all the way back to Zia-only, this project's original
behavior, not a broken one.
"""
import json
import logging
import time
from abc import ABC, abstractmethod

import requests

from core.config import settings
from chat.zia_client import Budget, TIMEOUT_SECONDS, MAX_ATTEMPTS, BACKOFF_SECONDS, record_call, get_health
from chat.llm_client import call_llm_with_retry

logger = logging.getLogger("LLMProvider")

# Shared connection pool for every Groq/Gemini HTTP call in this module.
# Live-measured 2026-08-22: bare module-level requests.post(...) calls (what
# every call site here used before this) each internally open-and-discard
# their own throwaway requests.Session() per call — requests' own documented
# behavior for the top-level api.post() convenience function, not a bug in
# our code, but it means a fresh TCP+TLS handshake to Google's/Groq's servers
# on literally every single completion call, never reusing a warm connection.
# This was the single largest stage in the /chat/stream first-token
# breakdown (1257ms of "Gemini stream startup"). One process-lifetime
# Session (urllib3 connection pooling + keep-alive) fixes it — same requests,
# same responses, no behavior change, just a warm connection on every call
# after the first to a given host.
_http = requests.Session()


class RateLimitError(Exception):
    """Raised specifically for an HTTP 429 from a provider, distinct from a
    generic failure — chat/circuit_breaker.py uses this to pick the longer
    120s rate-limit cooldown instead of the normal 60s failure cooldown
    (rate limits reset on a time window, not on "the service recovered")."""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        """False means this provider should never be selected — e.g. Groq/
        Gemini with no API key configured. Not the same as "currently
        degraded" (chat/zia_client.is_degraded), which still returns a
        usable provider, just a slow one."""

    @abstractmethod
    def complete(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None) -> str: ...

    @abstractmethod
    def complete_json(self, prompt: str, temperature: float = 0.0, budget: Budget | None = None) -> str: ...

    @abstractmethod
    def stream(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None):
        """Yields text chunks. Wired into POST /chat/stream (see
        chat/llm_provider.rag_answer_with_failover_stream and
        api/routers/chat.py's _stream_chat_response) as of 2026-08-22."""


class ZiaProvider(LLMProvider):
    name = "zia"

    def available(self) -> bool:
        return True  # the established primary; failure surfaces per-call, same as always

    def complete(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None) -> str:
        return call_llm_with_retry(prompt, temperature, budget=budget)

    def complete_json(self, prompt: str, temperature: float = 0.0, budget: Budget | None = None) -> str:
        # Zia's endpoint has no dedicated JSON-mode API parameter — every
        # classification/extraction prompt in this project already asks for
        # JSON via instruction text alone (see chat/router.py's
        # _CLASSIFY_PROMPT), so this is identical to complete() by design,
        # not a missing feature.
        return call_llm_with_retry(prompt, temperature, budget=budget)

    def stream(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None):
        # Not confirmed to support incremental SSE streaming — yields the
        # full response as a single chunk rather than pretending to a
        # token-by-token behavior that isn't actually happening.
        yield self.complete(prompt, temperature, budget)


_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    """Classification/extraction fallback ONLY — never call this for answer
    composition. See module docstring's routing table; enforced structurally
    by _TASK_CHAINS below, not just by convention."""
    name = "groq"

    def available(self) -> bool:
        return bool(settings.GROQ_API_KEY)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}

    def _call(self, prompt: str, temperature: float, timeout: float, json_mode: bool) -> str:
        if not self.available():
            raise Exception("GROQ_API_KEY is not configured")
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        start = time.monotonic()
        try:
            response = _http.post(_GROQ_ENDPOINT, json=payload, headers=self._headers(), timeout=timeout)
        except Exception:
            record_call(None, provider="groq")
            raise
        latency_ms = (time.monotonic() - start) * 1000
        if response.status_code == 429:
            record_call(None, provider="groq")
            raise RateLimitError(f"Groq rate limit (429): {response.text}")
        if response.status_code != 200:
            record_call(None, provider="groq")
            raise Exception(f"Groq API error {response.status_code}: {response.text}")
        record_call(latency_ms, provider="groq")
        return response.json()["choices"][0]["message"]["content"].strip()

    def _call_with_retry(self, prompt: str, temperature: float, budget: Budget | None, json_mode: bool) -> str:
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if budget is not None and budget.exceeded():
                logger.warning(f"Groq call abandoned after {attempt - 1} attempt(s) — request time budget exhausted")
                break
            timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
            if timeout <= 0:
                break
            try:
                return self._call(prompt, temperature, timeout, json_mode)
            except RateLimitError:
                raise  # never worth retrying within the same turn — surfaces immediately so the caller can fail over
            except Exception as e:
                last_error = e
                logger.warning(f"Groq call attempt {attempt}/{MAX_ATTEMPTS} failed ({e}), retrying")
                if attempt < MAX_ATTEMPTS:
                    backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                    if budget is None or budget.remaining() > backoff:
                        time.sleep(backoff)
        raise last_error or Exception("Groq call abandoned: request time budget exhausted before a usable response")

    def complete(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None) -> str:
        return self._call_with_retry(prompt, temperature, budget, json_mode=False)

    def complete_json(self, prompt: str, temperature: float = 0.0, budget: Budget | None = None) -> str:
        return self._call_with_retry(prompt, temperature, budget, json_mode=True)

    def stream(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None):
        # Groq's API genuinely supports SSE streaming (stream=True) — real
        # incremental yielding, unlike ZiaProvider's best-effort single chunk.
        if not self.available():
            raise Exception("GROQ_API_KEY is not configured")
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "stream": True,
        }
        timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
        with _http.post(_GROQ_ENDPOINT, json=payload, headers=self._headers(), timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                raise Exception(f"Groq API error {response.status_code}: {response.text}")
            for line in response.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                data = line[len(b"data: "):]
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"].get("content")
                if delta:
                    yield delta


class GeminiProvider(LLMProvider):
    """Composition fallback (any language) + classification's 2nd-tier
    fallback (see module docstring's routing table). Google AI Studio's REST
    API — auth via a `key=` query param, not a header, per Google's own
    documented shape (different from every other provider in this file)."""
    name = "gemini"

    # Google partitions free-tier quota PER MODEL NAME, not per key/project
    # (live-verified 2026-07-23 — see core/config.py's GEMINI_MODEL comment:
    # one alias had a confirmed-zero quota, another was exhausted mid-
    # session by this project's own testing while a third sibling model
    # still had headroom). A 429 on the configured model means THAT model's
    # daily allowance is spent, not that Gemini itself is down — trying one
    # sibling model before giving up on the whole provider turns a hard
    # quota wall into a soft one. Kept short and specific (real,
    # confirmed-working candidates from this session, not a guess at the
    # full model catalog).
    _FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest"]

    def _endpoint(self, method: str, model: str | None = None) -> str:
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model or settings.GEMINI_MODEL}:{method}"

    def available(self) -> bool:
        return bool(settings.GEMINI_API_KEY)

    def _call(self, prompt: str, temperature: float, timeout: float, json_mode: bool, model: str | None = None) -> str:
        if not self.available():
            raise Exception("GEMINI_API_KEY is not configured")
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        start = time.monotonic()
        try:
            response = _http.post(
                self._endpoint("generateContent", model),
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
        except Exception:
            record_call(None, provider="gemini")
            raise
        latency_ms = (time.monotonic() - start) * 1000
        if response.status_code == 429:
            record_call(None, provider="gemini")
            raise RateLimitError(f"Gemini rate limit (429) on model={model or settings.GEMINI_MODEL}: {response.text}")
        if response.status_code != 200:
            record_call(None, provider="gemini")
            raise Exception(f"Gemini API error {response.status_code}: {response.text}")
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            record_call(None, provider="gemini")
            block_reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise Exception(f"Gemini returned no candidates (blockReason={block_reason})")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            record_call(None, provider="gemini")
            finish_reason = candidates[0].get("finishReason")
            raise Exception(f"Gemini returned an empty response (finishReason={finish_reason})")
        record_call(latency_ms, provider="gemini")
        return text

    def _call_with_retry(self, prompt: str, temperature: float, budget: Budget | None, json_mode: bool) -> str:
        models_to_try = [settings.GEMINI_MODEL] + [m for m in self._FALLBACK_MODELS if m != settings.GEMINI_MODEL]
        last_error = None
        for model in models_to_try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if budget is not None and budget.exceeded():
                    logger.warning(f"Gemini call abandoned after {attempt - 1} attempt(s) on model={model} — request time budget exhausted")
                    break
                timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
                if timeout <= 0:
                    break
                try:
                    return self._call(prompt, temperature, timeout, json_mode, model=model)
                except RateLimitError as e:
                    last_error = e
                    logger.warning(f"Gemini model={model} rate-limited, trying next model if any: {e}")
                    break  # no point retrying THIS model — move to the next one in the outer loop
                except Exception as e:
                    last_error = e
                    logger.warning(f"Gemini call attempt {attempt}/{MAX_ATTEMPTS} on model={model} failed ({e}), retrying")
                    if attempt < MAX_ATTEMPTS:
                        backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                        if budget is None or budget.remaining() > backoff:
                            time.sleep(backoff)
            if budget is not None and budget.exceeded():
                break
        raise last_error or Exception("Gemini call abandoned: request time budget exhausted before a usable response")

    def complete(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None) -> str:
        return self._call_with_retry(prompt, temperature, budget, json_mode=False)

    def complete_json(self, prompt: str, temperature: float = 0.0, budget: Budget | None = None) -> str:
        return self._call_with_retry(prompt, temperature, budget, json_mode=True)

    def stream(self, prompt: str, temperature: float = 0.2, budget: Budget | None = None):
        # Gemini genuinely supports SSE streaming (streamGenerateContent?alt=sse)
        # — real incremental yielding, same as GroqProvider's.
        if not self.available():
            raise Exception("GEMINI_API_KEY is not configured")
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": temperature}}
        timeout = budget.call_timeout() if budget is not None else TIMEOUT_SECONDS
        with _http.post(
            self._endpoint("streamGenerateContent"),
            params={"key": settings.GEMINI_API_KEY, "alt": "sse"},
            json=payload, timeout=timeout, stream=True,
        ) as response:
            if response.status_code != 200:
                raise Exception(f"Gemini API error {response.status_code}: {response.text}")
            for line in response.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                chunk = json.loads(line[len(b"data: "):])
                for cand in chunk.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]


ZIA = ZiaProvider()
GROQ = GroqProvider()
GEMINI = GeminiProvider()

_PROVIDERS: dict[str, LLMProvider] = {"zia": ZIA, "groq": GROQ, "gemini": GEMINI}

# The exact per-task chains from the module docstring's routing table. Order
# matters: index 0 is the "primary" for fallback_reason purposes (see
# complete_with_failover) — whichever provider a task's chain names first is
# the one a non-null fallback_reason is explaining the absence of.
_TASK_CHAINS: dict[str, list[str]] = {
    "classification": ["groq", "gemini", "zia"],
    "composition": ["gemini", "zia"],   # Groq deliberately excluded — see module docstring
    "translation": ["gemini", "zia"],   # Groq deliberately excluded — same reason
}


def get_provider(name: str) -> LLMProvider:
    return _PROVIDERS[name]


def task_primary(task: str) -> str:
    return _TASK_CHAINS[task][0]


def _ordered_chain(task: str) -> list[LLMProvider]:
    """Providers for this task, available ones only, closed-breaker (healthy)
    providers first — a tripped provider is pushed to the back rather than
    removed entirely, so a chain with everything tripped still gets one last
    real attempt (graceful-degradation requirement) instead of failing
    instantly with nothing tried at all."""
    from chat.circuit_breaker import is_open

    names = _TASK_CHAINS.get(task, ["zia"])
    candidates = [_PROVIDERS[n] for n in names if n in _PROVIDERS and _PROVIDERS[n].available()]
    return sorted(candidates, key=lambda p: is_open(p.name))


def _is_rate_limit(exc: Exception) -> bool:
    return isinstance(exc, RateLimitError)


def complete_with_failover(
    task: str, prompt: str, temperature: float, budget: Budget | None = None, json_mode: bool = False,
) -> tuple[str, str, str | None, float]:
    """Generic failover entry point for classification/extraction/aggregate-
    composition calls, walking the task's full chain (see _TASK_CHAINS)
    rather than a single primary+fallback pair — a chain with everything
    else tripped still gets a real attempt at its last entry instead of
    failing outright the moment the first provider is down.

    Every attempt (success or failure) is fed to the circuit breaker
    (chat/circuit_breaker.record_result) here, in the one shared place every
    classification/aggregate-composition call already passes through, rather
    than duplicating that bookkeeping at every call site.

    Returns (result_text, provider_name_used, fallback_reason, latency_ms).
    fallback_reason is None when the task's chain-primary (e.g. Zia)
    answered this call directly, else a short human-readable reason (the
    primary's own error, or "circuit breaker open" if it was skipped without
    being tried this turn) — surfaced in ChatResponse for the explainable-AI/
    accountability requirement, not just logged. latency_ms times only the
    winning call, not any earlier failed attempts."""
    from chat.circuit_breaker import record_result

    chain = _ordered_chain(task)
    if not chain:
        raise Exception(f"No provider available for task={task}")

    call = (lambda p, b: p.complete_json(prompt, temperature, b)) if json_mode else (lambda p, b: p.complete(prompt, temperature, b))

    errors: dict[str, str] = {}
    primary = task_primary(task)
    for i, provider in enumerate(chain):
        # Give this attempt a fair fraction of what's left, not the whole
        # remaining budget — otherwise this provider's own internal retry
        # loop can burn the entire request budget and leave nothing for the
        # next provider in the chain (live-verified real bug, see
        # Budget.child's docstring). The last provider in the chain always
        # gets everything that's left (fraction=1).
        call_budget = budget.child(1 / (len(chain) - i)) if budget is not None else None
        try:
            start = time.monotonic()
            result = call(provider, call_budget)
            latency_ms = (time.monotonic() - start) * 1000
            record_result(provider.name, True)
            if provider.name == primary:
                reason = None
            elif primary in errors:
                reason = f"{primary} failed: {errors[primary]}"
            else:
                reason = f"{primary} unavailable (circuit breaker open)"
            return result, provider.name, reason, latency_ms
        except Exception as e:
            errors[provider.name] = str(e)
            logger.warning(f"{provider.name} failed for task={task}: {e}")
            record_result(provider.name, False, is_rate_limit=_is_rate_limit(e))
        if budget is not None and budget.exceeded():
            break

    raise Exception(errors.get(primary) or f"All providers failed for task={task}: {errors}")


def rag_answer_with_failover(final_query: str, budget: "Budget | None" = None) -> tuple[dict, str, str | None, float]:
    """Composition-task failover for the grounded RAG answer path
    (api/routers/chat.py's _grounded_answer). Zia is the only provider with
    access to Catalyst's own uploaded-document Knowledge Base
    (services.zoho_service.ask_zoho_rag); every other provider in the
    composition chain answers directly from `final_query`, which already
    carries whatever local grounding context (case DB data, MO analysis,
    conversation history) the caller built into it — that grounding survives
    a failover intact (requirement: "grounding must not weaken on
    failover"). The one honest gap is Zia's own document-KB retrieval
    specifically, which no other provider can reach — citations stay empty
    on failover rather than fabricated, never silently presented as
    document-backed.

    Groq is structurally excluded (composition uses the "composition" task
    chain, which never includes it — see module docstring).

    Returns (rag_result_dict, provider_name_used, fallback_reason,
    latency_ms) — same fallback_reason/latency_ms contract as
    complete_with_failover."""
    from chat.circuit_breaker import record_result
    from services.zoho_service import ask_zoho_rag

    chain = _ordered_chain("composition")
    if not chain:
        raise Exception("No composition provider available")

    errors: dict[str, str] = {}
    primary = task_primary("composition")
    for i, provider in enumerate(chain):
        # See complete_with_failover's matching comment / Budget.child's
        # docstring — without this, Zia's own retry loop can burn the whole
        # request budget and Gemini never gets a real turn.
        call_budget = budget.child(1 / (len(chain) - i)) if budget is not None else None
        try:
            start = time.monotonic()
            if provider.name == "zia":
                result = ask_zoho_rag(final_query, budget=call_budget)
            else:
                result = {"answer": provider.complete(final_query, temperature=0.2, budget=call_budget), "citations": []}
            latency_ms = (time.monotonic() - start) * 1000
            record_result(provider.name, True)
            if provider.name == primary:
                reason = None
            elif primary in errors:
                reason = f"{primary} failed: {errors[primary]}"
            else:
                reason = f"{primary} unavailable (circuit breaker open)"
            return result, provider.name, reason, latency_ms
        except Exception as e:
            errors[provider.name] = str(e)
            logger.warning(f"{provider.name} composition call failed: {e}")
            record_result(provider.name, False, is_rate_limit=_is_rate_limit(e))
        if budget is not None and budget.exceeded():
            break

    raise Exception(errors.get(primary) or f"All composition providers failed: {errors}")


def rag_answer_with_failover_stream(final_query: str, budget: "Budget | None" = None):
    """Streaming counterpart to rag_answer_with_failover() — same composition
    chain (Gemini -> Zia, Groq structurally excluded, same reasoning as the
    blocking version) and same grounding contract (final_query already
    carries whatever local context the caller built in — see
    api/routers/chat.py's _build_grounded_query), but yields incrementally
    instead of returning once at the end, for /chat/stream's SSE response.

    Yields dicts of one of two shapes:
      {"type": "token", "text": "..."} — a chunk of the answer as it's
        generated (real, provider-native streaming from Gemini; a single
        chunk carrying the whole answer if Zia ends up answering instead,
        since Zia's endpoint has no incremental API to relay — same honest
        distinction chat/llm_provider.LLMProvider.stream()'s own docstrings
        already draw).
      {"type": "done", "citations": [...], "provider_used": ..., "fallback_reason": ...,
        "latency_ms": ...} — exactly once, last, after all token chunks.

    Failover is only attempted BEFORE any token has been yielded for this
    turn — once Gemini has started streaming real content to the user,
    switching providers mid-stream would mean discarding partially-shown
    text (worse UX than just letting a rare mid-stream failure surface as an
    honest error). This mirrors the non-streaming version's per-provider
    budget slicing for the pre-first-token case; once streaming has begun,
    the budget/failover machinery is no longer in play for this turn."""
    from chat.circuit_breaker import record_result
    from services.zoho_service import ask_zoho_rag

    chain = _ordered_chain("composition")
    if not chain:
        raise Exception("No composition provider available")

    errors: dict[str, str] = {}
    primary = task_primary("composition")
    for i, provider in enumerate(chain):
        call_budget = budget.child(1 / (len(chain) - i)) if budget is not None else None
        started_streaming = False
        try:
            start = time.monotonic()
            if provider.name == "zia":
                result = ask_zoho_rag(final_query, budget=call_budget)
                started_streaming = True  # about to yield — no more failover past this point
                yield {"type": "token", "text": result["answer"]}
                citations = result["citations"]
            else:
                chunks = []
                for chunk in provider.stream(final_query, temperature=0.2, budget=call_budget):
                    started_streaming = True  # first chunk landed — committed to this provider now
                    chunks.append(chunk)
                    yield {"type": "token", "text": chunk}
                citations = []
                if not chunks:
                    raise Exception(f"{provider.name} stream produced no content")
            latency_ms = (time.monotonic() - start) * 1000
            record_result(provider.name, True)
            if provider.name == primary:
                reason = None
            elif primary in errors:
                reason = f"{primary} failed: {errors[primary]}"
            else:
                reason = f"{primary} unavailable (circuit breaker open)"
            yield {"type": "done", "citations": citations, "provider_used": provider.name,
                   "fallback_reason": reason, "latency_ms": latency_ms}
            return
        except Exception as e:
            errors[provider.name] = str(e)
            logger.warning(f"{provider.name} streaming composition call failed: {e}")
            record_result(provider.name, False, is_rate_limit=_is_rate_limit(e))
            if started_streaming:
                # Already shown the user real (partial) text from this provider —
                # raising here surfaces as an honest stream-level error rather
                # than silently retrying a different provider from scratch,
                # which would either duplicate or contradict what's already on
                # screen. See this function's docstring.
                raise
        if budget is not None and budget.exceeded():
            break

    raise Exception(errors.get(primary) or f"All composition providers failed: {errors}")


def translate_with_failover(
    text: str, source_lang: str, target_lang: str, emphasize: bool = False, budget: Budget | None = None,
) -> tuple[str, str, str | None, float]:
    """Returns (translated_text, provider_name, fallback_reason, latency_ms).
    Translation uses the same "composition" chain — Zia then Gemini, Groq
    excluded (per the routing table: translation output can be Hindi/
    Kannada, same reasoning as answer composition).

    Zia's path reuses services.zia_service.translate_text exactly as before
    (its retry/backoff/</think>-stripping/refusal-detection are Zia-model-
    specific and stay there, unchanged). Gemini's path is an equivalent
    translation prompt with no Zia-specific post-processing needed."""
    from chat.circuit_breaker import record_result
    from services.zia_service import translate_text as zia_translate

    def _via_zia(b):
        return zia_translate(text, source_lang, target_lang, emphasize=emphasize, budget=b)

    def _via_gemini(b):
        emphasis_note = (
            " Your entire response must be written using ONLY the target language's own script — "
            "no English words, no Latin-script transliteration, no mixed script of any kind."
            if emphasize else ""
        )
        prompt = (
            f"Translate the following {source_lang} text into {target_lang}.{emphasis_note} "
            f"Reply with ONLY the translated text, nothing else.\n\nText: {text}"
        )
        return GEMINI.complete(prompt, temperature=0.1, budget=b)

    calls = {"zia": _via_zia, "gemini": _via_gemini}
    chain = _ordered_chain("translation")
    if not chain:
        raise Exception("No translation provider available")

    errors: dict[str, str] = {}
    primary = task_primary("translation")
    for i, provider in enumerate(chain):
        # See complete_with_failover's matching comment / Budget.child's
        # docstring — without this, Zia's own retry loop can burn the whole
        # request budget and Gemini never gets a real turn.
        call_budget = budget.child(1 / (len(chain) - i)) if budget is not None else None
        try:
            start = time.monotonic()
            result = calls[provider.name](call_budget)
            latency_ms = (time.monotonic() - start) * 1000
            record_result(provider.name, True)
            if provider.name == primary:
                reason = None
            elif primary in errors:
                reason = f"{primary} failed: {errors[primary]}"
            else:
                reason = f"{primary} unavailable (circuit breaker open)"
            return result, provider.name, reason, latency_ms
        except Exception as e:
            errors[provider.name] = str(e)
            logger.warning(f"{provider.name} translation failed: {e}")
            record_result(provider.name, False, is_rate_limit=_is_rate_limit(e))
        if budget is not None and budget.exceeded():
            break

    raise Exception(errors.get(primary) or f"All translation providers failed: {errors}")
