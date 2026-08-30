import logging
import re
from collections import Counter
from datetime import datetime

from core.catalyst_client import execute_zcql, zcql_escape, validate_date, fetch_all_rows
from core.ttl_cache import ttl_cached

logger = logging.getLogger("AnalyticsService")

# CaseMaster's classification FKs (CrimeMajorHeadID, CrimeMinorHeadID, CaseStatusID,
# CaseCategoryID, GravityOffenceID, PoliceStationID) are NULL on every one of the
# 3000 live rows — verified directly against Catalyst — so crime-type grouping can't
# use them. BriefFacts follows a consistent "Investigation regarding {type}
# registered." template in the live data, so it's used as the crime-type signal
# instead. If CaseMaster's classification columns get populated later, switch this
# to CrimeMajorHeadID/CrimeSubHead — see reference_ksp_db_schema memory.
_BRIEF_FACTS_PATTERN = re.compile(r"Investigation regarding (.+?) registered\.?")

# ZCQL's hard LIMIT ceiling is 300 per query (see project memory) — trend/seasonal
# analysis needs the full date range, so this paginates in pages of 300 rather than
# a single query. CaseMaster is exactly 3000 rows today (10 full pages) — a cap of
# exactly 10 was live-verified to trigger the "hit the cap" warning on every call
# even though all 3000 real rows were fetched, because the loop never sees a
# short/partial final page to naturally stop on. Capped at 30 (9000 rows) instead,
# for headroom against real growth without letting an unbounded date range trigger
# truly unbounded fetching.
_PAGE_SIZE = 300
_MAX_PAGES = 30


def extract_crime_type(brief_facts: str) -> str:
    match = _BRIEF_FACTS_PATTERN.search(brief_facts or "")
    return match.group(1) if match else "Unspecified"


def get_dataset_summary(station_ids: list[int] | None = None) -> dict:
    """Total case and accused counts — real live COUNT queries, used for the
    Chat page's landing-state grounding line ("Grounded in N cases · M
    accused records"). Deliberately just these two native GROUP-BY-free
    COUNTs, not a full dashboard payload — this is presentation copy, not an
    analytics view.

    station_ids (added 2026-08-23): scopes total_cases (a direct CaseMaster
    query, cheap to filter). total_accused is deliberately NOT scoped yet —
    Accused has no PoliceStationID of its own, and scoping it correctly needs
    a join through Accused.CaseMasterID against an in-scope case-id list
    (same pattern as get_accused_history), not a plain WHERE clause. Left
    unscoped rather than rushed; a scoped officer currently sees the
    STATEWIDE accused count alongside their own scoped case count here — a
    real, known gap, not a silent one."""
    case_conditions = []
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        case_conditions.append(f"CaseMaster.PoliceStationID IN ({stations_literal})")
    case_where = f" WHERE {' AND '.join(case_conditions)}" if case_conditions else ""
    case_rows = execute_zcql(f"SELECT COUNT(CaseMaster.ROWID) FROM CaseMaster{case_where}")
    accused_rows = execute_zcql("SELECT COUNT(Accused.ROWID) FROM Accused")
    total_cases = int(case_rows[0].get("CaseMaster", case_rows[0]).get("COUNT(ROWID)", 0)) if case_rows else 0
    total_accused = int(accused_rows[0].get("Accused", accused_rows[0]).get("COUNT(ROWID)", 0)) if accused_rows else 0
    return {"total_cases": total_cases, "total_accused": total_accused}


def get_crime_type_distribution(station_ids: list[int] | None = None) -> list:
    """Case count per crime type, derived from CaseMaster.BriefFacts (see module
    docstring) via a single native ZCQL GROUP BY — no pagination needed since the
    grouped result set is tiny regardless of table size."""
    where_clause = ""
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        where_clause = f" WHERE CaseMaster.PoliceStationID IN ({stations_literal})"
    rows = execute_zcql(
        "SELECT CaseMaster.BriefFacts, COUNT(CaseMaster.ROWID) "
        f"FROM CaseMaster{where_clause} GROUP BY CaseMaster.BriefFacts"
    )
    distribution = Counter()
    for r in rows:
        row = r.get("CaseMaster", r)
        crime_type = extract_crime_type(row.get("BriefFacts"))
        distribution[crime_type] += int(row.get("COUNT(ROWID)", 0))
    return [{"crime_type": k, "count": v} for k, v in distribution.most_common()]


