import logging
import re
from collections import Counter
from datetime import datetime

from core.catalyst_client import execute_zcql, zcql_escape, validate_date

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
# a single query. Capped at 10 pages (3000 rows) to match current data volume
# without letting an unbounded date range trigger unbounded fetching.
_PAGE_SIZE = 300
_MAX_PAGES = 10


def extract_crime_type(brief_facts: str) -> str:
    match = _BRIEF_FACTS_PATTERN.search(brief_facts or "")
    return match.group(1) if match else "Unspecified"


def get_dataset_summary() -> dict:
    """Total case and accused counts — real live COUNT queries, used for the
    Chat page's landing-state grounding line ("Grounded in N cases · M
    accused records"). Deliberately just these two native GROUP-BY-free
    COUNTs, not a full dashboard payload — this is presentation copy, not an
    analytics view."""
    case_rows = execute_zcql("SELECT COUNT(CaseMaster.ROWID) FROM CaseMaster")
    accused_rows = execute_zcql("SELECT COUNT(Accused.ROWID) FROM Accused")
    total_cases = int(case_rows[0].get("CaseMaster", case_rows[0]).get("COUNT(ROWID)", 0)) if case_rows else 0
    total_accused = int(accused_rows[0].get("Accused", accused_rows[0]).get("COUNT(ROWID)", 0)) if accused_rows else 0
    return {"total_cases": total_cases, "total_accused": total_accused}


def get_crime_type_distribution() -> list:
    """Case count per crime type, derived from CaseMaster.BriefFacts (see module
    docstring) via a single native ZCQL GROUP BY — no pagination needed since the
    grouped result set is tiny regardless of table size."""
    rows = execute_zcql(
        "SELECT CaseMaster.BriefFacts, COUNT(CaseMaster.ROWID) "
        "FROM CaseMaster GROUP BY CaseMaster.BriefFacts"
    )
    distribution = Counter()
    for r in rows:
        row = r.get("CaseMaster", r)
        crime_type = extract_crime_type(row.get("BriefFacts"))
        distribution[crime_type] += int(row.get("COUNT(ROWID)", 0))
    return [{"crime_type": k, "count": v} for k, v in distribution.most_common()]


def paginate_case_dates(from_date: str | None, to_date: str | None) -> list:
    """Every CaseMaster row's date/crime-type/location fields, paginated. Carries
    latitude/longitude alongside the date/BriefFacts columns this function
    originally returned — existing callers (get_crime_trends, get_seasonal_trends)
    just ignore the extra keys, and scoring_service's district-bucketing needs
    them without a second full-table fetch."""
    conditions = []
    if from_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate >= '{zcql_escape(validate_date(from_date, 'from_date'))}'")
    if to_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate <= '{zcql_escape(validate_date(to_date, 'to_date'))}'")
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    all_rows = []
    for page in range(_MAX_PAGES):
        offset = page * _PAGE_SIZE
        query = (
            "SELECT CaseMaster.CrimeRegisteredDate, CaseMaster.BriefFacts, "
            "CaseMaster.latitude, CaseMaster.longitude "
            "FROM CaseMaster" + where_clause +
            f" LIMIT {offset},{_PAGE_SIZE}"
        )
        rows = execute_zcql(query)
        if not rows:
            break
        all_rows.extend(r.get("CaseMaster", r) for r in rows)
        if len(rows) < _PAGE_SIZE:
            break
    else:
        logger.warning(f"Hit the {_MAX_PAGES}-page cap fetching case dates — results may be incomplete")

    return all_rows


def get_crime_trends(from_date: str | None = None, to_date: str | None = None) -> list:
    """Case count per calendar month (YYYY-MM), oldest first — no ZCQL date-part
    function exists, so bucketing happens here after a paginated fetch."""
    rows = paginate_case_dates(from_date, to_date)
    monthly = Counter()
    for row in rows:
        registered = row.get("CrimeRegisteredDate")
        if not registered:
            continue
        month_key = str(registered)[:7]  # "YYYY-MM-DD" -> "YYYY-MM"
        monthly[month_key] += 1
    return [{"month": k, "count": v} for k, v in sorted(monthly.items())]


def get_seasonal_trends(from_date: str | None = None, to_date: str | None = None) -> list:
    """Case count per calendar month-of-year (Jan..Dec), summed across all years —
    surfaces seasonality independent of which year each case happened in."""
    rows = paginate_case_dates(from_date, to_date)
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
