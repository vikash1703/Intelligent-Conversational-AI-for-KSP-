import logging
from datetime import datetime

from data.karnataka_census_reference import KARNATAKA_DISTRICT_CENSUS
from services.analytics_service import get_crime_type_distribution, paginate_case_dates

logger = logging.getLogger("EntityExtractor")

KNOWN_DISTRICTS = [d["district"] for d in KARNATAKA_DISTRICT_CENSUS]

AGGREGATIONS = [
    "count", "list", "group_by_district", "group_by_month", "trend",
    # Added 2026-07-23 to close 2 real gaps found in live testing: a question
    # asking WHAT SECTIONS apply to a crime type was previously answered with
    # a district breakdown instead (there was no aggregation type for it at
    # all); a question asking for AVERAGE TIME BETWEEN FIR AND ARREST got an
    # unrelated count/district answer instead of either a real number or an
    # honest "not available" — see chat/zcql_builder.group_by_section /
    # average_days_to_arrest.
    "group_by_section", "avg_days_to_arrest",
]

# Both cached in-process at module scope — same convention as
# permission_service.py's RolePermission cache. Real crime types only change if
# a genuinely new BriefFacts template is registered (hasn't happened all
# session); the dataset's latest date only changes if new cases are added to
# this historical dataset, which also hasn't happened. Fetching either fresh
# on every chat turn would be pure repeated cost for a value that's already
# effectively constant for the process's lifetime.
_crime_types_cache: list[str] | None = None
_date_span_cache: tuple[str, str] | None = None


def get_known_crime_types() -> list[str]:
    """The real, distinct crime-type values actually present in CaseMaster
    (derived from BriefFacts — see analytics_service module docstring for why
    there's no classification FK to read this from directly) — fetched once,
    not hardcoded, so this always reflects live data rather than a stale
    guess at what crime types exist."""
    global _crime_types_cache
    if _crime_types_cache is None:
        _crime_types_cache = [row["crime_type"] for row in get_crime_type_distribution()]
    return _crime_types_cache


_case_statuses_cache: list[str] | None = None


def get_known_case_statuses() -> list[str]:
    """The real, distinct CaseStatus names (added 2026-08-24 — REAL BUG FIXED:
    "how many cases are charge sheeted" used to silently answer with the
    unfiltered total (3,000, should have been 994) because this aggregation
    path had no concept of case status at all — crime_type and district were
    filterable dimensions, status wasn't, so a status word either got ignored
    (falsely confident wrong answer) or wasn't recognized (fell through to a
    generic response) depending on phrasing, with no consistent honest
    "I don't recognize that" the way an unrecognized crime_type/district
    already got). Same fetch-once-not-hardcoded convention as
    get_known_crime_types() above — local import, not module-level: a
    module-level one would pull in services.db_service (via
    timeline_service), which is heavier than this small chat-layer module
    should import at load time for every caller, not just the aggregate-query
    path that actually needs it."""
    global _case_statuses_cache
    if _case_statuses_cache is None:
        from services.timeline_service import get_case_status_labels
        _case_statuses_cache = sorted(set(get_case_status_labels().values()))
    return _case_statuses_cache


def get_dataset_date_span() -> tuple[str, str]:
    """(earliest, latest) CrimeRegisteredDate across the whole dataset — one
    cached fetch backing both get_dataset_anchor_date() (the resolution
    anchor for relative date phrases) and the AGGREGATE_QUERY answer's
    "Sources: ... window X-Y" line when no explicit date filter narrowed the
    query."""
    global _date_span_cache
    if _date_span_cache is None:
        rows = paginate_case_dates(None, None)
        dates = [str(r["CrimeRegisteredDate"])[:10] for r in rows if r.get("CrimeRegisteredDate")]
        today = datetime.now().strftime("%Y-%m-%d")
        _date_span_cache = (min(dates), max(dates)) if dates else (today, today)
    return _date_span_cache


def get_dataset_anchor_date() -> str:
    """The dataset's own latest CrimeRegisteredDate — same anchor concept
    scoring_service.get_early_warning_alerts uses ("recent" has to be relative
    to the data itself, not wall-clock today, since this is a historical
    dataset). The entity extractor needs this as an explicit reference point
    so it resolves "last month"/"this year" against the dataset's own
    timeline, not today's real-world date."""
    return get_dataset_date_span()[1]
