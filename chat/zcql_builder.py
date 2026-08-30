import math
from collections import Counter
from datetime import datetime
from difflib import get_close_matches

from core.catalyst_client import execute_zcql, zcql_escape, validate_date, fetch_all_rows
from data.karnataka_census_reference import KARNATAKA_DISTRICT_CENSUS
from chat.entity_extractor import get_known_crime_types, get_known_case_statuses, KNOWN_DISTRICTS

# Same ZCQL LIMIT ceiling/pagination convention used throughout this project
# (analytics_service.paginate_case_dates, scoring_service's district-bucketing
# functions) — CaseMaster currently holds exactly 3000 rows.
_PAGE_SIZE = 300
_MAX_PAGES = 10


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth approximation — same convention already duplicated in
    similarity_service.py / mo_service.py / social_insights_service.py /
    scoring_service.py, adequate at Karnataka-state scale."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111


def _nearest_district(lat: float, lon: float) -> str:
    """Same nearest-centroid bucketing duplicated across this project (see
    _distance_km above) — CaseMaster has no usable Unit/District FK, so a
    case's real GPS point is bucketed to whichever of the 10 known districts'
    centroids it's closest to."""
    best_district, best_distance = None, float("inf")
    for d in KARNATAKA_DISTRICT_CENSUS:
        dist = _distance_km(lat, lon, d["centroid_lat"], d["centroid_lon"])
        if dist < best_distance:
            best_distance, best_district = dist, d["district"]
    return best_district


def validate_crime_type(value: str | None) -> str | None:
    """Exact-match only against the real distinct crime types on record — the
    whitelist. Returns None for anything else, including a close-but-not-exact
    string, so a caller never runs a query built from an unvalidated value.
    Case-insensitive since the LLM extractor is asked to normalize to the
    known list but occasionally varies casing."""
    if value is None:
        return None
    for known in get_known_crime_types():
        if known.lower() == value.lower():
            return known
    return None


def suggest_crime_types(value: str | None = None) -> list[str]:
    """Every known crime type, for a clarifying question ("Did you mean
    Theft, Murder, Attempt to Murder, or Online Fraud?"). With only 4 real
    crime types on record, a fuzzy-matched subset isn't meaningfully more
    useful than the full list — e.g. "Robbery" isn't a genuinely close match
    to any of them, so trimming to a "best guess" 2-3 would just be arbitrary
    exclusion rather than real signal. `value` is accepted but unused (kept
    for a symmetric call shape with suggest_districts)."""
    return get_known_crime_types()


def validate_district(value: str | None) -> str | None:
    """Exact-match only against the 10 real districts this dataset's cases
    fall within (see data/karnataka_census_reference.py) — the whitelist for
    district, same principle as validate_crime_type."""
    if value is None:
        return None
    for known in KNOWN_DISTRICTS:
        if known.lower() == value.lower():
            return known
    return None


def suggest_districts(value: str | None, n: int = 3) -> list[str]:
    """Nearest known districts to an unrecognized value — e.g. "Bengaluru"
    alone is genuinely ambiguous between Bengaluru Urban and Bengaluru Rural,
    both real, distinct districts in this dataset, so it correctly surfaces
    both as suggestions rather than silently guessing one."""
    if not value:
        return KNOWN_DISTRICTS[:n]
    matches = get_close_matches(value, KNOWN_DISTRICTS, n=n, cutoff=0.3)
    if matches:
        return matches
    # get_close_matches is edit-distance based and misses clear prefix matches
    # like "Bengaluru" -> "Bengaluru Urban"/"Bengaluru Rural" (very different
    # lengths, low overall similarity ratio) — a direct substring check catches
    # this common case before falling back to the full list.
    prefix_matches = [d for d in KNOWN_DISTRICTS if value.lower() in d.lower() or d.lower() in value.lower()]
    return prefix_matches[:n] or KNOWN_DISTRICTS[:n]


def validate_case_status(value: str | None) -> str | None:
    """Exact-match only against the real distinct CaseStatus names on
    record — same whitelist principle as validate_crime_type/validate_
    district. Added 2026-08-24 to fix a real bug: "how many cases are charge
    sheeted" used to silently answer with the unfiltered total instead of
    either a real, status-filtered count or an honest "I don't recognize
    that" the way an unrecognized crime_type/district already did."""
    if value is None:
        return None
    for known in get_known_case_statuses():
        if known.lower() == value.lower():
            return known
    return None


