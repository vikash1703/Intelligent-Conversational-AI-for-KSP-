import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from schemas.chat_dto import (
    ChatRequest, ChatResponse, ChatSessionSummary, ChatMessageOut, ChatFeedbackRequest,
    ClearLanguagePreferenceRequest,
)
from schemas.auth_dto import CurrentUser
from services.db_service import get_case_details
from services.mo_service import analyze_mo
from services.chat_history_service import (
    get_recent_messages,
    save_message,
    get_all_messages,
    list_sessions_for_user,
    session_belongs_to_user,
    delete_session,
    search_sessions_by_content,
)
from services.vector_memory_service import retrieve_relevant
from services.audit_service import (
    log_chat_query, log_intent_classification, log_chat_feedback,
    log_language_preference, get_session_language_preference,
)
from services.legal_kb_service import answer_legal_query
from services.scoring_service import get_early_warning_alerts
from services.permission_service import get_scoped_station_ids
from chat.router import classify_intent, rewrite_and_classify_follow_up
from chat.entity_extractor import get_dataset_date_span
from chat.fast_path import fast_path_classification, EMPTY_ENTITIES
from chat.zia_client import Budget
from chat.answer_cache import get_cached_answer, set_cached_answer
from chat.language import script_matches, LANGUAGE_NAMES
from chat.zcql_builder import (
    count_cases, list_cases, group_by_district, group_by_month, group_by_section, average_days_to_arrest,
    validate_crime_type, validate_district, suggest_crime_types, suggest_districts,
    validate_case_status, suggest_case_statuses,
)
from chat.llm_provider import (
    complete_with_failover, translate_with_failover, rag_answer_with_failover, rag_answer_with_failover_stream,
)
from chat.status_messages import status_text
from chat.prompts import (
    SYSTEM_PROMPT, OUT_OF_SCOPE_REPLY, LOW_CONFIDENCE_REPLY, NETWORK_QUERY_REDIRECT, is_vague_rag_answer,
    build_sources_line, strip_existing_sources_line,
)
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()
logger = logging.getLogger("ChatRouter")

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip_markdown(text: str) -> str:
    """The chat bubble renders answers as plain text, not markdown, so a model-
    added "**bold**"/"# Header"/"* bullet" shows up as literal symbols. Tried
    fixing this with a prompt instruction first — live-tested it live-broke the
    RAG endpoint's document retrieval for bare general-knowledge questions
    ("What is Section 302 vs 307?" started returning "I'm not sure..." the
    moment ANY extra sentence was added before or after the question, prefix or
    suffix). Stripping markdown from the answer after the fact avoids touching
    the prompt at all, so it can't regress retrieval."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\*\-]\s+", "", text, flags=re.MULTILINE)
    return text


# Live-verified gap: a user typing "Summarize crime number 999999999999999"
# directly in the message text — instead of using the separate "Case context"
# sidebar field the frontend provides — got the generic "I'm not sure..."
# fallback, because request.crime_no was empty; the number sitting right there
# in the question text was never looked at. CrimeNo's real format is 18 digits
# (1-digit category + 4-digit district + 4-digit station + 4-digit year +
# 5-digit serial, per the ER diagram) — extracting a standalone run of digits in
# that range from the question itself, only when crime_no wasn't already
# supplied via the dedicated field, closes that gap without changing how the
# field-based path works.
_CRIME_NO_IN_TEXT = re.compile(r"\b\d{15,20}\b")

# Deterministic pre-check, same convention as chat/fast_path.py — network/
# gang questions have zero real grounding anywhere in this pipeline (no
# intent, no entity extraction routes to network_service at all), so without
# this they fell through to the ungrounded RAG path, which improvised a
# generic "10 most recent cases" list — real data, but not an answer to what
# was actually asked. See chat/prompts.NETWORK_QUERY_REDIRECT.
_NETWORK_QUERY_RE = re.compile(
    r"\b(criminal network|network graph|show.*(the |a )?network|gang network|"
    r"network for (a |the )?gang|(most|who has the most) connections|organized crime group)\b",
    re.IGNORECASE,
)

# Maps classify_intent's free-text input_language detection ("English",
# "Hindi", "Kannada") back onto this app's existing 2-letter codes, so a
# question actually written in one of the 3 languages the frontend's own
# script-regex already fully owns (detect -> pre-translate -> reply-
# translate, all client-side, well-tested) doesn't get double-handled by
# this NEW server-side auto-language path too — see _auto_detected_lang
# below. Anything else (Tamil, French, ...) passes through as its own name;
# LANGUAGE_NAMES.get(code, code) already degrades gracefully for an unknown
# code by just using it as its own display name.
_LANGUAGE_NAME_TO_CODE = {"english": "en", "hindi": "hi", "kannada": "kn"}


def _auto_detected_lang(input_language: str | None) -> str | None:
    """Added 2026-07-23: lets a question asked in ANY language get answered
    back in that same language automatically, without an explicit "answer in
    X" instruction — classify_intent's own multilingual understanding
    detects input_language on every turn (see chat/router.py), not just the
    3 languages the frontend can recognize by Unicode script. Returns None
    for English or "unknown" (nothing to auto-translate into) and for en/hi/
    kn specifically (the frontend's existing script-detect/translate flow
    already owns those — see the module comment above), so this only ever
    fires for a genuinely new case this app couldn't handle before."""
    if not input_language:
        return None
    code = _LANGUAGE_NAME_TO_CODE.get(input_language.strip().lower())
    if code:
        return None  # en/hi/kn — frontend's own flow already handles these
    return input_language.strip()


_AGGREGATE_ANSWER_PROMPT = (
    f"{SYSTEM_PROMPT}\n\n"
    "A user asked a statistical question and the exact result has already been "
    "computed from the real CaseMaster database below — your only job is to phrase "
    "it naturally, not to compute or add anything yourself.\n\n"
    "Question: {question}\n\n"
    "Computed result (the only facts you may use):\n{description}\n\n"
    "Write a 2-5 sentence answer using ONLY the numbers given above. If a district "
    "breakdown is included below the total, mention it naturally — name which district "
    "has the highest count, not just the overall total. Do not invent, round "
    "differently, or add any case detail not listed. Do not add a \"Sources\" "
    "line yourself — one is appended separately after your answer."
)

# Used only when a language override is already active for this turn —
# produces the English answer AND its target-language rendering in the SAME
# call, instead of composing in English then making a second, separate
# translate_text() round trip. Eliminates one whole Zia call for the most
# common language-override case (AGGREGATE_QUERY). See
# _compose_aggregate_answer's docstring for the fallback if this ever fails
# to produce a valid pair.
_AGGREGATE_ANSWER_BILINGUAL_PROMPT = (
    f"{SYSTEM_PROMPT}\n\n"
    "A user asked a statistical question and the exact result has already been "
    "computed from the real CaseMaster database below — your only job is to phrase "
    "it naturally in TWO languages, not to compute or add anything yourself.\n\n"
    "Question: {question}\n\n"
    "Computed result (the only facts you may use):\n{description}\n\n"
    "Write a 2-4 sentence answer using ONLY the numbers given above, once in English "
    "and once in {target_language_name} (written entirely in {target_language_name}'s "
    "own native script — no English words, no transliteration). Do not invent, round "
    "differently, or add any case detail not listed. Do not add a \"Sources\" line in "
    "either version — one is appended separately after your answer.\n\n"
    "Reply with ONLY a single-line JSON object, no other text, no markdown code fence, "
    "in exactly this shape:\n"
    '{{"english": "<english answer>", "translated": "<{target_language_name} answer>"}}'
)


def _filters_line(
    crime_type: str | None, district: str | None, date_from: str | None, date_to: str | None,
    case_status: str | None = None,
) -> str:
    return (
        f"Filters — crime type: {crime_type or 'any'}; district: {district or 'any'}; "
        f"case status: {case_status or 'any'}; "
        f"date range: {date_from or 'unbounded'} to {date_to or 'unbounded'}."
    )


def _severity_tier(ratio: float) -> str:
    """Same 4-tier bands the Alerts page uses (frontend/src/pages/Alerts.jsx's
    tierFor()) — kept in sync so a crime type described as "Watch" here means
    the same thing as "Watch" on that page."""
    if ratio >= 1.5:
        return "Critical"
    if ratio >= 1.2:
        return "Watch"
    if ratio >= 0.75:
        return "Normal"
    return "Declining"


def _trend_context_line(
    crime_type: str | None, date_from: str | None, date_to: str | None, station_ids: list[int] | None = None,
) -> str | None:
    """When a question asks about one crime type over a ~30-day window ending
    at the dataset's latest known date (e.g. "why are X cases spiking in the
    last 30 days") this is the same recent-vs-historical-baseline comparison
    already computed for the Alerts page (services/scoring_service.
    get_early_warning_alerts) — added here so the chat answer can say whether
    the count is actually elevated and by how much, not just list raw numbers.
    Deliberately does NOT attempt a causal "why" — this dataset has no root-
    cause/motive field on a case, so that half of the question stays honestly
    unanswered; this only adds the statistical half that IS real, computed
    data. Returns None when the window doesn't look like a ~30-day recent
    query (a historical date range asking about 2020 shouldn't be compared
    against a rolling 30-day baseline computed from today) or when the crime
    type isn't found among the computed alerts."""
    if not crime_type or not date_from or not date_to:
        return None
    try:
        span_days = (datetime.strptime(date_to, "%Y-%m-%d") - datetime.strptime(date_from, "%Y-%m-%d")).days
    except ValueError:
        return None
    if not (25 <= span_days <= 35):
        return None

    alerts = get_early_warning_alerts(recent_days=30, station_ids=station_ids)
    match = next((a for a in alerts if a["crime_type"] == crime_type), None)
    if match is None or match["window_start"] > date_to or match["window_start"] < date_from:
        # window_start comparison is a loose sanity check that the alert's own
        # rolling window and this question's explicit date range are actually
        # pointing at the same period, not just coincidentally 30 days wide.
        return None

    return (
        f"Trend check for {crime_type} (last {match['window_days']} days vs its own historical average): "
        f"{match['recent_count']} recorded vs {match['expected_count']} expected — a {match['ratio']}x ratio, "
        f"classified {_severity_tier(match['ratio'])}. The case records do not capture a root cause or motive "
        f"for any change in volume — only counts and locations are available, never a reason."
    )