def paginate_case_dates(from_date: str | None, to_date: str | None, station_ids: list[int] | None = None) -> list:
    """Every CaseMaster row's date/crime-type/location fields, paginated. Carries
    latitude/longitude alongside the date/BriefFacts columns this function
    originally returned — existing callers (get_crime_trends, get_seasonal_trends)
    just ignore the extra keys, and scoring_service's district-bucketing needs
    them without a second full-table fetch.

    station_ids (added 2026-08-23, see services/permission_service.
    get_scoped_station_ids) — pushed into the WHERE clause like from_date/to_date,
    since PoliceStationID is a real CaseMaster column."""
    conditions = []
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        conditions.append(f"CaseMaster.PoliceStationID IN ({stations_literal})")
    if from_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate >= '{zcql_escape(validate_date(from_date, 'from_date'))}'")
    if to_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate <= '{zcql_escape(validate_date(to_date, 'to_date'))}'")
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    # Cursor-based (fetch_all_rows), not offset-based LIMIT page*n,n — codebase-
    # wide pagination audit, 2026-08-23: offset pagination without ORDER BY can
    # return the same row at both the end of one page and the start of the
    # next (live-reproduced on CaseMaster/Accused), and adding ORDER BY did
    # NOT fix it. Worse, the duplicate silently displaces a different real
    # row, so even de-duplicating on receipt still under-counts — measured
    # directly on this exact table (3000 rows): offset+dedup returned 2999,
    # cursor pagination returned the true 3000/3000. See fetch_all_rows'
    # own docstring for the full finding.
    return fetch_all_rows(
        "CaseMaster",
        ["CrimeRegisteredDate", "BriefFacts", "latitude", "longitude"],
        where_clause, page_size=_PAGE_SIZE, max_pages=_MAX_PAGES,
    )


@ttl_cached()
def get_crime_trends(from_date: str | None = None, to_date: str | None = None, station_ids: list[int] | None = None) -> list:
    """Case count per calendar month (YYYY-MM), oldest first — no ZCQL date-part
    function exists, so bucketing happens here after a paginated fetch.
    TTL-cached (see core/ttl_cache) — live-measured at ~3.2s per call, and
    CaseMaster doesn't change mid-demo."""
    rows = paginate_case_dates(from_date, to_date, station_ids)
    monthly = Counter()
    for row in rows:
        registered = row.get("CrimeRegisteredDate")
        if not registered:
            continue
        month_key = str(registered)[:7]  # "YYYY-MM-DD" -> "YYYY-MM"
        monthly[month_key] += 1
    return [{"month": k, "count": v} for k, v in sorted(monthly.items())]


@ttl_cached()
def get_seasonal_trends(from_date: str | None = None, to_date: str | None = None, station_ids: list[int] | None = None) -> list:
    """Case count per calendar month-of-year (Jan..Dec), summed across all years —
    surfaces seasonality independent of which year each case happened in.
    TTL-cached (see core/ttl_cache) — same rationale as get_crime_trends above."""
    rows = paginate_case_dates(from_date, to_date, station_ids)
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    seasonal = Counter()
    for row in rows:
        registered = row.get("CrimeRegisteredDate")
        if not registered:
            continue
        try:
            month_num = int(str(registered)[5:7])
            seasonal[month_names[month_num - 1]] += 1
        except (ValueError, IndexError):
            continue
    return [{"month": m, "count": seasonal.get(m, 0)} for m in month_names]


def _bucket_age(age: int) -> str:
    if age <= 18:
        return "0-18"
    if age <= 30:
        return "19-30"
    if age <= 45:
        return "31-45"
    if age <= 60:
        return "46-60"
    return "60+"


def get_victim_demographics() -> dict:
    """Gender breakdown via native GROUP BY; age-band breakdown via a single
    unfiltered fetch + Python bucketing (ZCQL has no CASE/bucketing function)."""
    gender_rows = execute_zcql(
        "SELECT Victim.GenderID, COUNT(Victim.VictimMasterID) FROM Victim GROUP BY Victim.GenderID"
    )
    gender_distribution = [
        {"gender_id": r.get("Victim", r).get("GenderID"), "count": int(r.get("Victim", r).get("COUNT(VictimMasterID)", 0))}
        for r in gender_rows
    ]

    # Age-band breakdown is a sample of the first 300 victim records (ZCQL's LIMIT
    # ceiling), not an exhaustive count like the gender breakdown above — there's no
    # native age-bucketing function to push this down as a GROUP BY. Good enough for
    # a proportional distribution view; switch to full pagination if exact counts
    # are needed later.
    age_rows = execute_zcql(f"SELECT Victim.AgeYear FROM Victim LIMIT {_PAGE_SIZE}")
    age_bands = Counter()
    for r in age_rows:
        age_str = r.get("Victim", r).get("AgeYear")
        if age_str is None:
            continue
        try:
            age_bands[_bucket_age(int(age_str))] += 1
        except ValueError:
            continue

    return {
        "by_gender": gender_distribution,
        "by_age_band": [{"age_band": band, "count": age_bands.get(band, 0)} for band in ["0-18", "19-30", "31-45", "46-60", "60+"]],
    }