def suggest_case_statuses(value: str | None = None) -> list[str]:
    """Every known case status, for a clarifying question — with only 3 real
    statuses on record, same reasoning as suggest_crime_types for why a
    fuzzy-trimmed subset wouldn't be meaningfully more useful than the full
    list. `value` accepted but unused, for a symmetric call shape."""
    return get_known_case_statuses()


def _build_where(
    crime_type: str | None, date_from: str | None, date_to: str | None,
    station_ids: list[int] | None = None, case_status: str | None = None,
) -> str:
    """Every value reaching here has already been through validate_crime_type
    (an exact-match whitelist check) or validate_date (a strict YYYY-MM-DD
    format check) by the caller — this never interpolates raw, unvalidated
    user text into ZCQL. zcql_escape() is still applied on top as
    defense-in-depth, not as the only safeguard.

    station_ids (added 2026-08-23, see services/permission_service.
    get_scoped_station_ids): a real ZCQL condition on CaseMaster's own
    PoliceStationID column, pushed straight into the WHERE clause — unlike
    the `district`/`_in_district` filter used elsewhere in this module,
    which is a synthetic nearest-GPS-centroid bucketing done in Python
    because CaseMaster has no real district column. This is the
    jurisdiction-scoping boundary, a different concept from that
    "which district does this look like" business answer, and it's applied
    here rather than post-fetch so a scoped chat aggregate query never even
    retrieves an out-of-jurisdiction row in the first place. "0" (never a
    real ROWID), not "-1" — see services/db_service.py's matching comment on
    why ZCQL rejects negative bigint literals here.

    case_status (added 2026-08-24, real bug fix — see validate_case_status's
    docstring): resolved to its real CaseStatusID via services.
    timeline_service.get_case_status_labels() (the same canonical function
    every other status display in this app already goes through) and pushed
    as a real ZCQL condition, exactly like crime_type — a status name is a
    genuine CaseMaster column (CaseStatusID), unlike district."""
    conditions = []
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        conditions.append(f"CaseMaster.PoliceStationID IN ({stations_literal})")
    if case_status is not None:
        from services.timeline_service import get_case_status_labels
        status_ids_by_name = {name: rowid for rowid, name in get_case_status_labels().items()}
        status_id = status_ids_by_name.get(case_status)
        # validate_case_status already guarantees case_status is a real known
        # name, so status_id should always resolve — "0" (never a real ROWID)
        # as a defensive fallback only, same sentinel convention as
        # station_ids above, not expected to actually be hit in practice.
        conditions.append(f"CaseMaster.CaseStatusID = '{zcql_escape(status_id or '0')}'")
    if crime_type is not None:
        # Exact match against BriefFacts' own fixed per-type template
        # ("Investigation regarding {type} registered.") — NOT a LIKE
        # substring match. Live-verified 2026-07-23 this was a real,
        # significant accuracy bug, not a hypothetical: a substring match on
        # "Murder" also matched every "Attempt to Murder" row (that string
        # contains "Murder" too), inflating the reported Murder count from a
        # true 734 to 1511 — every "how many murder cases" answer this app
        # has ever given was double-counting. Confirmed via direct query:
        # BriefFacts has exactly 4 distinct values across the whole table
        # (one fixed template per known crime type, no variation), so an
        # exact match is strictly more correct here, not just a narrower
        # special case — and the 4 exact-match counts sum to precisely 3000,
        # the table's real total row count, with zero overlap.
        conditions.append(f"CaseMaster.BriefFacts = '{zcql_escape(f'Investigation regarding {crime_type} registered.')}'")
    if date_from is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate >= '{zcql_escape(validate_date(date_from, 'date_from'))}'")
    if date_to is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate <= '{zcql_escape(validate_date(date_to, 'date_to'))}'")
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def _fetch_filtered_cases(
    crime_type: str | None, date_from: str | None, date_to: str | None,
    station_ids: list[int] | None = None, case_status: str | None = None,
) -> list[dict]:
    """Paginated fetch of every case matching crime_type/date/case_status
    (all pushed down to ZCQL) — district is deliberately NOT filterable
    here, since it isn't a real stored column (see _nearest_district above);
    callers needing a district filter bucket these rows themselves after
    this fetch. Includes ROWID (added 2026-07-23) so group_by_section/
    average_days_to_arrest can join against ActSectionAssociation/
    ArrestSurrender's own CaseMasterID without a second fetch — every
    pre-existing caller ignores this extra field, so it's a safe additive
    change. station_ids/case_status ARE pushed to ZCQL (see _build_where) —
    real columns, unlike district."""
    where_clause = _build_where(crime_type, date_from, date_to, station_ids, case_status)
    # Cursor-based (fetch_all_rows), not offset-based pagination — see its own
    # docstring for the live-reproduced ZCQL finding: offset pagination can
    # both duplicate AND silently drop a real row, even with dedup on receipt
    # (codebase-wide audit, 2026-08-23). This feeds every chat aggregate query
    # (count/list/group-by-*), so this mattered for real answer accuracy.
    return fetch_all_rows(
        "CaseMaster",
        ["CrimeNo", "CrimeRegisteredDate", "BriefFacts", "latitude", "longitude"],
        where_clause, page_size=_PAGE_SIZE, max_pages=_MAX_PAGES,
    )