def _describe_result(aggregation: str, crime_type: str | None, district: str | None,
                      date_from: str | None, date_to: str | None, result: dict,
                      case_status: str | None = None) -> str:
    """Plain-text, deterministic rendering of the computed result — this is
    what the LLM in _compose_aggregate_answer is asked to phrase naturally.
    Keeping this deterministic (not itself LLM-generated) guarantees every
    number the model is allowed to talk about is real and exact."""
    filters = _filters_line(crime_type, district, date_from, date_to, case_status)
    if aggregation == "list":
        lines = [f"- {c['crime_no']} registered {c['registered_date']}: {c['brief_facts']}" for c in result["cases"]]
        return (
            f"{filters}\nTotal matching cases: {result['count']}. "
            f"Showing the {len(result['cases'])} most recently registered:\n" + "\n".join(lines)
        )
    if aggregation == "group_by_district":
        lines = [f"- {d['district']}: {d['count']}" for d in result["by_district"]]
        return f"{filters}\nTotal matching cases: {result['total']}. By district, highest first:\n" + "\n".join(lines)
    if aggregation in ("group_by_month", "trend"):
        lines = [f"- {m['month']}: {m['count']}" for m in result["by_month"]]
        return f"{filters}\nTotal matching cases: {result['total']}. By month:\n" + "\n".join(lines)
    if aggregation == "group_by_section":
        if not result["by_section"]:
            return f"{filters}\nTotal matching cases: {result['total_cases']}. No Act/Section data is linked for these cases."
        lines = [f"- {s['act']} Section {s['section']}: {s['count']}" for s in result["by_section"]]
        note = (
            f" ({result['unresolved_skipped']} additional section record(s) had no readable code on file and were excluded.)"
            if result.get("unresolved_skipped") else ""
        )
        return (
            f"{filters}\nTotal matching cases: {result['total_cases']}. Sections applied, most common first:\n"
            + "\n".join(lines) + note
        )
    if aggregation == "avg_days_to_arrest":
        return (
            f"{filters}\nTotal matching cases: {result['total_cases']}. Of these, {result['count_with_arrest_data']} "
            f"have a linked arrest date. Average time from FIR registration to arrest: {result['average_days']} days "
            f"(range: {result['min_days']}–{result['max_days']} days)."
            + (f" {result['excluded_predates_fir']} case(s) had an arrest date recorded before the FIR date — "
               "excluded as a likely data-entry issue, not a real negative duration."
               if result.get("excluded_predates_fir") else "")
        )
    # count
    return f"{filters}\nTotal matching cases: {result['count']}."


def _compose_aggregate_answer(
    question: str, description: str, resolved_lang: str | None = None, budget: "Budget | None" = None,
) -> tuple[str, str | None, str | None, str | None, float | None]:
    """Turns the deterministic result description into natural prose via the
    "composition" task chain (Gemini -> Zia, see chat/llm_provider.py's
    routing table) — NOT ask_zoho_rag(), since there's no document to
    retrieve here, only structured data to phrase (see chat/llm_client.py's
    module docstring for why the RAG endpoint is the wrong tool for this).
    Deliberately "composition", not "classification" — this call can produce
    Hindi/Kannada prose (the bilingual branch below), and Groq is never
    allowed in the composition chain (see chat/llm_provider.py's module
    docstring: its open models aren't trusted for that output quality).
    Falls back to the raw description itself if every composition provider
    fails, rather than losing the turn — a plainer-than-ideal but still
    fully correct and grounded answer beats no answer at all (requirement:
    graceful total failure never returns a 500).

    Returns (english_answer, translated_answer, provider_used,
    fallback_reason, provider_latency_ms). provider_used is None ONLY when every composition
    provider failed — the caller (_handle_aggregate_query) treats that as
    the signal to mark this turn raw_fallback=True, since english_answer is
    then `description` itself with zero LLM involvement.

    translated_answer is only ever non-None when resolved_lang is an active
    non-English override AND the bilingual composition below both succeeded
    and validated — this is what lets send_message skip a separate
    translate_text() call entirely for the common case. Any failure (call
    error, unparseable JSON, or the translated half not validating as the
    right script) falls back to the plain English-only composition below,
    exactly the behavior this function already had before — a latency
    optimization, never a correctness trade-off, since the caller still has
    its own separate-translate-call fallback for when translated_answer
    comes back None."""
    if resolved_lang and resolved_lang != "en":
        lang_name = LANGUAGE_NAMES.get(resolved_lang, resolved_lang)
        try:
            prompt = _AGGREGATE_ANSWER_BILINGUAL_PROMPT.format(
                question=question, description=description, target_language_name=lang_name,
            )
            raw, provider_used, fallback_reason, latency_ms = complete_with_failover(
                "composition", prompt, 0.2, budget=budget, json_mode=True,
            )
            match = _JSON_OBJECT_RE.search(raw)
            parsed = json.loads(match.group(0) if match else raw)
            english = _strip_markdown(parsed["english"])
            translated = _strip_markdown(parsed["translated"])
            if english.strip() and translated.strip() and script_matches(translated, resolved_lang):
                return english, translated, provider_used, fallback_reason, latency_ms
            logger.warning("Bilingual aggregate composition didn't validate, falling back to English-only + separate translate")
        except Exception as e:
            logger.warning(f"Bilingual aggregate composition failed, falling back to English-only + separate translate: {e}")

    try:
        prompt = _AGGREGATE_ANSWER_PROMPT.format(question=question, description=description)
        english, provider_used, fallback_reason, latency_ms = complete_with_failover("composition", prompt, 0.2, budget=budget)
        return _strip_markdown(english), None, provider_used, fallback_reason, latency_ms
    except Exception as e:
        logger.warning(f"Aggregate answer composition failed on every provider, falling back to the raw result description: {e}")
        return description, None, None, None, None


def _clarifying_response(message: str) -> dict:
    return {
        "answer": message, "citations": [], "sources": [], "translated_answer": None, "language_notice": None,
        "provider": None, "fallback_reason": None, "raw_fallback": False, "provider_latency_ms": None,
    }


