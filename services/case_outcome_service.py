import logging

from core.catalyst_client import fetch_all_rows
from core.ttl_cache import ttl_cached
from services.analytics_service import extract_crime_type
from services.timeline_service import get_case_status_labels

logger = logging.getLogger("CaseOutcomeService")

# Tier 1 item 7 (Case Outcome Flow / Sankey), added 2026-08-24. Real
# Crime Type -> Case Status -> Chargesheet Outcome flow, backing the
# Analytics page's new section.
_OUTCOME_NO_CHARGESHEET = "No Chargesheet Yet"
_OUTCOME_FILED = "Chargesheet Filed"
_OUTCOME_FALSE_CASE = "False Case"
# ChargesheetDetails.cstype documents a 3rd value, C=Undetected (see the ER
# diagram / reference_ksp_db_schema memory) — live-verified 2026-08-24: 0 of
# 1,025 real ChargesheetDetails rows use it. Not fabricated as a flow node
# with a manufactured zero; called out as a real, honest absence in the
# page's own disclosure banner instead (see Analytics.jsx's caseOutcome
# section), matching the same standard as the Hotspot Map's synthetic-
# coordinates banner.
_CSTYPE_LABEL = {"A": _OUTCOME_FILED, "B": _OUTCOME_FALSE_CASE}


@ttl_cached()
def _case_chargesheet_types() -> dict[str, list[str]]:
    """Every case's real linked ChargesheetDetails.cstype values — a case can
    have more than one (live-verified 2026-08-24: 128 of 884 cases with any
    chargesheet have 2+, sometimes contradictory, e.g. one 'A' and one 'B' on
    the same case)."""
    rows = fetch_all_rows("ChargesheetDetails", ["CaseMasterID", "cstype"])
    by_case: dict[str, list[str]] = {}
    for r in rows:
        by_case.setdefault(r["CaseMasterID"], []).append(r.get("cstype"))
    return by_case


def _resolve_outcome(cstypes: list[str] | None) -> str:
    """A case with both an 'A' (chargesheet filed) and a 'B' (false case) row
    resolves to 'Chargesheet Filed' — the more definitive of the two real
    outcomes takes priority. A deliberate, documented modeling choice, not a
    hidden one: affects 128 real cases (live-verified 2026-08-24, same day),
    each counted once here rather than split or double-counted across two
    flow segments."""
    if not cstypes:
        return _OUTCOME_NO_CHARGESHEET
    if "A" in cstypes:
        return _OUTCOME_FILED
    if "B" in cstypes:
        return _OUTCOME_FALSE_CASE
    return _OUTCOME_NO_CHARGESHEET


@ttl_cached()
def _case_outcomes() -> dict[str, str]:
    """CaseMasterID -> resolved chargesheet outcome, for every case with at
    least one real ChargesheetDetails row. A case absent from this dict has
    none at all — callers treat that absence as _OUTCOME_NO_CHARGESHEET."""
    by_case = _case_chargesheet_types()
    return {case_id: _resolve_outcome(types) for case_id, types in by_case.items()}


@ttl_cached()
def _all_case_ids() -> set[str]:
    return {row["ROWID"] for row in fetch_all_rows("CaseMaster", [])}


def case_ids_by_chargesheet_outcome(outcome: str) -> set[str]:
    """Real CaseMaster ROWIDs whose resolved chargesheet outcome is `outcome`.
    "No Chargesheet Yet" needs the full case-id universe to correctly include
    cases that are simply absent from ChargesheetDetails entirely, not just
    an empty intersection."""
    outcomes = _case_outcomes()
    if outcome == _OUTCOME_NO_CHARGESHEET:
        definitive_ids = {cid for cid, o in outcomes.items() if o != _OUTCOME_NO_CHARGESHEET}
        return _all_case_ids() - definitive_ids
    return {cid for cid, o in outcomes.items() if o == outcome}


def _is_int_in(value, id_set: set[int]) -> bool:
    try:
        return int(value) in id_set
    except (TypeError, ValueError):
        return False


@ttl_cached()
def get_case_outcome_flow(station_ids: list[int] | None = None) -> dict:
    """Real Crime Type -> Case Status -> Chargesheet Outcome flow counts, full
    scan, scoped like every other case-touching aggregate in this app (a
    station-scoped officer sees flow counts against only their own real
    cases, never the statewide total)."""
    cases = fetch_all_rows("CaseMaster", ["BriefFacts", "CaseStatusID", "PoliceStationID"])
    if station_ids is not None:
        sset = set(station_ids)
        cases = [c for c in cases if _is_int_in(c.get("PoliceStationID"), sset)]

    status_labels = get_case_status_labels()
    outcomes = _case_outcomes()

    stage1: dict[tuple[str, str], int] = {}
    stage2: dict[tuple[str, str], int] = {}
    crime_totals: dict[str, int] = {}
    status_totals: dict[str, int] = {}
    outcome_totals: dict[str, int] = {}

    for c in cases:
        crime_type = extract_crime_type(c.get("BriefFacts"))
        status = status_labels.get(str(c.get("CaseStatusID")), "Unknown")
        outcome = outcomes.get(c["ROWID"], _OUTCOME_NO_CHARGESHEET)

        stage1[(crime_type, status)] = stage1.get((crime_type, status), 0) + 1
        stage2[(status, outcome)] = stage2.get((status, outcome), 0) + 1
        crime_totals[crime_type] = crime_totals.get(crime_type, 0) + 1
        status_totals[status] = status_totals.get(status, 0) + 1
        outcome_totals[outcome] = outcome_totals.get(outcome, 0) + 1

    return {
        "total_cases": len(cases),
        "crime_types": [{"name": k, "count": v} for k, v in sorted(crime_totals.items())],
        "statuses": [{"name": k, "count": v} for k, v in sorted(status_totals.items())],
        "outcomes": [{"name": k, "count": v} for k, v in sorted(outcome_totals.items())],
        "stage1_links": [{"source": c, "target": s, "value": v} for (c, s), v in sorted(stage1.items())],
        "stage2_links": [{"source": s, "target": o, "value": v} for (s, o), v in sorted(stage2.items())],
        # {status name -> real CaseStatusID} — so a frontend click handler can
        # build a /cases/search?case_status_id=... filter without a second
        # round trip to /cases/filter-options just for this one lookup.
        "status_ids": {name: rowid for rowid, name in status_labels.items()},
    }