def _in_district(row: dict, district: str) -> bool:
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is None or lon is None:
        return False
    return _nearest_district(float(lat), float(lon)) == district


def count_cases(crime_type: str | None = None, district: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """COUNT of matching cases. Pushes straight down to a single ZCQL COUNT
    query when no district filter is needed (the fast, common path); a
    district filter requires fetching matching rows and bucketing by nearest
    centroid in Python first, since district isn't a real column.
    station_ids/case_status are real columns either way, so both are always
    pushed to ZCQL regardless of which path is taken."""
    if district is None:
        where_clause = _build_where(crime_type, date_from, date_to, station_ids, case_status)
        rows = execute_zcql(f"SELECT COUNT(CaseMaster.ROWID) FROM CaseMaster{where_clause}")
        count = int(rows[0].get("CaseMaster", rows[0]).get("COUNT(ROWID)", 0)) if rows else 0
        return {"count": count}

    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    count = sum(1 for r in matching if _in_district(r, district))
    return {"count": count}


def list_cases(crime_type: str | None = None, district: str | None = None,
                date_from: str | None = None, date_to: str | None = None, limit: int = 10,
                station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """Up to `limit` matching cases, most recently registered first."""
    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    if district is not None:
        matching = [r for r in matching if _in_district(r, district)]
    matching.sort(key=lambda r: r.get("CrimeRegisteredDate") or "", reverse=True)
    top = matching[:limit]
    return {
        "count": len(matching),
        "cases": [
            {"crime_no": r.get("CrimeNo"), "registered_date": r.get("CrimeRegisteredDate"), "brief_facts": r.get("BriefFacts")}
            for r in top
        ],
    }


def group_by_district(crime_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
                       station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """Case count per district (nearest-centroid bucketed), sorted highest
    first — answers "which district has the most X" by construction (the
    caller just reads the first entry)."""
    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    counts = Counter()
    skipped_no_location = 0
    for r in matching:
        lat, lon = r.get("latitude"), r.get("longitude")
        if lat is None or lon is None:
            skipped_no_location += 1
            continue
        counts[_nearest_district(float(lat), float(lon))] += 1
    return {
        "total": len(matching),
        "skipped_no_location": skipped_no_location,
        "by_district": [{"district": d, "count": c} for d, c in counts.most_common()],
    }


def group_by_month(crime_type: str | None = None, district: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """Case count per calendar month (YYYY-MM), oldest first — same bucketing
    approach as analytics_service.get_crime_trends, extended with an optional
    crime_type/district/case_status filter that function doesn't support."""
    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    if district is not None:
        matching = [r for r in matching if _in_district(r, district)]
    counts = Counter()
    for r in matching:
        registered = r.get("CrimeRegisteredDate")
        if not registered:
            continue
        counts[str(registered)[:7]] += 1
    return {
        "total": len(matching),
        "by_month": [{"month": m, "count": c} for m, c in sorted(counts.items())],
    }


# ZCQL's own query-length limits (separate from the 300-row LIMIT ceiling
# documented elsewhere) make one giant IN(...) clause risky once a crime
# type's matching case count runs into the hundreds — batching keeps each
# individual query small and well within anything ZCQL has ever been
# live-verified to handle.
_JOIN_BATCH_SIZE = 100


def group_by_section(crime_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
                      station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """Act/Section count for cases matching crime_type/date/case_status —
    answers "what sections are most commonly applied in X cases" (added
    2026-07-23; this aggregation genuinely didn't exist before, so that
    question previously fell back to an unrelated district breakdown).
    Joins CaseMaster's own ROWID against ActSectionAssociation.CaseMasterID
    in Python (same join-in-Python convention as _in_district's district
    bucketing — ZCQL itself isn't asked to do the join)."""
    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    case_ids = [r["ROWID"] for r in matching if r.get("ROWID")]

    counts = Counter()
    unresolved_skipped = 0
    for i in range(0, len(case_ids), _JOIN_BATCH_SIZE):
        batch = case_ids[i:i + _JOIN_BATCH_SIZE]
        ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in batch)
        rows = execute_zcql(
            "SELECT ActSectionAssociation.ActCode, ActSectionAssociation.SectionCode "
            f"FROM ActSectionAssociation WHERE ActSectionAssociation.CaseMasterID IN ({ids_literal})"
        )
        for r in rows:
            row = r.get("ActSectionAssociation", r)
            act, section = row.get("ActCode"), row.get("SectionCode")
            if not (act and section):
                continue
            # Live-verified real gap (2026-07-23): a fraction of
            # ActSectionAssociation rows carry a raw internal Catalyst ROWID
            # in ActCode/SectionCode instead of a real code — the same
            # unresolved-FK pattern documented elsewhere in this project
            # (db_service.get_case_full's own act_sections already surfaces
            # an "unresolved_id" field for exactly this). A real ActCode is a
            # short label like "IPC"/"IT"; a raw ROWID is a long digit
            # string — that difference is what this filters on, rather than
            # showing an investigator a meaningless internal id with no
            # human label.
            if act.isdigit() and len(act) > 6:
                unresolved_skipped += 1
                continue
            if section.isdigit() and len(section) > 6:
                unresolved_skipped += 1
                continue
            counts[(act, section)] += 1

    return {
        "total_cases": len(matching),
        "by_section": [
            {"act": act, "section": section, "count": c}
            for (act, section), c in counts.most_common()
        ],
        "unresolved_skipped": unresolved_skipped,
    }


def average_days_to_arrest(crime_type: str | None = None, district: str | None = None,
                            date_from: str | None = None, date_to: str | None = None,
                            station_ids: list[int] | None = None, case_status: str | None = None) -> dict:
    """Average/min/max days between FIR registration and arrest, for cases
    matching crime_type/district/date that actually have a linked arrest
    record (added 2026-07-23 — this question previously had no aggregation
    to answer it at all, so the composer substituted an unrelated count/
    district answer instead of either a real number or an honest "not
    available"). Joins against ArrestSurrender.CaseMasterID in Python, same
    convention as group_by_section above.

    A negative day-count (arrest recorded before the FIR — the same
    "predates_fir" data-quality flag services/timeline_service.py already
    checks for on individual cases) is excluded from the average rather than
    silently included, since it would skew the number using a value that's
    already known to be a likely source-data issue, not a real measurement.
    Returned separately as `excluded_predates_fir` so the caller can be
    honest about it rather than silently dropping rows."""
    matching = _fetch_filtered_cases(crime_type, date_from, date_to, station_ids, case_status)
    if district is not None:
        matching = [r for r in matching if _in_district(r, district)]
    case_by_id = {r["ROWID"]: r for r in matching if r.get("ROWID")}
    case_ids = list(case_by_id.keys())

    arrest_dates: dict[str, str] = {}
    for i in range(0, len(case_ids), _JOIN_BATCH_SIZE):
        batch = case_ids[i:i + _JOIN_BATCH_SIZE]
        ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in batch)
        rows = execute_zcql(
            "SELECT ArrestSurrender.CaseMasterID, ArrestSurrender.ArrestSurrenderDate "
            f"FROM ArrestSurrender WHERE ArrestSurrender.CaseMasterID IN ({ids_literal})"
        )
        for r in rows:
            row = r.get("ArrestSurrender", r)
            cid, arrest_date = row.get("CaseMasterID"), row.get("ArrestSurrenderDate")
            if cid and arrest_date:
                arrest_dates[cid] = str(arrest_date)[:10]

    deltas = []
    excluded_predates_fir = 0
    for case_id, arrest_date in arrest_dates.items():
        fir_date = case_by_id[case_id].get("CrimeRegisteredDate")
        if not fir_date:
            continue
        try:
            delta = (datetime.strptime(arrest_date, "%Y-%m-%d") - datetime.strptime(str(fir_date)[:10], "%Y-%m-%d")).days
        except ValueError:
            continue
        if delta < 0:
            excluded_predates_fir += 1
            continue
        deltas.append(delta)

    if not deltas:
        return {
            "total_cases": len(matching), "count_with_arrest_data": 0,
            "average_days": None, "min_days": None, "max_days": None,
            "excluded_predates_fir": excluded_predates_fir,
        }
    return {
        "total_cases": len(matching),
        "count_with_arrest_data": len(deltas),
        "average_days": round(sum(deltas) / len(deltas), 1),
        "min_days": min(deltas),
        "max_days": max(deltas),
        "excluded_predates_fir": excluded_predates_fir,
    }