def _handle_aggregate_query(
    question: str, entities: dict, resolved_lang: str | None = None, budget: "Budget | None" = None,
    station_ids: list[int] | None = None,
) -> dict | None:
    """Validates crime_type/district against the real whitelists, dispatches
    to the matching zcql_builder template, and composes a natural-language
    answer. `entities` is already extracted (see chat/router.py's
    classify_intent, which now extracts intent AND entities in one call —
    previously a second, separate extract_entities() call happened here).
    Returns None (the caller's signal to fall back to the legacy grounded-RAG
    flow, same as a failed intent classification) only when no usable
    aggregation type was extracted at all — every other failure mode (an
    unrecognized crime_type/district, a zero-count result) gets a real,
    helpful answer of its own rather than silently falling through.

    translated_answer in the returned dict is pre-filled (skipping a further
    separate translate call in send_message) only for the one branch that
    actually goes through an LLM call with resolved_lang threaded into it
    (_compose_aggregate_answer) — the zero-count and clarifying-response
    branches are deterministic/instant with no generation call to fold
    translation into, so those leave translated_answer None and rely on
    send_message's existing separate-translate-call fallback."""
    if entities.get("aggregation") not in (
        "count", "list", "group_by_district", "group_by_month", "trend",
        "group_by_section", "avg_days_to_arrest",
    ):
        return None

    crime_type_raw, district_raw = entities["crime_type"], entities["district"]
    date_from, date_to = entities["date_from"], entities["date_to"]
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    crime_type = validate_crime_type(crime_type_raw) if crime_type_raw else None
    if crime_type_raw and crime_type is None:
        options = suggest_crime_types(crime_type_raw)
        message = (
            f"I don't have \"{crime_type_raw}\" as a recorded crime type. "
            f"Did you mean {', '.join(options[:-1])} or {options[-1]}?"
        )
        return _clarifying_response(message)

    district = validate_district(district_raw) if district_raw else None
    if district_raw and district is None:
        options = suggest_districts(district_raw)
        message = (
            f"I don't recognize \"{district_raw}\" as a district in this dataset. "
            f"Did you mean {', '.join(options[:-1])} or {options[-1]}?" if len(options) > 1
            else f"I don't recognize \"{district_raw}\" as a district in this dataset. Did you mean {options[0]}?"
        )
        return _clarifying_response(message)

    # Two-way district comparison (added 2026-07-23) — a genuinely different
    # answer shape from every other aggregation here: two named districts
    # side by side, not a single total or a full 10-district breakdown.
    # Previously "compare X and Y" had nowhere to go but the plain
    # group_by_district/count path, which answered with the ENTIRE district
    # breakdown instead of an actual two-way comparison — technically real
    # data, but not what was asked.
    district_2_raw = entities.get("district_2")
    district_2 = validate_district(district_2_raw) if district_2_raw else None
    if district_2_raw and district_2 is None:
        options = suggest_districts(district_2_raw)
        message = (
            f"I don't recognize \"{district_2_raw}\" as a district in this dataset. "
            f"Did you mean {', '.join(options[:-1])} or {options[-1]}?" if len(options) > 1
            else f"I don't recognize \"{district_2_raw}\" as a district in this dataset. Did you mean {options[0]}?"
        )
        return _clarifying_response(message)

    # case_status (added 2026-08-24 — REAL BUG FIXED: "how many cases are
    # charge sheeted" used to silently answer with the unfiltered total
    # instead of a real, status-filtered count, because this aggregation
    # path had no concept of case status at all — see chat/zcql_builder.
    # validate_case_status's docstring). Same validate-or-clarify pattern as
    # crime_type/district above, not a silent ignore-or-guess.
    case_status_raw = entities.get("case_status")
    case_status = validate_case_status(case_status_raw) if case_status_raw else None
    if case_status_raw and case_status is None:
        options = suggest_case_statuses(case_status_raw)
        message = (
            f"I don't recognize \"{case_status_raw}\" as a case status in this dataset. "
            f"Did you mean {', '.join(options[:-1])} or {options[-1]}?" if len(options) > 1
            else f"I don't recognize \"{case_status_raw}\" as a case status in this dataset. Did you mean {options[0]}?"
        )
        return _clarifying_response(message)

    if district and district_2:
        r1 = count_cases(crime_type, district, date_from, date_to, station_ids=station_ids, case_status=case_status)
        r2 = count_cases(crime_type, district_2, date_from, date_to, station_ids=station_ids, case_status=case_status)
        earliest, latest = get_dataset_date_span()
        window = (date_from or earliest, date_to or latest)
        filters = _filters_line(crime_type, None, date_from, date_to, case_status)
        description = f"{filters}\n{district}: {r1['count']} cases\n{district_2}: {r2['count']} cases"
        answer, translated, provider_used, fallback_reason, provider_latency_ms = _compose_aggregate_answer(
            question, description, resolved_lang=resolved_lang, budget=budget,
        )
        raw_fallback = provider_used is None
        citations = [{
            "source": "database", "aggregation": "compare_districts", "crime_type": crime_type,
            "count": r1["count"] + r2["count"], "window": window,
        }]
        answer = strip_existing_sources_line(answer)
        sources_line = build_sources_line(citations)
        answer = f"{answer}\n\n{sources_line}"
        if translated:
            translated = strip_existing_sources_line(translated)
            translated = f"{translated}\n\n{sources_line}"
        return {
            "answer": answer, "citations": citations, "sources": [], "translated_answer": translated,
            "language_notice": None, "provider": provider_used, "fallback_reason": fallback_reason,
            "raw_fallback": raw_fallback, "provider_latency_ms": provider_latency_ms,
        }

    aggregation = entities["aggregation"]
    if aggregation == "count":
        result = count_cases(crime_type, district, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["count"]
    elif aggregation == "list":
        result = list_cases(crime_type, district, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["count"]
    elif aggregation == "group_by_district":
        result = group_by_district(crime_type, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["total"]
    elif aggregation == "group_by_section":
        result = group_by_section(crime_type, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["total_cases"]
    elif aggregation == "avg_days_to_arrest":
        result = average_days_to_arrest(crime_type, district, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["total_cases"]
    else:  # group_by_month or trend
        result = group_by_month(crime_type, district, date_from, date_to, station_ids=station_ids, case_status=case_status)
        count = result["total"]

    earliest, latest = get_dataset_date_span()
    window = (date_from or earliest, date_to or latest)

    if aggregation == "avg_days_to_arrest" and count > 0 and result.get("count_with_arrest_data", 0) == 0:
        # Real cases exist but none have a linked arrest record — an honest
        # "not available" (per the confidence-calibration principle), not a
        # count/district answer standing in for the timing question that was
        # actually asked.
        answer = (
            f"I have {count} matching {crime_type or ''} cases on record, but none of them have an "
            "arrest date linked in the database, so I can't compute an average time to arrest for "
            "this query. Either no arrests were made yet, or they weren't recorded with a linked "
            "case reference."
        )
        citations = [{"source": "database", "aggregation": aggregation, "count": count, "window": window}]
        return {
            "answer": answer, "citations": citations, "sources": [], "translated_answer": None, "language_notice": None,
            "provider": None, "fallback_reason": None, "raw_fallback": False, "provider_latency_ms": None,
        }

    if count == 0:
        answer = (
            f"No {crime_type or 'matching'} cases found"
            f"{f' in {district}' if district else ''}"
            f"{f' with status {case_status}' if case_status else ''}"
            f"{f' between {date_from} and {date_to}' if date_from or date_to else ''}. "
            f"Available crime types: {', '.join(suggest_crime_types())}. "
            f"Recorded cases span {earliest} to {latest}."
        )
        citations = [{"source": "database", "aggregation": aggregation, "count": 0, "window": window}]
        return {
            "answer": answer, "citations": citations, "sources": [], "translated_answer": None, "language_notice": None,
            "provider": None, "fallback_reason": None, "raw_fallback": False, "provider_latency_ms": None,
        }

    # Requirement (2026-07-23): a count/trend answer with no district
    # specified should never be JUST a flat total — always give the
    # district context an investigator would actually want. Skipped when
    # the question already asked about one specific district (nothing to
    # break down further) or already IS a district breakdown (aggregation
    # == "group_by_district" — that's this same data, not a second copy of
    # it). Best-effort: a failure here degrades to the plain description,
    # never blocks the main answer.
    if district is None and aggregation in ("count", "group_by_month", "trend"):
        try:
            breakdown = group_by_district(crime_type, date_from, date_to, station_ids=station_ids, case_status=case_status)
            top_districts = breakdown["by_district"][:5]
            if top_districts:
                result["district_breakdown"] = (
                    "District breakdown (top 5, highest first): "
                    + ", ".join(f"{d['district']} ({d['count']})" for d in top_districts)
                )
        except Exception as e:
            logger.warning(f"District-breakdown enrichment skipped: {e}")

    description = _describe_result(aggregation, crime_type, district, date_from, date_to, result, case_status)
    if result.get("district_breakdown"):
        description = f"{description}\n{result['district_breakdown']}"
    trend_line = _trend_context_line(crime_type, date_from, date_to, station_ids)
    if trend_line:
        description = f"{description}\n\n{trend_line}"
    answer, translated, provider_used, fallback_reason, provider_latency_ms = _compose_aggregate_answer(
        question, description, resolved_lang=resolved_lang, budget=budget,
    )
    # provider_used is None only when every composition provider failed this
    # turn (see _compose_aggregate_answer) — `answer` is then the raw
    # deterministic description with zero LLM involvement, requirement 6b's
    # "graceful total failure" path for aggregate queries.
    raw_fallback = provider_used is None

    citations = [{
        "source": "database", "aggregation": aggregation, "crime_type": crime_type,
        "district": district, "case_status": case_status, "count": count, "window": window,
    }]
    answer = strip_existing_sources_line(answer)
    sources_line = build_sources_line(citations)
    answer = f"{answer}\n\n{sources_line}"
    if translated:
        translated = strip_existing_sources_line(translated)
        translated = f"{translated}\n\n{sources_line}"

    sources = [c["crime_no"] for c in result["cases"]] if aggregation == "list" else []
    return {
        "answer": answer, "citations": citations, "sources": sources,
        "translated_answer": translated, "language_notice": None, "provider": provider_used,
        "fallback_reason": fallback_reason, "raw_fallback": raw_fallback, "provider_latency_ms": provider_latency_ms,
    }


def _build_history_str(session_id: str, question: str, recent: list) -> str:
    """Relevant-earlier-turns (Chroma semantic recall beyond the recent-6
    window) + the recent turns themselves, formatted into one prompt-ready
    string — unchanged from the pre-routing single-path version of this
    endpoint, just extracted so every route handler that needs conversational
    context can call it without duplicating the logic. Takes `recent` as an
    already-fetched list (see send_message) rather than re-querying Catalyst
    for it a second time."""
    recent_cutoff = None
    if recent:
        try:
            # SetAt is a UTC wall-clock string with no offset embedded (see
            # chat_history_service) — strptime alone produces a naive datetime,
            # and naive .timestamp() assumes the *local* system timezone (IST
            # here), silently shifting the cutoff by hours. Explicit UTC tzinfo
            # fixes the reference frame to match vector_memory_service's
            # time.time()-based `order`.
            recent_cutoff = datetime.strptime(recent[0]["SetAt"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, KeyError):
            recent_cutoff = None
    relevant = retrieve_relevant(session_id, question, top_k=5, before_order=recent_cutoff)

    history_parts = [f"{r['role']}: {r['message']}" for r in relevant]
    recent_parts = [f"{h['Role']}: {h['Message']}" for h in recent]
    history_str = ""
    if history_parts:
        history_str += "Relevant earlier context from this conversation:\n" + "\n".join(history_parts) + "\n\n"
    if recent_parts:
        history_str += "Recent conversation:\n" + "\n".join(recent_parts) + "\n"
    return history_str


def _format_raw_case_data(crime_no: str, db_data: dict, mo_result: dict | None) -> str:
    """Deterministic, field-labeled dump of a case record — the requirement
    6c fallback ("for case lookups, return the raw structured case data with
    field labels") for when every composition provider (Zia, Gemini) has
    failed this turn. No LLM involved at all, so this can never misstate
    what's actually in the database, only be less naturally phrased than a
    normal composed answer."""
    from services.timeline_service import get_case_status_labels

    status_id = db_data.get("CaseStatusID")
    status_name = get_case_status_labels().get(str(status_id), "Unknown") if status_id else "Unknown"

    lines = [
        "AI answer composition is currently unavailable — showing the raw case record instead:",
        "",
        f"Crime No: {db_data.get('CrimeNo', '—')}",
        f"Case No: {db_data.get('CaseNo', '—')}",
        f"Registered: {db_data.get('CrimeRegisteredDate', '—')}",
        f"Incident date: {db_data.get('IncidentFromDate', '—')}",
        f"Status: {status_name}",
        f"Brief facts: {db_data.get('BriefFacts') or 'not recorded'}",
    ]
    accused = db_data.get("accused") or []
    if accused:
        lines.append("Accused: " + "; ".join(f"{a.get('AccusedName', '—')} (age {a.get('AgeYear', '—')})" for a in accused))
    victims = db_data.get("victims") or []
    if victims:
        lines.append("Victims: " + "; ".join(f"{v.get('VictimName', '—')} (age {v.get('AgeYear', '—')})" for v in victims))
    if mo_result and mo_result.get("is_possible_series"):
        match_count = len(mo_result.get("clustered_matches") or [])
        lines.append(f"Crime pattern: possible series ({match_count} similar nearby cases).")
    return "\n".join(lines)


def _build_grounded_query(
    question: str, explicit_crime_no: str | None, history_str: str, allow_case_context: bool,
    station_ids: list[int] | None = None,
) -> dict:
    """Pure grounding/prompt-building — crime-number regex + full-record
    injection + MO-pattern enrichment + history-aware prompt assembly. No LLM
    call happens in here at all; extracted out of what used to be the first
    two-thirds of _grounded_answer() (below) specifically so /chat/stream's
    streaming path can reuse the exact same grounding logic instead of a
    second, drift-prone copy of it — this stays the single source of truth
    for "what does the composer get told" for both the streaming and
    non-streaming endpoints.

    `allow_case_context=False` is LEGAL_REFERENCE's one behavioral difference —
    never look for or inject a crime number's case data even if one is present.

    Returns a dict:
      - early_answer: set (non-None) only for the short-circuited "no such
        case" reply — caller should use this directly and never call a
        composer at all.
      - final_query, case_found, db_data, mo_result, effective_crime_no: set
        for the normal path — final_query is what a composer should be
        called with.
    """
    context_str = ""
    case_found = False
    db_data = None
    mo_result = None
    effective_crime_no = explicit_crime_no if allow_case_context else None
    _t0 = time.monotonic()  # perf-diagnostic only, see _stream_chat_response's perf() docstring

    if allow_case_context:
        if not effective_crime_no:
            match = _CRIME_NO_IN_TEXT.search(question)
            if match:
                effective_crime_no = match.group(0)

        if effective_crime_no:
            # get_case_details and analyze_mo both only depend on
            # effective_crime_no (verified: analyze_mo re-fetches the target
            # case's own CaseMaster row itself, independently — it never
            # reads get_case_details' result) — live-measured taking
            # ~1054ms + ~912ms sequentially, the two single biggest
            # pre-composition stages. Same ThreadPoolExecutor pattern
            # already used for db_service.get_case_full()'s 6 child-table
            # fetches. analyze_mo() degrades gracefully to None for a
            # crime_no with no matching case (its own early `if not
            # target_rows: return None`), so running it before case_found is
            # even known is safe — the "no such case" branch below just
            # discards the (cheap, single-query) result unused.
            def _safe_analyze_mo():
                try:
                    return analyze_mo(effective_crime_no)
                except Exception as e:
                    logger.warning(f"MO-analysis enrichment skipped for {effective_crime_no}: {e}")
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                case_future = pool.submit(get_case_details, effective_crime_no, station_ids)
                mo_future = pool.submit(_safe_analyze_mo)
                try:
                    db_data = case_future.result()
                except ValueError:
                    # Same "Invalid crime number format" the regex in db_service raises —
                    # cases.py already turns this into a 400.
                    raise AppException("Invalid crime number format", status_code=400)
                mo_result = mo_future.result()
            logger.info(f"[perf] get_case_details+analyze_mo (parallel): {round((time.monotonic() - _t0) * 1000)}ms")
            case_found = db_data is not None

            if case_found and db_data.get("CaseStatusID"):
                # Without this, the model has nothing to report except the raw
                # internal ROWID (live-verified: it printed "43437000000083215"
                # as the case status verbatim rather than a real name) — same
                # cached ROWID->name lookup the Cases-page timeline already
                # uses, just resolved before the LLM ever sees the field
                # instead of relying on the model to somehow know what an
                # opaque Catalyst id means.
                try:
                    from services.timeline_service import get_case_status_labels
                    status_name = get_case_status_labels().get(str(db_data["CaseStatusID"]))
                    if status_name:
                        db_data["CaseStatusName"] = status_name
                except Exception as e:
                    logger.warning(f"Case status label lookup skipped for {effective_crime_no}: {e}")

            if case_found and db_data.get("latitude") is not None and db_data.get("longitude") is not None:
                # CaseMaster has no district column at all (see chat/
                # zcql_builder.py's module-level comments) — a case's district
                # only exists as a nearest-centroid bucketing of its own GPS
                # point. Without this, a follow-up like "which district?" after
                # a case lookup has literally no district fact anywhere in the
                # injected context to answer from, regardless of how well the
                # follow-up itself gets resolved.
                try:
                    from chat.zcql_builder import _nearest_district
                    db_data["District"] = _nearest_district(float(db_data["latitude"]), float(db_data["longitude"]))
                except Exception as e:
                    logger.warning(f"District resolution skipped for {effective_crime_no}: {e}")

            if not case_found:
                # A crime_no that matches no real case used to still get sent to the
                # RAG endpoint, which answered with a generic, confusing "I'm not sure
                # what information you're looking for". Answering directly here, without
                # ever calling the LLM, is both faster and gives the user something they
                # can actually act on (re-check the number).
                answer = (
                    f"I couldn't find any case or accused record for crime number "
                    f"'{effective_crime_no}'. Please double-check the number and try again."
                )
                return {"early_answer": answer, "case_found": None, "db_data": None,
                         "mo_result": None, "effective_crime_no": effective_crime_no}

            # Live-verified: without an explicit null-handling instruction, the model
            # sometimes fills in a plausible-sounding default (e.g. answered "case
            # status is active" for a case whose CaseStatusID was actually None)
            # instead of reporting the field as unavailable.
            #
            # Live-verified a second, different gap via the eval harness (see
            # eval/eval_report_baseline.md): asked "what type of case is this" as a
            # follow-up, the model answered "this is an FIR (First Information
            # Report)" — technically true of every case in this dataset, so
            # substantively unhelpful — instead of naming the actual crime type
            # (Theft/Murder/etc.) sitting in BriefFacts. The extra sentence below is
            # safe to add here specifically because this is the context-HAVING
            # branch, already proven tolerant of extra instruction text (unlike the
            # bare-question branch further down, which stays untouched).
            context_str = (
                "Case database context (any field shown as None/null/empty below was not "
                "recorded for this case — say that information is not available rather than "
                "guessing, inferring, or assuming a common/default value). The BriefFacts field "
                "states this case's actual crime type (e.g. Theft, Murder, Attempt to Murder, "
                "Online Fraud) — if asked what type of case or crime this is, answer with that "
                "specific crime type, not a generic term like 'FIR'. When reporting the case's "
                "status, use CaseStatusName (a real readable name) — CaseStatusID is an internal "
                "database id with no meaning on its own and must never be shown to the user; if "
                "CaseStatusName isn't present, say the status isn't available rather than "
                "reporting the raw id:\n"
                f"{str(db_data)}\n\n"
            )
            # mo_result was already fetched above, concurrently with
            # get_case_details — surfaces the same crime-pattern/spree
            # detection built for the Insights page's "Modus Operandi" card
            # directly in chat.
            if mo_result:
                context_str += (
                    "Crime-pattern analysis for this case (based on matching crime type, "
                    "location, and timing against other cases — not Act/Section codes). "
                    "clustered_matches lists other real cases within the series-detection radius, "
                    "usable as 'related cases nearby' if the question asks for a summary:\n"
                    f"{str(mo_result)}\n\n"
                )
            # Investigator-briefing structure — only when the question is actually
            # asking for a case overview/summary (a narrow follow-up like "how old
            # is the accused" should just get a direct answer to that, not this
            # full structure every time). Safe to add here specifically because
            # this is the context-HAVING branch, already proven tolerant of extra
            # instruction text (see the comment above on the null-handling fix).
            context_str += (
                "If — and only if — the question is asking for a summary or overview of this "
                "case (e.g. \"summarize\", \"tell me about\", \"brief me on\"), structure the "
                "answer the way a senior investigating officer would brief a junior one, in this "
                "order, using only the data given above: (1) crime type and any Act/Section "
                "information given; (2) parties — accused (name, age) and victims (name, age) if "
                "present; (3) timeline — incident date, FIR registration date, arrest date if any, "
                "chargesheet status if any; (4) current case status, plus one concrete next "
                "investigative step that follows from what's actually missing or pending in the "
                "data (e.g. no arrest on file yet, chargesheet not filed); (5) related nearby cases "
                "from the crime-pattern data above, if any were given. Skip any of these five parts "
                "entirely if there's genuinely nothing to say for it — never pad with a placeholder "
                "sentence. For any other, narrower question about this case, just answer that "
                "specific question directly instead of using this structure.\n\n"
            )

    # Follow-up framing fix, live-verified: a short pronoun-style follow-up ("How
    # old are they?") with the answer sitting right there in "Recent conversation"
    # still got a generic "I'm not sure what information you're looking for" from
    # the RAG endpoint under a plainer wording. This exact phrasing answered
    # correctly 3/3 in live A/B testing — don't reword without re-testing live,
    # this model's RAG behavior is sensitive to exact phrasing in ways that aren't
    # predictable from reasoning about it alone.
    #
    # Deliberately NOT adding a "plain text, no markdown" instruction to the bare
    # (else) branch below — live-tested that ANY extra sentence added before or
    # after a short bare question breaks the RAG endpoint's document-retrieval
    # matching. Markdown is stripped from every answer after the fact instead
    # (_strip_markdown), which needs no prompt change and so has nothing to
    # regress.
    if history_str or context_str:
        final_query = (
            f"{history_str}"
            f"{context_str}"
            "The question below may be a follow-up to the conversation above and/or "
            "relate to the case database context above (if any). Answer it using only "
            "the information already provided above.\n\n"
            "Answer in plain text only — no markdown formatting (no **, *, #, or similar symbols).\n\n"
            f"Question: {question}"
        )
    else:
        final_query = question

    return {
        "early_answer": None, "final_query": final_query, "case_found": case_found,
        "db_data": db_data, "mo_result": mo_result, "effective_crime_no": effective_crime_no,
    }


def _grounded_answer(
    question: str, explicit_crime_no: str | None, history_str: str, allow_case_context: bool,
    budget: "Budget | None" = None, station_ids: list[int] | None = None,
) -> tuple[str, list, bool | None, str | None, str | None, bool, float | None]:
    """The pre-routing behavior, unchanged: crime-number regex + full-record
    injection + MO-pattern enrichment + history-aware RAG call. This is what
    CASE_LOOKUP, the AGGREGATE_QUERY stub, an unresolved FOLLOW_UP, and a
    failed/low-confidence classification all fall back to — all four are,
    structurally, "answer the way this endpoint always did before routing was
    added", so they share one implementation rather than four copies that could
    drift apart. Non-streaming caller (POST /chat/message) — see
    /chat/stream's own handler for the streaming equivalent, which calls
    _build_grounded_query() directly instead of this wrapper.

    Returns (answer, citations, case_found, provider_used, fallback_reason,
    raw_fallback, provider_latency_ms). case_found is None when this was a
    short-circuited "no such case" answer (caller should return immediately,
    no second call needed; provider_used/fallback_reason are also None
    there, since no LLM call happened), else a bool.

    provider_used/fallback_reason come from chat/llm_provider.
    rag_answer_with_failover's "composition" chain (Gemini -> Zia — Groq is
    structurally excluded from composition, see that module's docstring).

    raw_fallback=True (provider_used="raw_data") is the requirement-6c
    graceful-total-failure path: when EVERY composition provider fails AND a
    case was actually loaded (case_found), the raw structured case record is
    returned instead of an apology — never a 500, never silence about what
    happened."""
    built = _build_grounded_query(question, explicit_crime_no, history_str, allow_case_context, station_ids)
    if built["early_answer"] is not None:
        return built["early_answer"], [], None, None, None, False, None

    final_query = built["final_query"]
    case_found, db_data, mo_result = built["case_found"], built["db_data"], built["mo_result"]
    effective_crime_no = built["effective_crime_no"]

    # Composition-chain failover (Gemini -> Zia, Groq structurally excluded —
    # see chat/llm_provider.rag_answer_with_failover's docstring). Whichever
    # provider answers gets `final_query`, which already carries whatever
    # local grounding context (case DB data, MO analysis, conversation
    # history) was built above — that grounding survives a failover intact.
    try:
        rag_result, provider_used, fallback_reason, provider_latency_ms = rag_answer_with_failover(final_query, budget=budget)
    except Exception as e:
        # Requirement 6c: graceful total failure for a case lookup — every
        # composition provider failed, but the case record itself was
        # already loaded (case_found), so show it raw with field labels
        # rather than a vague apology or a 500. When there's no case data to
        # fall back on (a general/legal question with nothing case-shaped),
        # re-raise — send_message's own except block handles that honestly
        # (there's genuinely no raw data to show).
        logger.warning(f"All composition providers failed for grounded answer: {e}")
        if case_found and db_data:
            answer = _format_raw_case_data(effective_crime_no, db_data, mo_result)
            citations = [{"source": "database", "crime_no": effective_crime_no}]
            return answer, citations, case_found, "raw_data", None, True, None
        raise

    answer = _strip_markdown(rag_result["answer"])

    citations = list(rag_result["citations"])
    if case_found:
        citations.insert(0, {"source": "database", "crime_no": effective_crime_no})

    # Confidence guardrail: nothing grounded this answer at all (no case data
    # injected, no document retrieved by RAG) AND the model gave its own known
    # "nothing found" boilerplate — swap in a clean, deterministic reply
    # instead of passing that vague non-answer straight through. This function
    # never does entity extraction itself, so reaching this point with
    # case_found=False already means nothing case/entity-shaped was found —
    # "no entities were extracted" is satisfied by construction.
    if not citations and not case_found and is_vague_rag_answer(answer):
        answer = LOW_CONFIDENCE_REPLY
        citations = []

    return answer, citations, case_found, provider_used, fallback_reason, False, provider_latency_ms


def _resolve_translated_answer(answer: str, resolved_lang: str | None, budget: "Budget | None" = None) -> tuple[str | None, str | None]:
    """Translates the English `answer` into resolved_lang server-side when a
    language override is active (explicit this turn, or sticky from an
    earlier one — see send_message) — never touches `answer` itself, which
    stays the English canonical text throughout (the eval harness's keyword
    checks, and every history/RAG prompt in this file, depend on that).

    Validates the translation's own script actually matches resolved_lang
    (chat.language.script_matches) rather than trusting a 200 response is
    automatically correct; on mismatch, retries exactly once with a strongly-
    worded instruction, then falls back to English with a visible
    language_notice rather than ever silently showing the wrong language.

    Returns (translated_answer, language_notice) — both None when
    resolved_lang is None or "en" (nothing to translate). `budget`, when
    passed, caps both translation calls to whatever's left of the whole
    request's time ceiling (chat/zia_client.Budget) — if the budget is
    already exhausted by the time this runs, the call raises immediately and
    this degrades to the same honest "showing English instead" notice as any
    other translation failure, rather than pushing the request further past
    its ceiling.

    Routes through chat/llm_provider.translate_with_failover — Gemini is
    tried first, with automatic failover to Zia on failure (requirement 3c);
    Groq is structurally excluded from translation (same reasoning as answer
    composition — see chat/llm_provider.py's module docstring)."""
    if not resolved_lang or resolved_lang == "en":
        return None, None

    lang_name = LANGUAGE_NAMES.get(resolved_lang, resolved_lang)
    try:
        translated, _provider, _reason, _latency_ms = translate_with_failover(answer, "en", resolved_lang, budget=budget)
    except Exception as e:
        logger.warning(f"Language-override translation failed: {e}")
        return None, f"Could not translate this answer into {lang_name} right now — showing English instead."

    if script_matches(translated, resolved_lang):
        return translated, None

    if budget is not None and budget.exceeded():
        logger.warning(f"Skipping translation retry for {resolved_lang} — request time budget exhausted")
        return None, f"Could not produce a reliable {lang_name} translation in time — showing English instead."

    logger.warning(f"Translated answer failed script validation for {resolved_lang}, retrying with emphasis")
    try:
        retried, _provider, _reason, _latency_ms = translate_with_failover(answer, "en", resolved_lang, emphasize=True, budget=budget)
    except Exception as e:
        logger.warning(f"Language-override translation retry failed: {e}")
        return None, f"Could not produce a reliable {lang_name} translation — showing English instead."

    if script_matches(retried, resolved_lang):
        return retried, None

    logger.warning(f"Translated answer still failed script validation for {resolved_lang} after retry")
    return None, f"Could not produce a reliable {lang_name} translation after retrying — showing English instead."


# Live-verified real user complaint (2026-07-24): pasting several questions
# into one message (e.g. a numbered list) only ever got the FIRST one
# answered — the whole pipeline (classification, grounding, composition) is
# built around exactly one question per turn by design, so the rest were
# silently ignored rather than erroring. _split_multi_question detects this
# shape; _answer_multi_question (below send_message) answers each one
# through the real, unmodified single-question pipeline and combines the
# results, rather than teaching the classifier/composer to somehow handle
# several unrelated questions (different intents, different grounding needs)
# in one LLM call.
_NUMBERED_LINE_RE = re.compile(r"^\s*(?:\d+[\.\):]|[-*•])\s*(.+)$")
_MAX_BATCH_QUESTIONS = 15


def _split_multi_question(text: str) -> list[str] | None:
    """Returns a list of 2+ sub-questions if `text` looks like several
    distinct questions pasted as one message (numbered/bulleted list, or
    several bare lines each ending in "?"), else None — meaning "treat as
    one ordinary single question", the overwhelmingly common case.
    Deliberately conservative: a line only counts as its own question if
    it's either explicitly numbered/bulleted OR ends in "?", so a normal
    multi-sentence single question (which has no numbering and often no
    mid-text "?") is never mistakenly fragmented."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None

    sub_questions = []
    for line in lines:
        match = _NUMBERED_LINE_RE.match(line)
        candidate = match.group(1).strip() if match else line
        if not candidate:
            continue
        if match or candidate.endswith("?"):
            sub_questions.append(candidate)

    if len(sub_questions) < 2:
        return None
    return sub_questions[:_MAX_BATCH_QUESTIONS]


def _answer_multi_question(
    sub_questions: list[str], request: ChatRequest, http_request: Request, current_user: CurrentUser,
) -> ChatResponse:
    """Answers each sub-question through the real, unmodified send_message
    pipeline (called directly as a plain function — a FastAPI route handler
    is just a normal Python function outside the ASGI/dependency-injection
    layer, so this reuses 100% of the existing classification/grounding/
    composition/persistence logic per sub-question rather than a second,
    parallel implementation that could drift from it). Each sub-question is
    saved to ChatHistory/AuditLog individually, in the same session — the
    conversation view honestly shows all N real Q&A pairs, not one opaque
    blob, and a later FOLLOW_UP can still refer back to any of them.

    Sequential, not parallel — simpler and safer (no shared-state races
    across the many module-level in-process caches this pipeline already
    has), at the cost of total latency scaling with the number of
    questions. A batch of 10 typically takes 15-60s total at today's
    Gemini-primary latency; capped at _MAX_BATCH_QUESTIONS to bound the
    worst case."""
    session_id = request.session_id or str(uuid.uuid4())
    parts, translated_parts = [], []
    all_citations, all_sources, providers_used = [], [], []
    total_latency = 0.0
    resolved_lang = None

    for i, sub_q in enumerate(sub_questions, start=1):
        sub_request = ChatRequest(question=sub_q, session_id=session_id, crime_no=request.crime_no)
        try:
            sub_response = send_message(sub_request, http_request, current_user)
        except AppException as e:
            parts.append(f"{i}. {sub_q}\n{e.message}")
            continue
        parts.append(f"{i}. {sub_q}\n{sub_response.answer}")
        if sub_response.translated_answer:
            translated_parts.append(f"{i}. {sub_q}\n{sub_response.translated_answer}")
        all_citations.extend(sub_response.citations or [])
        all_sources.extend(sub_response.sources or [])
        if sub_response.provider_used:
            providers_used.append(sub_response.provider_used)
        total_latency += sub_response.latency_ms or 0.0
        if sub_response.response_language:
            resolved_lang = sub_response.response_language

    distinct_providers = set(providers_used)
    provider_summary = next(iter(distinct_providers)) if len(distinct_providers) == 1 else ("mixed" if distinct_providers else None)

    return ChatResponse(
        answer="\n\n".join(parts),
        session_id=session_id,
        citations=all_citations,
        sources=all_sources,
        intent="MULTI_QUESTION",
        latency_ms=round(total_latency, 1),
        response_language=resolved_lang,
        translated_answer="\n\n".join(translated_parts) if translated_parts else None,
        provider_used=provider_summary,
    )


def _classify_and_log(
    question: str, recent: list, session_id: str, current_user: CurrentUser, client_ip: str,
    budget: "Budget | None" = None, skip_llm: bool = False,
) -> dict:
    """Tries the regex fast-path first (chat/fast_path.py) — zero Zia calls
    for an unambiguous crime-number/section-number/"how many X cases" shape —
    and only falls through to the real LLM classifier when nothing matches.
    Either way, the result is logged to AuditLog the same way (confidence 1.0
    and near-zero latency for a fast-path hit is a meaningful, honest signal
    in its own right, not a placeholder), so the eval harness's AuditLog
    read-back still sees a real intent for every turn.

    `skip_llm=True` skips the LLM classifier entirely when the fast path
    doesn't match, returning the same "couldn't classify" shape a failed LLM
    call already produces — send_message's existing legacy grounded-RAG
    fallback handles that path unchanged. Not currently set from Zia's
    health (removed 2026-07-23 — see chat/llm_provider.py's module docstring
    on the Groq/Gemini-primary reorder): pre-emptively skipping the whole
    classification chain because ZIA SPECIFICALLY looked unhealthy stopped
    making sense once Zia is the classification chain's last-resort fallback,
    not its primary — Groq/Gemini answer this fine regardless of Zia's own
    health. Kept as a parameter for any future caller with a real reason to
    skip classification outright."""
    start = time.monotonic()
    fast = fast_path_classification(question)
    if fast is not None:
        latency_ms = (time.monotonic() - start) * 1000
        log_intent_classification(
            user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
            message=question, intent=fast["intent"], confidence=fast["confidence"],
            latency_ms=latency_ms, ip_address=client_ip,
        )
        return fast

    if skip_llm:
        latency_ms = (time.monotonic() - start) * 1000
        result = {"intent": None, "confidence": 0.0, "response_language": None, "cleaned_message": question, "provider": None, **EMPTY_ENTITIES}
        log_intent_classification(
            user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
            message=question, intent=None, confidence=0.0, latency_ms=latency_ms, ip_address=client_ip,
        )
        return result

    result = classify_intent(question, recent, budget=budget)
    latency_ms = (time.monotonic() - start) * 1000
    log_intent_classification(
        user_id=current_user.username,
        role_name=current_user.role.value,
        session_id=session_id,
        message=question,
        intent=result["intent"],
        confidence=result["confidence"],
        latency_ms=latency_ms,
        ip_address=client_ip,
    )
    return result


@router.post("/message", response_model=ChatResponse)
def send_message(
    request: ChatRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    # Professional validation
    if not request.question or not request.question.strip():
        raise AppException("Question cannot be empty", status_code=400)

    # Multiple questions pasted as one message (added 2026-07-24 — see
    # _split_multi_question's docstring) — answered as a batch through the
    # real single-question pipeline per question, not this function's own
    # single-question logic below at all.
    sub_questions = _split_multi_question(request.question)
    if sub_questions:
        return _answer_multi_question(sub_questions, request, http_request, current_user)

    session_id = request.session_id or str(uuid.uuid4())
    client_ip = http_request.client.host if http_request.client else "Unknown"
    turn_start = time.monotonic()
    # One budget per request, threaded through every downstream LLM call
    # (Groq/Gemini/Zia) so a slow first call can't let the whole turn run
    # past REQUEST_CEILING_SECONDS just because each call's own retry loop
    # still had attempts left (see chat/zia_client.py). No longer gated on
    # Zia's specific health (see chat/llm_provider.py's module docstring on
    # the 2026-07-23 Groq/Gemini-primary reorder) — Zia is now the LAST
    # resort in every chain, so its health alone no longer predicts whether
    # this turn's classification/composition will actually be slow.
    budget = Budget()

    def elapsed_ms() -> float:
        return round((time.monotonic() - turn_start) * 1000, 1)

    try:
        # Cache check FIRST, before any classification or LLM call at all — a
        # hit here (chat/answer_cache.py) means this exact legal-KB question
        # was pre-warmed at startup or asked before within the last 7 days,
        # and returns in milliseconds regardless of Zia's current health.
        cached = get_cached_answer(request.question)
        if cached is not None:
            sticky_lang = get_session_language_preference(session_id)
            # Real bug fix (2026-08-29): resolved_lang used to come from
            # sticky preference alone, and input_language was never returned
            # at all on a cache hit — a repeated Hindi/Kannada question
            # silently lost its detected language the second time it was
            # asked. cached["input_language"] (added alongside this fix)
            # restores the exact same resolution _auto_detected_lang already
            # uses on the non-cached path below.
            resolved_lang = sticky_lang or _auto_detected_lang(cached["input_language"])
            answer = cached["answer"]
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            log_intent_classification(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                message=request.question, intent=cached["intent"], confidence=1.0,
                latency_ms=elapsed_ms(), ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            return ChatResponse(
                answer=answer, session_id=session_id, citations=cached["citations"], intent=cached["intent"],
                latency_ms=elapsed_ms(), response_language=resolved_lang, translated_answer=translated,
                language_notice=notice, provider_used="cache", input_language=cached["input_language"],
            )

        if _NETWORK_QUERY_RE.search(request.question):
            resolved_lang = get_session_language_preference(session_id)
            answer = NETWORK_QUERY_REDIRECT
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            return ChatResponse(
                answer=answer, session_id=session_id, citations=[], intent="OUT_OF_SCOPE",
                latency_ms=elapsed_ms(), response_language=resolved_lang, translated_answer=translated,
                language_notice=notice, provider_used=None,
            )

        # Hybrid memory, not a full-transcript dump: a small recent-turns window for
        # immediate conversational flow, plus (only when actually needed — see
        # _build_history_str) a semantic-similarity search over anything older.
        # `recent` alone is what the intent classifier gets — cheap, no Chroma
        # lookup, matching classify_intent's (message, recent_history) contract.
        #
        # Three-level language resolution, priority order: (1) an explicit
        # override detected THIS turn by classify_intent below always wins and
        # becomes the new sticky preference; (2) absent that, a sticky
        # preference from an earlier turn in this same session still applies;
        # (3) absent both, resolved_lang stays None and the existing frontend
        # auto-detect/UI-toggle behavior is untouched — this endpoint simply
        # isn't asserting a language for that case.
        #
        # These two reads both take only session_id and don't touch each
        # other's data (verified: get_recent_messages reads ChatHistory,
        # get_session_language_preference reads AuditLog) — live-measured at
        # 250ms/287ms sequentially, run concurrently instead.
        with ThreadPoolExecutor(max_workers=2) as pool:
            recent_future = pool.submit(get_recent_messages, session_id, 6)
            sticky_future = pool.submit(get_session_language_preference, session_id)
            recent = recent_future.result()
            sticky_lang = sticky_future.result()

        classification = _classify_and_log(request.question, recent, session_id, current_user, client_ip, budget=budget)
        intent = classification["intent"]
        # resolved_message is cleaned_message with follow-up resolution ALSO
        # folded into the same classification call (added 2026-08-22, see
        # chat/router.py's _CLASSIFY_PROMPT "resolved_message" paragraph) —
        # for an ordinary (non-follow-up) message it's identical to
        # cleaned_message; for a follow-up, the classifier has already
        # rewritten it into a standalone question AND classified/extracted
        # entities from that standalone form, all in this one call. This is
        # what collapses a follow-up from 2 classification-type LLM calls
        # down to 1 — matching a fresh question's call count — for every
        # case the model can resolve using just the recent-turns context it's
        # already given (the common case, live-measured).
        question_for_rag = classification["resolved_message"]
        # Captured once, from the ORIGINAL message's own classification — the
        # rare FOLLOW_UP safety-net branch below operates on already-resolved
        # text (nothing left to detect a second time), so this must not be
        # overwritten by that reclassification's own (near-certainly null)
        # response_language.
        explicit_override = classification["response_language"]
        # Same reasoning, same original-classification-only capture.
        input_language = classification["input_language"]

        # entities_source is whichever classification call's entity fields
        # (crime_type/district/date_from/.../aggregation) actually apply to
        # the final resolved intent — the original classification, unless the
        # rare FOLLOW_UP safety-net branch below re-classifies, in which case
        # that reclassification's own entities are the relevant ones.
        entities_source = classification

        # Rare safety-net path: the main classification call above couldn't
        # resolve this into a standalone form even with recent-turn context
        # (see _CLASSIFY_PROMPT's resolved_message paragraph — this should be
        # uncommon, not the default outcome for a follow-up). Falls back to
        # the dedicated rewrite+reclassify call, now armed with the FULL
        # history (recent turns + Chroma semantic recall beyond that window),
        # which the main classification call doesn't have access to — a
        # second, better-informed attempt before giving up.
        follow_up_history_str = None
        if intent == "FOLLOW_UP":
            follow_up_start = time.monotonic()
            follow_up_history_str = _build_history_str(session_id, question_for_rag, recent)
            reclassification = rewrite_and_classify_follow_up(question_for_rag, follow_up_history_str, budget=budget)
            question_for_rag = reclassification["rewritten_question"]
            # Same AuditLog contract _classify_and_log's LLM-path branch already
            # writes — this call replaces what used to be a separate
            # _classify_and_log invocation, so it needs the same log entry.
            log_intent_classification(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                message=question_for_rag, intent=reclassification["intent"], confidence=reclassification["confidence"],
                latency_ms=(time.monotonic() - follow_up_start) * 1000, ip_address=client_ip,
            )
            intent = reclassification["intent"] if reclassification["intent"] != "FOLLOW_UP" else None
            entities_source = reclassification

        # An override detected this turn always wins and re-stickies the
        # session (even to the same language it was already on, which is a
        # harmless no-op write); otherwise whatever was already sticky (if
        # anything) carries forward; absent both, fall back to whatever
        # language this specific message was actually detected as being
        # written in (added 2026-07-23 — see _auto_detected_lang) so a
        # question in, say, Tamil gets answered in Tamil without the user
        # ever having to say "answer in Tamil" explicitly.
        if explicit_override:
            resolved_lang = explicit_override
            log_language_preference(
                user_id=current_user.username, role_name=current_user.role.value,
                session_id=session_id, language=explicit_override, ip_address=client_ip,
            )
        else:
            resolved_lang = sticky_lang or _auto_detected_lang(input_language)

        if intent == "OUT_OF_SCOPE":
            answer = OUT_OF_SCOPE_REPLY
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            return ChatResponse(
                answer=answer, session_id=session_id, citations=[], intent=intent, latency_ms=elapsed_ms(),
                response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                provider_used=None, input_language=input_language,
            )

        if intent == "LEGAL_REFERENCE":
            # Try the structured legal KB first — deterministic, so the
            # required output shape (title+number, plain-language meaning,
            # punishment, cognizable/bailable, BNS equivalent, disclaimer) is
            # guaranteed rather than dependent on an LLM remembering to
            # include every field. Only questions genuinely outside the KB's
            # ~100 sections / ~19 procedures fall through to the RAG endpoint
            # below (still case-data-free, per allow_case_context=False).
            kb_result = answer_legal_query(question_for_rag)
            if kb_result:
                answer = kb_result["answer"]
                citation = {"source": "legal_kb"}
                citation.update({k: v for k, v in kb_result.items() if k != "answer"})
                if sources_line := build_sources_line([citation]):
                    answer = f"{answer}\n\n{sources_line}"
                save_message(session_id, "user", request.question)
                save_message(session_id, "assistant", answer)
                log_chat_query(
                    user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                    query_text=request.question, response_text=answer, ip_address=client_ip,
                )
                # Grows the cache organically beyond the startup pre-warm set —
                # a repeat of this exact (normalized) question, or a fresh
                # process restart within the 7-day TTL, now skips
                # classification entirely too (see the cache check at the top
                # of this function).
                set_cached_answer(request.question, answer, [citation], intent, input_language=input_language)
                translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
                return ChatResponse(
                    answer=answer, session_id=session_id, citations=[citation], intent=intent, latency_ms=elapsed_ms(),
                    response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                    provider_used=None, input_language=input_language,
                )

        # Resolved once, lazily — only cache/LEGAL_REFERENCE(KB hit)/
        # OUT_OF_SCOPE turns can skip this (none of them touch case data);
        # everything reaching this point needs it for both AGGREGATE_QUERY
        # and the grounded-answer path just below. Can raise AppException
        # (403, fail-closed) for an officer whose jurisdiction isn't
        # configured — deliberately NOT caught here, propagates the same way
        # every other AppException in this function does (see the except
        # block at the bottom), rather than being swallowed into a generic
        # 500 or silently treated as unscoped.
        station_ids = get_scoped_station_ids(current_user)

        if intent == "AGGREGATE_QUERY":
            # Real ZCQL COUNT/GROUP aggregation — whitelist validation +
            # zcql_builder dispatch (see _handle_aggregate_query), using the
            # entities already extracted in the SAME call that classified the
            # intent (chat/router.py's classify_intent — no second, separate
            # extraction call anymore). Returns None only when no usable
            # aggregation type was extracted at all, in which case this falls
            # through to the same legacy grounded-RAG flow every other
            # unresolved intent uses — everything else (an unrecognized
            # crime_type/district, a zero-count result) is handled and
            # returned here directly.
            aggregate_result = _handle_aggregate_query(
                question_for_rag, entities_source, resolved_lang=resolved_lang, budget=budget, station_ids=station_ids,
            )
            if aggregate_result is not None:
                answer = aggregate_result["answer"]
                save_message(session_id, "user", request.question)
                save_message(session_id, "assistant", answer)
                log_chat_query(
                    user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                    query_text=request.question, response_text=answer, ip_address=client_ip,
                )
                # translated_answer is already resolved (folded into the same
                # composition call, see _compose_aggregate_answer) whenever
                # that succeeded — only fall back to a separate translate
                # call when it didn't (resolved_lang was None/"en" to begin
                # with, or the bilingual attempt failed/didn't validate).
                if aggregate_result["translated_answer"] is not None:
                    translated, notice = aggregate_result["translated_answer"], aggregate_result["language_notice"]
                else:
                    translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
                return ChatResponse(
                    answer=answer, session_id=session_id,
                    citations=aggregate_result["citations"], sources=aggregate_result["sources"],
                    intent=intent, latency_ms=elapsed_ms(),
                    response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                    provider_used=aggregate_result["provider"],
                    fallback_reason=aggregate_result.get("fallback_reason"),
                    raw_fallback=aggregate_result.get("raw_fallback") or None,
                    provider_latency_ms=aggregate_result.get("provider_latency_ms"),
                    input_language=input_language,
                )
            logger.info(f"AGGREGATE_QUERY had no usable aggregation extracted (confidence={classification['confidence']}) — falling back to the grounded RAG flow")

        # LEGAL_REFERENCE (KB miss), CASE_LOOKUP, an unresolved AGGREGATE_QUERY,
        # an unresolved FOLLOW_UP, and a failed classification (intent is None)
        # all need the full history string — build it once, reusing the
        # FOLLOW_UP branch's copy if that's the path that got us here instead
        # of computing it twice.
        history_str = follow_up_history_str if follow_up_history_str is not None else _build_history_str(session_id, question_for_rag, recent)

        allow_case_context = intent != "LEGAL_REFERENCE"
        try:
            answer, citations, case_found, provider_used, fallback_reason, raw_fallback, provider_latency_ms = _grounded_answer(
                question_for_rag, request.crime_no if allow_case_context else None, history_str, allow_case_context,
                budget=budget, station_ids=station_ids,
            )
        except AppException:
            raise
        except Exception as e:
            # The one call in this whole pipeline with no good fallback of its
            # own (open-ended questions have no KB/cache/deterministic answer
            # to fall back to) — this is the honest "best partial result"
            # requirement 1 asks for: rather than a generic 500 (what this
            # used to do — live-verified: a genuinely degraded Zia can
            # exhaust every retry within the tightened budget and still
            # fail), tell the user plainly what happened instead of making
            # them wait or guess why nothing came back.
            logger.warning(f"Grounded RAG answer failed after all retries, returning a degraded notice instead of a 500: {e}")
            answer = (
                "I'm having trouble reaching the AI service right now — it's responding "
                "very slowly or not at all. Please try again in a moment, or rephrase "
                "your question."
            )
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            notice = None
            if resolved_lang and resolved_lang != "en":
                lang_name = LANGUAGE_NAMES.get(resolved_lang, resolved_lang)
                notice = f"Could not respond in {lang_name} right now — the AI service is unavailable."
            return ChatResponse(
                answer=answer, session_id=session_id, citations=[], intent=intent, latency_ms=elapsed_ms(),
                response_language=resolved_lang, translated_answer=None, language_notice=notice,
                service_degraded=True, provider_used=None, input_language=input_language,
            )

        answer = strip_existing_sources_line(answer)
        if sources_line := build_sources_line(citations):
            answer = f"{answer}\n\n{sources_line}"

        if case_found is None:
            # Short-circuited "no such case" answer — already fully formed, no
            # second RAG call needed.
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            return ChatResponse(
                answer=answer, session_id=session_id, citations=[], intent=intent, latency_ms=elapsed_ms(),
                response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                provider_used=provider_used,
                fallback_reason=fallback_reason, raw_fallback=raw_fallback or None,
                provider_latency_ms=provider_latency_ms, input_language=input_language,
            )

        save_message(session_id, "user", request.question)
        save_message(session_id, "assistant", answer)
        log_chat_query(
            user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
            query_text=request.question, response_text=answer, ip_address=client_ip,
        )

        translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
        return ChatResponse(
            answer=answer, session_id=session_id, citations=citations, intent=intent, latency_ms=elapsed_ms(),
            response_language=resolved_lang, translated_answer=translated, language_notice=notice,
            provider_used=provider_used,
            fallback_reason=fallback_reason, raw_fallback=raw_fallback or None,
            provider_latency_ms=provider_latency_ms, input_language=input_language,
        )
    except AppException:
        # Already has the right status/message (e.g. CatalystQueryError's 502 for a
        # real upstream failure) — let it propagate as-is instead of flattening it
        # into a generic 500 below.
        raise
    except Exception as e:
        raise AppException(f"Chat processing failed: {str(e)}", status_code=500)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _stream_chat_response(request: ChatRequest, http_request: Request, current_user: CurrentUser):
    """Generator yielding SSE frames for POST /chat/stream — see that route's
    own docstring. Mirrors send_message()'s control flow (reusing the exact
    same helpers: _classify_and_log, _build_history_str,
    rewrite_and_classify_follow_up, _build_grounded_query,
    answer_legal_query, _handle_aggregate_query) so the two endpoints can't
    silently drift apart, rather than a second, independent implementation
    of the routing logic.

    Scope, deliberately: multi-question batching (_split_multi_question) is
    NOT handled here — a pasted multi-question message streams as one turn
    against the whole raw text. POST /chat/message remains the endpoint for
    that case (checked first, so streaming callers with a genuine batch
    still get a reasonable, if unsplit, answer rather than an error).

    reasoning_path (carried in the final "done" event) is the explainability
    trace: every stage this turn genuinely went through, in arrival order,
    each annotated with what actually happened (a real row count, not a
    filler string) — meant to be persisted client-side under the finished
    message as a collapsible trace, not thrown away once the transient
    loading state ends."""
    turn_start = time.monotonic()
    reasoning_path: list[dict] = []

    def stage(name: str, detail: str) -> None:
        reasoning_path.append({"stage": name, "detail": detail, "at_ms": round((time.monotonic() - turn_start) * 1000)})

    # Perf-diagnostic logging (server log, not the user-facing reasoning_path
    # trace above) — added while chasing the "~1.1s unaccounted for" gap
    # between the classify+retrieve estimate and measured first-token time.
    # Kept as permanent instrumentation (cheap — one monotonic() read and a
    # log line per stage) rather than throwaway prints, since pre-composition
    # latency is exactly the kind of regression that's invisible again the
    # moment nobody's actively watching for it.
    def perf(label: str) -> None:
        logger.info(f"[perf] {label}: {round((time.monotonic() - turn_start) * 1000)}ms since request start")

    def elapsed_ms() -> float:
        return round((time.monotonic() - turn_start) * 1000, 1)

    if not request.question or not request.question.strip():
        yield _sse({"type": "error", "message": "Question cannot be empty"})
        return

    session_id = request.session_id or str(uuid.uuid4())
    client_ip = http_request.client.host if http_request.client else "Unknown"
    budget = Budget()

    def done(**kwargs) -> str:
        base = {
            "type": "done", "session_id": session_id, "latency_ms": elapsed_ms(),
            "reasoning_path": reasoning_path, "citations": [], "sources": None,
            "provider_used": None, "fallback_reason": None, "raw_fallback": None,
            "provider_latency_ms": None, "response_language": None,
            "translated_answer": None, "language_notice": None, "intent": None,
            # See ChatResponse.input_language's docstring — the backend
            # classifier's own language detection, independent of script.
            # None for the pre-classification short-circuits (cache hit,
            # network-query redirect), where no classification ever ran.
            "input_language": None,
        }
        base.update(kwargs)
        return _sse(base)

    try:
        # Cache hit — already instant, no meaningful stages to show.
        cached = get_cached_answer(request.question)
        if cached is not None:
            # Same real bug fix as the non-streaming cache-hit path above —
            # see that comment for the full explanation.
            sticky_lang = get_session_language_preference(session_id)
            resolved_lang = sticky_lang or _auto_detected_lang(cached["input_language"])
            answer = cached["answer"]
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            log_intent_classification(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                message=request.question, intent=cached["intent"], confidence=1.0,
                latency_ms=elapsed_ms(), ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            yield _sse({"type": "token", "text": answer})
            yield done(answer=answer, intent=cached["intent"], citations=cached["citations"],
                       response_language=resolved_lang, translated_answer=translated,
                       language_notice=notice, provider_used="cache", input_language=cached["input_language"])
            return

        if _NETWORK_QUERY_RE.search(request.question):
            resolved_lang = get_session_language_preference(session_id)
            answer = NETWORK_QUERY_REDIRECT
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            yield _sse({"type": "token", "text": answer})
            yield done(answer=answer, intent="OUT_OF_SCOPE", response_language=resolved_lang,
                       translated_answer=translated, language_notice=notice)
            return

        perf("request_received")
        # Both take only session_id, don't touch each other's data — run
        # concurrently (see send_message's matching comment for the
        # independence verification).
        with ThreadPoolExecutor(max_workers=2) as pool:
            recent_future = pool.submit(get_recent_messages, session_id, 6)
            sticky_future = pool.submit(get_session_language_preference, session_id)
            recent = recent_future.result()
            sticky_lang = sticky_future.result()
        perf("recent_and_sticky_lang_done")

        yield _sse({"type": "status", "stage": "classifying", "text": status_text("classifying", sticky_lang)})
        classification = _classify_and_log(request.question, recent, session_id, current_user, client_ip, budget=budget)
        perf("classify_and_log_done")
        stage("classify", f"intent={classification['intent']}, confidence={classification['confidence']}")
        intent = classification["intent"]
        # resolved_message folds follow-up resolution into this same call —
        # see the matching comment in send_message() and chat/router.py's
        # _CLASSIFY_PROMPT. Collapses a follow-up to 1 classification-type
        # call (matching a fresh question) for every case the model can
        # resolve from recent-turn context alone.
        question_for_rag = classification["resolved_message"]
        explicit_override = classification["response_language"]
        input_language = classification["input_language"]
        entities_source = classification

        # Rare safety-net path — see send_message()'s matching comment.
        follow_up_history_str = None
        if intent == "FOLLOW_UP":
            yield _sse({"type": "status", "stage": "classifying", "text": status_text("classifying", sticky_lang)})
            follow_up_history_str = _build_history_str(session_id, question_for_rag, recent)
            follow_up_start = time.monotonic()
            reclassification = rewrite_and_classify_follow_up(question_for_rag, follow_up_history_str, budget=budget)
            question_for_rag = reclassification["rewritten_question"]
            log_intent_classification(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                message=question_for_rag, intent=reclassification["intent"], confidence=reclassification["confidence"],
                latency_ms=(time.monotonic() - follow_up_start) * 1000, ip_address=client_ip,
            )
            perf("follow_up_resolve_and_log_done")
            stage("resolve_follow_up", f"rewritten -> intent={reclassification['intent']}")
            intent = reclassification["intent"] if reclassification["intent"] != "FOLLOW_UP" else None
            entities_source = reclassification

        if explicit_override:
            resolved_lang = explicit_override
            log_language_preference(
                user_id=current_user.username, role_name=current_user.role.value,
                session_id=session_id, language=explicit_override, ip_address=client_ip,
            )
        else:
            resolved_lang = sticky_lang or _auto_detected_lang(input_language)

        if intent == "OUT_OF_SCOPE":
            answer = OUT_OF_SCOPE_REPLY
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            yield _sse({"type": "token", "text": answer})
            yield done(answer=answer, intent=intent, response_language=resolved_lang,
                       translated_answer=translated, language_notice=notice, input_language=input_language)
            return

        if intent == "LEGAL_REFERENCE":
            kb_result = answer_legal_query(question_for_rag)
            if kb_result:
                answer = kb_result["answer"]
                citation = {"source": "legal_kb"}
                citation.update({k: v for k, v in kb_result.items() if k != "answer"})
                if sources_line := build_sources_line([citation]):
                    answer = f"{answer}\n\n{sources_line}"
                save_message(session_id, "user", request.question)
                save_message(session_id, "assistant", answer)
                log_chat_query(
                    user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                    query_text=request.question, response_text=answer, ip_address=client_ip,
                )
                set_cached_answer(request.question, answer, [citation], intent, input_language=input_language)
                translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
                stage("legal_kb", "answered from deterministic legal knowledge base, no LLM call")
                yield _sse({"type": "token", "text": answer})
                yield done(answer=answer, intent=intent, citations=[citation],
                           response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                           input_language=input_language)
                return

        # See send_message()'s matching comment — resolved once, lazily, only
        # for the paths that actually touch case data. A fail-closed
        # AppException here is caught by this generator's own outer
        # try/except AppException below and surfaces as an SSE error event
        # (the streaming equivalent of a 403 — can't set an HTTP status
        # mid-stream).
        station_ids = get_scoped_station_ids(current_user)

        if intent == "AGGREGATE_QUERY":
            yield _sse({"type": "status", "stage": "searching", "text": status_text("searching", resolved_lang)})
            yield _sse({"type": "status", "stage": "composing", "text": status_text("composing", resolved_lang),
                        "response_language": resolved_lang, "input_language": input_language})
            aggregate_result = _handle_aggregate_query(
                question_for_rag, entities_source, resolved_lang=resolved_lang, budget=budget, station_ids=station_ids,
            )
            if aggregate_result is not None:
                answer = aggregate_result["answer"]
                stage("aggregate_query", f"aggregation={entities_source.get('aggregation')}")
                save_message(session_id, "user", request.question)
                save_message(session_id, "assistant", answer)
                log_chat_query(
                    user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                    query_text=request.question, response_text=answer, ip_address=client_ip,
                )
                if aggregate_result["translated_answer"] is not None:
                    translated, notice = aggregate_result["translated_answer"], aggregate_result["language_notice"]
                else:
                    translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
                yield _sse({"type": "token", "text": answer})
                yield done(
                    answer=answer, intent=intent, citations=aggregate_result["citations"], sources=aggregate_result["sources"],
                    response_language=resolved_lang, translated_answer=translated, language_notice=notice,
                    provider_used=aggregate_result["provider"], fallback_reason=aggregate_result.get("fallback_reason"),
                    raw_fallback=aggregate_result.get("raw_fallback") or None,
                    provider_latency_ms=aggregate_result.get("provider_latency_ms"),
                    input_language=input_language,
                )
                return

        # Everything else — CASE_LOOKUP, an unresolved LEGAL_REFERENCE (KB miss),
        # an unresolved AGGREGATE_QUERY, an unresolved FOLLOW_UP, a failed
        # classification — shares the real, streamed composition path below.
        # This is exactly the slow path (6.9s fresh / 19.6s follow-up,
        # pre-fix) the whole streaming feature exists for.
        history_str = follow_up_history_str if follow_up_history_str is not None else _build_history_str(session_id, question_for_rag, recent)
        perf("build_history_str_done")
        allow_case_context = intent != "LEGAL_REFERENCE"
        effective_crime_no = request.crime_no if allow_case_context else None

        yield _sse({"type": "status", "stage": "searching", "text": status_text("searching", resolved_lang)})
        built = _build_grounded_query(question_for_rag, effective_crime_no, history_str, allow_case_context, station_ids)
        perf("build_grounded_query_done")
        if allow_case_context:
            found_count = 1 if built["case_found"] else 0
            stage("search_case_records", f"case_found={built['case_found']}")
            yield _sse({"type": "status", "stage": "found", "text": status_text("found", resolved_lang, found_count), "count": found_count})

        if built["early_answer"] is not None:
            # No such case — already a complete, deterministic answer, no
            # composer call needed at all.
            answer = built["early_answer"]
            save_message(session_id, "user", request.question)
            save_message(session_id, "assistant", answer)
            log_chat_query(
                user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                query_text=request.question, response_text=answer, ip_address=client_ip,
            )
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            yield _sse({"type": "token", "text": answer})
            yield done(answer=answer, intent=intent, response_language=resolved_lang,
                       translated_answer=translated, language_notice=notice, input_language=input_language)
            return

        yield _sse({"type": "status", "stage": "composing", "text": status_text("composing", resolved_lang),
                    "response_language": resolved_lang, "input_language": input_language})
        full_answer_chunks: list[str] = []
        citations: list = []
        provider_used, fallback_reason, provider_latency_ms = None, None, None
        composition_failed = False
        first_token_seen = False
        try:
            for event in rag_answer_with_failover_stream(built["final_query"], budget=budget):
                if event["type"] == "token":
                    if not first_token_seen:
                        first_token_seen = True
                        perf("first_composition_token")
                    full_answer_chunks.append(event["text"])
                    yield _sse({"type": "token", "text": event["text"]})
                else:  # "done"
                    citations = event["citations"]
                    provider_used = event["provider_used"]
                    fallback_reason = event["fallback_reason"]
                    provider_latency_ms = event["latency_ms"]
        except Exception as e:
            composition_failed = True
            logger.warning(f"Streaming composition failed: {e}")

        if composition_failed:
            if built["case_found"] and built["db_data"]:
                answer = _format_raw_case_data(built["effective_crime_no"], built["db_data"], built["mo_result"])
                citations = [{"source": "database", "crime_no": built["effective_crime_no"]}]
                provider_used, fallback_reason, provider_latency_ms = "raw_data", None, None
                yield _sse({"type": "token", "text": answer})
            else:
                answer = (
                    "I'm having trouble reaching the AI service right now — it's responding "
                    "very slowly or not at all. Please try again in a moment, or rephrase your question."
                )
                save_message(session_id, "user", request.question)
                save_message(session_id, "assistant", answer)
                log_chat_query(
                    user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
                    query_text=request.question, response_text=answer, ip_address=client_ip,
                )
                notice = None
                if resolved_lang and resolved_lang != "en":
                    notice = f"Could not respond in {LANGUAGE_NAMES.get(resolved_lang, resolved_lang)} right now — the AI service is unavailable."
                yield _sse({"type": "token", "text": answer})
                yield done(answer=answer, intent=intent, response_language=resolved_lang,
                           language_notice=notice, service_degraded=True, input_language=input_language)
                return
        else:
            answer = _strip_markdown("".join(full_answer_chunks))
            if built["case_found"]:
                citations = [{"source": "database", "crime_no": built["effective_crime_no"]}] + list(citations)
            if not citations and not built["case_found"] and is_vague_rag_answer(answer):
                answer = LOW_CONFIDENCE_REPLY
                citations = []

        stage("compose", f"provider={provider_used}" + (f", fallback_reason={fallback_reason}" if fallback_reason else ""))
        answer = strip_existing_sources_line(answer)
        if sources_line := build_sources_line(citations):
            answer = f"{answer}\n\n{sources_line}"

        save_message(session_id, "user", request.question)
        save_message(session_id, "assistant", answer)
        log_chat_query(
            user_id=current_user.username, role_name=current_user.role.value, session_id=session_id,
            query_text=request.question, response_text=answer, ip_address=client_ip,
        )

        translated, notice = None, None
        if resolved_lang and resolved_lang != "en":
            yield _sse({"type": "status", "stage": "translating", "text": status_text("translating", resolved_lang)})
            translated, notice = _resolve_translated_answer(answer, resolved_lang, budget=budget)
            stage("translate", f"target={resolved_lang}, ok={translated is not None}")

        yield done(
            answer=answer, intent=intent, citations=citations, response_language=resolved_lang,
            translated_answer=translated, language_notice=notice, provider_used=provider_used,
            fallback_reason=fallback_reason, raw_fallback=(provider_used == "raw_data") or None,
            provider_latency_ms=provider_latency_ms, input_language=input_language,
        )
    except AppException as e:
        yield _sse({"type": "error", "message": e.message, "status_code": e.status_code})
    except Exception as e:
        logger.error(f"Streaming chat processing failed: {e}")
        yield _sse({"type": "error", "message": f"Chat processing failed: {str(e)}"})


@router.post("/stream")
def stream_message(request: ChatRequest, http_request: Request, current_user: CurrentUser = Depends(get_current_user)):
    """SSE variant of POST /chat/message — same routing/grounding/composition
    logic (see _stream_chat_response), streamed as it's produced instead of
    buffered into one JSON response. POST /chat/message is unchanged and
    stays available for any caller that wants a single response instead.

    Event shapes (each an SSE `data: <json>\\n\\n` frame):
      {"type": "status", "stage": ..., "text": "<localized progress text>"}
      {"type": "token", "text": "<chunk of the answer>"}
      {"type": "done", "answer": ..., "session_id": ..., "citations": [...],
       "provider_used": ..., "fallback_reason": ..., "latency_ms": ...,
       "response_language": ..., "translated_answer": ..., "language_notice": ...,
       "intent": ..., "reasoning_path": [{"stage", "detail", "at_ms"}, ...]}
      {"type": "error", "message": "..."}

    `X-Accel-Buffering: no` disables nginx-style proxy buffering of the SSE
    stream — harmless if nothing in front of this actually buffers, but the
    one header that turns "streamed on the wire" into "the browser also gets
    it incrementally" if something does."""
    return StreamingResponse(
        _stream_chat_response(request, http_request, current_user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback")
def chat_feedback(
    request: ChatFeedbackRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Thumbs up/down on an assistant answer — accountability trail only, does
    not affect the answer already given or any future routing/generation."""
    if request.rating not in ("up", "down"):
        raise AppException("rating must be 'up' or 'down'", status_code=400)
    client_ip = http_request.client.host if http_request.client else "Unknown"
    log_chat_feedback(
        user_id=current_user.username, role_name=current_user.role.value, session_id=request.session_id,
        question=request.question, answer=request.answer, rating=request.rating, ip_address=client_ip,
    )
    return {"recorded": True}


@router.post("/language-preference/clear")
def clear_language_preference(
    request: ClearLanguagePreferenceRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """One-click "stop replying in X" from the Chat page's language badge —
    logs an explicit clear so get_session_language_preference's next read
    returns None for this session (the most recent [language_preference] row
    always wins, see audit_service), reverting to the existing auto-detect/
    UI-toggle behavior on the next turn."""
    client_ip = http_request.client.host if http_request.client else "Unknown"
    log_language_preference(
        user_id=current_user.username, role_name=current_user.role.value,
        session_id=request.session_id, language=None, ip_address=client_ip,
    )
    return {"cleared": True}


@router.get("/sessions", response_model=list[ChatSessionSummary])
def list_sessions(current_user: CurrentUser = Depends(get_current_user)):
    """Backs the Chat page's history sidebar — every past conversation this
    user has had, most recent first, so one can be reopened the way Claude's
    own "Recents" list works."""
    return list_sessions_for_user(current_user.username)


@router.get("/sessions/search")
def sessions_search(q: str, current_user: CurrentUser = Depends(get_current_user)):
    """Session ids matching q by transcript content, for the sidebar's search
    box — title matches are already handled client-side against the
    /sessions list; this covers matches inside a conversation's body."""
    return {"session_ids": list(search_sessions_by_content(current_user.username, q))}


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def get_session_messages(session_id: str, current_user: CurrentUser = Depends(get_current_user)):
    if not session_belongs_to_user(session_id, current_user.username):
        raise AppException("Session not found", status_code=404)
    messages = get_all_messages(session_id)
    return [
        {"role": m["Role"], "message": m["Message"], "set_at": m.get("SetAt")}
        for m in messages
    ]


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Deletes a conversation from history entirely — ChatHistory rows, this
    user's AuditLog rows for it, and its semantic-memory index (see
    chat_history_service.delete_session). A real owned session always has at
    least one AuditLog row (every /chat/message call writes one), so a 0 count
    here only ever means the session wasn't this user's to begin with."""
    deleted_count = delete_session(session_id, current_user.username)
    if deleted_count == 0:
        raise AppException("Session not found", status_code=404)
    return {"deleted": True, "message_count": deleted_count}
