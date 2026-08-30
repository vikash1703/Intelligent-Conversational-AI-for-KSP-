import logging
import math
from datetime import datetime

from core.catalyst_client import execute_zcql, zcql_escape, fetch_all_rows
from services.analytics_service import extract_crime_type
from services.network_service import get_network_for_accused
from chat.llm_provider import rag_answer_with_failover

logger = logging.getLogger("SimilarityService")

# Hybrid design, deliberately not a pure-LLM search: a cheap structured pre-filter
# (same derived crime_type + geographic proximity, both native data) narrows the
# candidate pool first, and the LLM is only used once per request to explain the
# connection in plain language — not to search or rank. This keeps latency/cost
# bounded regardless of how many cases share a crime_type (e.g. ~700-800 each in
# the live data) and keeps the ranking itself deterministic/auditable.
_CANDIDATE_PAGE_SIZE = 300
# REAL BUG FIXED 2026-08-23 (codebase-wide pagination audit): this used to be a
# single LIMIT 300 query, then sorted by distance and sliced to top N — meaning
# "most similar case" was only ever chosen from an arbitrary first-300-in-ZCQL-
# order subset of a crime type's real ~700-800 cases, not the true nearest.
# Exactly the same class of bug the Financial Intelligence page's
# list_suspicious_transactions had (sort applied AFTER a truncating LIMIT).
# Fixed by paginating through every same-crime_type candidate before sorting —
# via core.catalyst_client.fetch_all_rows' cursor pagination, not offset-based
# (see its own docstring: offset pagination can silently drop a real
# candidate even with dedup, which a plain sort-after-limit fix would have
# reintroduced in a different form).
_CANDIDATE_MAX_PAGES = 10


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth approximation — adequate at Karnataka-state scale, avoids the
    cost/complexity of a proper haversine for what's just a similarity ranking."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111  # ~111km per degree


def find_similar_cases(crime_no: str, limit: int = 5) -> dict | None:
    safe_crime_no = zcql_escape(crime_no)
    target_rows = execute_zcql(
        "SELECT CaseMaster.ROWID, CaseMaster.BriefFacts, CaseMaster.latitude, CaseMaster.longitude "
        f"FROM CaseMaster WHERE CaseMaster.CrimeNo = '{safe_crime_no}'"
    )
    if not target_rows:
        return None

    target = target_rows[0].get("CaseMaster", target_rows[0])
    target_crime_type = extract_crime_type(target.get("BriefFacts"))
    target_lat, target_lon = target.get("latitude"), target.get("longitude")

    # Every candidate, not just the first 300 in ZCQL's own order (see
    # _CANDIDATE_MAX_PAGES comment above).
    candidates = fetch_all_rows(
        "CaseMaster", ["CrimeNo", "latitude", "longitude"],
        f" WHERE CaseMaster.BriefFacts = '{zcql_escape(target.get('BriefFacts', ''))}' "
        f"AND CaseMaster.CrimeNo != '{safe_crime_no}'",
        page_size=_CANDIDATE_PAGE_SIZE, max_pages=_CANDIDATE_MAX_PAGES,
    )

    scored = []
    for row in candidates:
        if row.get("latitude") is None or target_lat is None:
            continue
        distance = _distance_km(float(target_lat), float(target_lon), float(row["latitude"]), float(row["longitude"]))
        scored.append({"crime_no": row["CrimeNo"], "distance_km": round(distance, 1)})

    scored.sort(key=lambda x: x["distance_km"])
    top_matches = scored[:limit]

    explanation = ""
    doc_citations = []
    if top_matches:
        summary_lines = "\n".join(f"- {m['crime_no']}: {m['distance_km']} km away" for m in top_matches)
        prompt = (
            f"A case (crime type: {target_crime_type}) has these similar cases, matched by the same "
            f"crime type and geographic proximity:\n{summary_lines}\n\n"
            "In 2-3 sentences, explain what this pattern might suggest to an investigator. "
            "Base this only on the crime type and distances given — do not invent case details."
        )
        try:
            rag_result, _provider, _reason, _latency_ms = rag_answer_with_failover(prompt)
            explanation = rag_result["answer"]
            doc_citations = rag_result["citations"]
        except Exception as e:
            logger.error(f"LLM explanation failed for similar-cases: {str(e)}")
            explanation = "(explanation unavailable)"

    return {
        "crime_no": crime_no,
        "crime_type": target_crime_type,
        "similar_cases": top_matches,
        "explanation": explanation,
        "citations": [
            {"source": "database", "crime_no": crime_no, "matched_crime_nos": [m["crime_no"] for m in top_matches]}
        ] + doc_citations,
    }


def get_investigative_leads(crime_no: str) -> dict | None:
    similar = find_similar_cases(crime_no, limit=5)
    if similar is None:
        return None

    safe_crime_no = zcql_escape(crime_no)
    case_rows = execute_zcql(
        "SELECT CaseMaster.ROWID FROM CaseMaster WHERE CaseMaster.CrimeNo = '" + safe_crime_no + "'"
    )
    case_id = case_rows[0].get("CaseMaster", case_rows[0])["ROWID"]

    accused_rows = execute_zcql(
        f"SELECT Accused.AccusedMasterID FROM Accused WHERE Accused.CaseMasterID = '{zcql_escape(str(case_id))}'"
    )

    network_notes = []
    for r in accused_rows:
        accused_id = r.get("Accused", r)["AccusedMasterID"]
        network = get_network_for_accused(str(accused_id))
        if network and network.get("gang_name"):
            network_notes.append(f"Accused {accused_id} is linked to gang '{network['gang_name']}'")

    # Deliberately NOT prefixed with "Given this case context:" — verified live that
    # this exact phrase makes Zoho's RAG endpoint treat the prompt as a document-search
    # query, which then fails to match any uploaded document and falls back to "I'm not
    # sure what information you're looking for" regardless of how the rest of the prompt
    # is worded. Plain descriptive prose (as below) gets a real answer instead.
    network_summary = " ".join(network_notes) if network_notes else "No known gang or network links were found."
    prompt = (
        f"A case (crime type: {similar['crime_type']}) has {len(similar['similar_cases'])} similar cases on "
        f"record, matched by crime type and geographic proximity. {network_summary}\n\n"
        "In 2-3 sentences, suggest concrete investigative leads an officer could pursue next. "
        "Base this only on the information given above — do not invent case details."
    )
    doc_citations = []
    try:
        rag_result, _provider, _reason, _latency_ms = rag_answer_with_failover(prompt)
        leads = rag_result["answer"]
        doc_citations = rag_result["citations"]
    except Exception as e:
        logger.error(f"LLM lead-generation failed: {str(e)}")
        leads = "(leads unavailable)"

    return {
        "crime_no": crime_no,
        "leads": leads,
        "citations": [
            {
                "source": "database",
                "crime_no": crime_no,
                "similar_case_count": len(similar["similar_cases"]),
                "network_links": network_notes,
            }
        ] + doc_citations,
    }
