import logging

from core.catalyst_client import execute_zcql, zcql_escape
from services.db_service import get_case_full
from chat.llm_provider import rag_answer_with_failover

logger = logging.getLogger("InsightService")

# Routed through the same Gemini-primary/Zia-fallback composition chain
# chat.py uses (see chat/llm_provider.py's module docstring) instead of a
# raw ask_zoho_rag() call — these prompts inject structured case data as
# text, the same shape chat.py's grounded-answer composition already uses,
# so there's no Zia-specific document-retrieval need here that would argue
# for calling Zia directly. Added 2026-07-23 alongside the chat.py routing
# reorder, since this module had the exact same "every call waits on Zia
# first, with no fallback at all" problem.

# Fixed prompt templates, not freeform chat — every call to this module asks for
# the same structured output shape, so results are consistent across cases rather
# than depending on how a user happened to phrase a chat question. Grounded in
# CaseMaster/Accused/etc. data pulled fresh per call (same pattern as chat.py's
# crime_no context injection), never on the model's own knowledge of the case.

_SUMMARY_TEMPLATE = (
    "Generate a structured case summary using ONLY the data below. Do not invent "
    "any facts not present in this data. Use exactly these section headers, written "
    "in plain text with no markdown syntax at all (no #, ##, **, -, or any other "
    "formatting symbols — the frontend renders this as plain text, not markdown, so "
    "literal # and ** characters would show up as-is):\n"
    "Background:\nParties Involved:\nTimeline:\nCurrent Status:\nLegal Sections:\n\n"
    "Case data:\n{case_data}"
)

_BEHAVIORAL_TEMPLATE = (
    "Analyze this offender's case history using ONLY the data below. Identify any "
    "patterns in crime type, timing, or escalation across their cases. If there is "
    "only one case on record, state that explicitly rather than speculating about "
    "a pattern. Do not invent facts not present in this data.\n\n"
    "Case history:\n{case_history}"
)


def generate_case_summary(crime_no: str) -> dict | None:
    case = get_case_full(crime_no)
    if not case:
        return None

    prompt = _SUMMARY_TEMPLATE.format(case_data=str(case))
    try:
        rag_result, provider_used, _reason, _latency_ms = rag_answer_with_failover(prompt)
    except Exception as e:
        logger.error(f"Case summary generation failed for {crime_no}: {str(e)}")
        raise

    return {
        "crime_no": crime_no,
        "summary": rag_result["answer"],
        "provider_used": provider_used,
        "citations": [
            {"source": "database", "crime_no": crime_no, "case_master_id": case["CaseMasterID"]}
        ] + rag_result["citations"],
    }


def generate_behavioral_analysis(accused_name: str) -> dict | None:
    safe_name = zcql_escape(accused_name)
    rows = execute_zcql(
        "SELECT Accused.AccusedMasterID, Accused.CaseMasterID, Accused.AgeYear, Accused.GenderID "
        f"FROM Accused WHERE Accused.AccusedName = '{safe_name}'"
    )
    if not rows:
        return None

    # One bulk IN-query instead of one query per case_id — avoids N sequential
    # round-trips for an accused with a long case history (same fix as
    # scoring_service.get_offender_risk_score).
    # AccusedMasterID is a per-case appearance, not a stable per-human identifier
    # (see db_service.get_accused_history) — the same name can legitimately match
    # several rows across different cases (or even different people). The first
    # match is used as a representative id for "View in Network" deep-linking,
    # same acceptance of this schema limitation as the accused-history endpoint.
    accused_rows = [r.get("Accused", r) for r in rows]
    representative_accused_id = accused_rows[0]["AccusedMasterID"]
    case_ids = [r["CaseMasterID"] for r in accused_rows]
    ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in case_ids)
    case_rows = execute_zcql(
        "SELECT CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, CaseMaster.BriefFacts "
        f"FROM CaseMaster WHERE CaseMaster.ROWID IN ({ids_literal})"
    )
    case_histories = [r.get("CaseMaster", r) for r in case_rows]

    crime_nos = [h.get("CrimeNo") for h in case_histories if h.get("CrimeNo")]

    prompt = _BEHAVIORAL_TEMPLATE.format(case_history=str(case_histories))
    try:
        rag_result, provider_used, _reason, _latency_ms = rag_answer_with_failover(prompt)
    except Exception as e:
        logger.error(f"Behavioral analysis failed for {accused_name}: {str(e)}")
        raise

    return {
        "accused_name": accused_name,
        "accused_id": representative_accused_id,
        "analysis": rag_result["answer"],
        "provider_used": provider_used,
        "citations": [
            {"source": "database", "accused_name": accused_name, "case_count": len(case_histories), "crime_nos": crime_nos}
        ] + rag_result["citations"],
    }
