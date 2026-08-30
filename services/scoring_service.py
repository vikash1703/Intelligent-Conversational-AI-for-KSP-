import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from core.catalyst_client import execute_zcql, zcql_escape, fetch_all_rows
from core.ttl_cache import ttl_cached
from data.karnataka_census_reference import KARNATAKA_DISTRICT_CENSUS
from services.analytics_service import extract_crime_type, paginate_case_dates

logger = logging.getLogger("ScoringService")

# Explainable, weighted-sum scoring — deliberately NOT an LLM/black-box guess.
# Every point in the final score traces back to a specific case/field, returned in
# `breakdown` so an investigator/reviewer can see exactly why a score was produced.
# High-severity crime types (from the BriefFacts-derived crime_type — see
# analytics_service module docstring for why) score higher than property/economic
# offences. Tune these constants, not the formula shape, as real data comes in.
_SEVERITY_POINTS = {
    "Murder": 25,
    "Attempt to Murder": 20,
    "Online Fraud": 8,
    "Theft": 5,
}
_REPEAT_CASE_POINTS = 15  # per case beyond the first
_CHARGESHEET_A_BONUS = 10  # evidence-backed chargesheet (cstype 'A') on a case
_ACTIVE_ARREST_BONUS = 5  # accused has a recorded arrest (not just still-at-large)
_MAX_SCORE = 100

# Social risk factors — deliberately light weights (small relative to severity/repeat
# points above) since these describe population-level correlations, not this specific
# individual; they nudge the score, they don't drive it. Accused.GenderID was
# considered and dropped: live-verified every one of 3915 Accused rows has the same
# GenderID value, so it carries zero signal on this dataset — not fabricated as a factor.
_AGE_RISK_BAND = (18, 25)  # general recidivism-risk correlation for young offenders
_AGE_RISK_POINTS = 8
_LOCALITY_RADIUS_DEG = 0.02  # ~2.2km at Karnataka's latitude — matches similarity_service's proximity notion
_LOCALITY_DENSITY_THRESHOLD = 4  # case count within radius to call an area "clustered" — sampled the live distribution (median 2, max 5 within this radius), 4+ sits in roughly the top decile
_LOCALITY_DENSITY_POINTS = 7


def get_risk_score_weights() -> dict:
    """The real constants get_offender_risk_score's formula uses, for the
    Offender Profiling UI's explainability panel (added 2026-08-23) — reads
    the SAME module-level values the scoring function itself uses, not a
    second, hand-copied set the frontend could drift from as these get
    tuned. This is the whole point of the explainable-scoring design (see
    module docstring): the formula isn't just internally traceable, it's
    meant to be shown."""
    return {
        "severity_points": dict(_SEVERITY_POINTS),
        "repeat_case_points_per_case": _REPEAT_CASE_POINTS,
        "chargesheet_filed_bonus": _CHARGESHEET_A_BONUS,
        "arrest_on_record_bonus": _ACTIVE_ARREST_BONUS,
        "age_risk_band": list(_AGE_RISK_BAND),
        "age_risk_points": _AGE_RISK_POINTS,
        "locality_density_threshold_cases": _LOCALITY_DENSITY_THRESHOLD,
        "locality_density_points": _LOCALITY_DENSITY_POINTS,
        "max_score": _MAX_SCORE,
    }


_TOKEN_MATCH_FALLBACK_THRESHOLD = 10


def _first_last_token(name: str | None):
    """('first token', 'last token') of a name, lowercased — the fallback
    identity key used below when exact full-name matching finds too few
    repeat offenders to be a useful signal. A middle name/initial doesn't
    break the match (only the first and last tokens have to agree); a
    single-token name matches only itself. Returns None for an empty name."""
    if not name:
        return None
    tokens = name.strip().split()
    if not tokens:
        return None
    return (tokens[0].lower(), tokens[-1].lower())


def get_repeat_offenders(min_case_count: int = 2, station_ids: list[int] | None = None) -> list:
    """Accused appearing in >= min_case_count cases.

    METHODOLOGY, re-verified and made explicit 2026-08-26 per a direct
    request to check every identity signal on Accused before trusting this
    number: the table's only columns beyond AccusedName are AccusedMasterID
    (a per-row surrogate — live-verified 3,915 distinct values for 3,915
    rows, never reused across cases, so it CANNOT link the same person
    across cases), AgeYear (real, 100% filled, but an age-at-record, not a
    DOB or stable identity marker), GenderID (100% filled but a single
    constant value, '1', across all 3,915 rows — zero discriminating
    power), and PersonID (100% filled but only 2 distinct values, already
    documented elsewhere as broken source data, not a real ordinal). No
    date of birth, address, father's name, or ID-document column exists on
    this table at all. AccusedName is genuinely the only usable identity
    signal here.

    Two-tier matching, in order: (1) EXACT full AccusedName string match
    (case/whitespace-sensitive as given) — the strict, high-confidence
    method. If that finds fewer than _TOKEN_MATCH_FALLBACK_THRESHOLD (10)
    people, (2) falls back to a looser (first token, last token) match,
    tolerant of a middle name/initial difference. Both tiers were run live
    2026-08-26: exact match found exactly 1 (Ramesh Gowda, 3 cases); the
    token fallback ALSO found exactly 1, identical person, zero additional
    name variants surfaced — confirming the low count isn't an artifact of
    exact-match strictness, the dataset genuinely has one real repeat
    offender by any reasonable name-based definition. Whichever tier ran is
    reported in each result's `match_method` field so the UI never hides
    which method produced a given entry. No cap or filter is applied to the
    result — every qualifying person/group is returned, sorted by case
    count.

    REAL LIVE-VERIFIED DATA-INTEGRITY BUG, fixed 2026-08-23: the unscoped
    path here used to be a single bare `... GROUP BY AccusedName` query with
    no LIMIT clause at all — and ZCQL, live-verified this same day, SILENTLY
    defaults to LIMIT 300 when none is given (not documented anywhere,
    confirmed by direct A/B test: a bare `SELECT` with no LIMIT and a
    `LIMIT 300` query returned the identical 300 rows). That's a materially
    different, more dangerous bug than the already-known "ZCQL rejects
    LIMIT > 300" — that one errors loudly; this one silently truncates and
    looks like a complete result. This function was working off the first
    300 of 3,915 real Accused rows the entire time, meaning a real repeat
    offender whose rows happened to fall outside that arbitrary first slice
    would simply never be found — no error, no warning, wrong answer.
    Confirmed via a full re-scan of all 3,915 rows: the OLD (broken, 300-row)
    path found a misleadingly plausible-looking "1 repeat offender, 3 cases"
    result; the real, complete dataset has exactly 2 (Ramesh Gowda, 3 cases;
    Meena Padukone, 2 cases) out of 3,913 distinct names — 3,911 of which
    appear in exactly 1 case, confirming the original "near-total per-case
    uniqueness" claim was directionally right, just not exactly "one"
    result, and arrived at for the wrong (truncated-data) reason.

    Fixed by removing the two-path split entirely — always scan every real
    Accused row (now via db_service.get_all_accused_rows' shared TTL-cached
    full scan, added same day — a repeat call within the cache TTL pays zero
    real query cost for this step), applying the in-scope-case filter only
    when station_ids is given. One code path, can't silently drift from full
    coverage again the way the old unscoped shortcut did.

    case_count is counted as DISTINCT CaseMasterIDs per group (a set), not a
    raw row count — correct if the same person ever has 2+ Accused rows
    within the SAME case (none currently do under exact match, verified)."""
    from services.db_service import get_all_accused_rows

    in_scope_case_ids = None
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        scope_rows = execute_zcql(
            f"SELECT CaseMaster.ROWID FROM CaseMaster WHERE CaseMaster.PoliceStationID IN ({stations_literal})"
        )
        in_scope_case_ids = {r.get("CaseMaster", r)["ROWID"] for r in scope_rows}

    accused_rows = [
        row for row in get_all_accused_rows()
        if in_scope_case_ids is None or row.get("CaseMasterID") in in_scope_case_ids
    ]

    # Tier 1: exact full-name match.
    name_case_ids: defaultdict[str, set] = defaultdict(set)
    for row in accused_rows:
        name_case_ids[row.get("AccusedName")].add(row.get("CaseMasterID"))
    exact_qualifying = {name: ids for name, ids in name_case_ids.items() if len(ids) >= min_case_count}

    if len(exact_qualifying) >= _TOKEN_MATCH_FALLBACK_THRESHOLD:
        match_method = "exact_name"
        # groups: display_name -> {case_ids, name_variants}. Exact tier has
        # exactly one variant per group by construction.
        groups = {name: {"case_ids": ids, "name_variants": [name]} for name, ids in exact_qualifying.items()}
    else:
        # Tier 2 fallback: (first token, last token), tolerant of a middle
        # name/initial difference — see _first_last_token and this
        # function's own docstring for why and when this triggers.
        match_method = "first_last_token"
        token_groups: defaultdict[tuple, dict] = defaultdict(lambda: {"case_ids": set(), "name_variants": set()})
        for row in accused_rows:
            key = _first_last_token(row.get("AccusedName"))
            if key is None:
                continue
            token_groups[key]["case_ids"].add(row.get("CaseMasterID"))
            token_groups[key]["name_variants"].add(row.get("AccusedName"))
        groups = {}
        for (first, last), g in token_groups.items():
            if len(g["case_ids"]) < min_case_count:
                continue
            variants = sorted(g["name_variants"])
            # Display name: the real, most complete-looking variant (longest
            # string — captures a middle name/initial when one variant has
            # one and another doesn't) rather than an arbitrary pick.
            display_name = max(variants, key=len)
            groups[display_name] = {"case_ids": g["case_ids"], "name_variants": variants}

    # Real crime type per case, resolved only for the (small) set of cases
    # that actually belong to a qualifying group — not a full CaseMaster
    # scan, which would be wasteful when this list is short.
    all_case_ids = {cid for g in groups.values() for cid in g["case_ids"]}
    crime_type_by_case: dict[str, str] = {}
    if all_case_ids:
        ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in all_case_ids)
        rows = execute_zcql(f"SELECT CaseMaster.ROWID, CaseMaster.BriefFacts FROM CaseMaster WHERE CaseMaster.ROWID IN ({ids_literal})")
        for r in rows:
            case = r.get("CaseMaster", r)
            crime_type_by_case[case["ROWID"]] = extract_crime_type(case.get("BriefFacts"))

    # A representative age for the group's age_band scoring factor — the
    # first matching row's AgeYear (best-effort only, same caveat
    # _score_case_ids' own docstring already carries: this is never treated
    # as a claim that every name variant in a token-matched group is
    # confirmed to be the same real person, just the best signal available).
    age_by_case_id: dict[str, str] = {}
    for row in accused_rows:
        age_by_case_id.setdefault(row.get("CaseMasterID"), row.get("AgeYear"))

    # Risk score computed eagerly per qualifying group (2026-08-26, "make the
    # page feel complete" request) — this list is inherently tiny (by
    # definition, only people/groups in min_case_count+ cases), so scoring it
    # up front costs the same as what a "View profile" click was already
    # going to trigger anyway.
    offenders = []
    for display_name, g in groups.items():
        ids = g["case_ids"]
        representative_age = next((age_by_case_id.get(cid) for cid in ids if age_by_case_id.get(cid)), None)
        risk = _score_case_ids(display_name, list(ids), representative_age)
        offenders.append({
            "accused_name": display_name,
            "name_variants": g["name_variants"],
            "case_count": len(ids),
            "crime_types": sorted({crime_type_by_case.get(cid, "Unspecified") for cid in ids}),
            "risk_score": risk["risk_score"],
            "risk_level": risk["risk_level"],
            "match_method": match_method,
        })
    return sorted(offenders, key=lambda x: x["case_count"], reverse=True)


def get_accused_crime_type_distribution(station_ids: list[int] | None = None) -> list[dict]:
    """Real count of accused ROWS per real crime_type (BriefFacts-derived,
    same extract_crime_type every other chart in this app uses) — added
    2026-08-26 for the Offender Profiling page's "Crime Type Distribution"
    chart. Every accused row belongs to exactly one case, so this is a
    straightforward join, not an estimate.

    REAL BUG CAUGHT LIVE while building this: the first version fetched
    crime_type via one ZCQL SELECT ... WHERE ROWID IN (...) with ALL ~3,000
    distinct case ids from get_all_accused_rows() in a single IN-list —
    the exact same "ZCQL rejects/silently mishandles an oversized IN-list"
    failure this codebase already has a documented real case of (see
    chat/zcql_builder.py's _JOIN_BATCH_SIZE, case_outcome_service's own
    comment on the same trap). It didn't error — it silently resolved only
    a fraction of case ids, so 3,360 of 3,915 accused fell into a fake
    "Unspecified" bucket instead of their real crime type. Fixed by doing a
    full CaseMaster scan via fetch_all_rows (cursor-paginated, no IN-list at
    all) instead — the same pattern data_quality_service._all_cases() and
    custody_service._all_arrests_enriched() already use for this exact
    "need one field for every case" shape, and simpler than batching 3,000
    ids into ~30 separate IN-list queries."""
    from services.db_service import get_all_accused_rows

    in_scope_case_ids = None
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "0"
        scope_rows = execute_zcql(
            f"SELECT CaseMaster.ROWID FROM CaseMaster WHERE CaseMaster.PoliceStationID IN ({stations_literal})"
        )
        in_scope_case_ids = {r.get("CaseMaster", r)["ROWID"] for r in scope_rows}

    case_rows = fetch_all_rows("CaseMaster", ["BriefFacts"])
    crime_type_by_case = {c["ROWID"]: extract_crime_type(c.get("BriefFacts")) for c in case_rows}

    counts: Counter = Counter()
    for row in get_all_accused_rows():
        case_id = row.get("CaseMasterID")
        if in_scope_case_ids is not None and case_id not in in_scope_case_ids:
            continue
        counts[crime_type_by_case.get(case_id, "Unspecified")] += 1

    return sorted(
        [{"crime_type": ct, "count": n} for ct, n in counts.items()],
        key=lambda x: x["count"], reverse=True,
    )


def _age_band_risk(age_year) -> int:
    """Light, non-determinative factor: an offender in the elevated-recidivism-risk
    band gets a modest bump — this describes a population-level correlation, not a
    claim about this individual, and is small relative to the severity/repeat points
    above by design."""
    try:
        age = int(age_year)
    except (TypeError, ValueError):
        return 0
    lo, hi = _AGE_RISK_BAND
    return _AGE_RISK_POINTS if lo <= age <= hi else 0


def _locality_cluster_bonus(lat, lon) -> int:
    """Counts other cases within ~2km via a ZCQL bounding-box filter — a rough
    'is this a crime-dense area' signal. CaseMaster.PoliceStationID would be the
    natural locality key, but that FK is NULL on every live row (see
    analytics_service module docstring), so lat/long is the only usable locality
    signal in this schema today."""
    if lat is None or lon is None:
        return 0
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return 0
    rows = execute_zcql(
        "SELECT COUNT(CaseMaster.ROWID) FROM CaseMaster WHERE "
        f"CaseMaster.latitude >= {lat_f - _LOCALITY_RADIUS_DEG:.6f} AND "
        f"CaseMaster.latitude <= {lat_f + _LOCALITY_RADIUS_DEG:.6f} AND "
        f"CaseMaster.longitude >= {lon_f - _LOCALITY_RADIUS_DEG:.6f} AND "
        f"CaseMaster.longitude <= {lon_f + _LOCALITY_RADIUS_DEG:.6f}"
    )
    if not rows:
        return 0
    count = int(rows[0].get("CaseMaster", rows[0]).get("COUNT(ROWID)", 0))
    return _LOCALITY_DENSITY_POINTS if count >= _LOCALITY_DENSITY_THRESHOLD else 0


def get_offender_risk_score(accused_name: str, station_ids: list[int] | None = None) -> dict | None:
    """Weighted risk score (0-100) for one accused, aggregated across every case
    they appear in. See module docstring for the scoring philosophy.

    station_ids (added 2026-08-23, see services/permission_service.
    get_scoped_station_ids): a scoped officer's risk score for this accused
    reflects ONLY their in-scope cases — same rationale as
    db_service.get_accused_history's matching parameter (an officer's view of
    "how risky is this person" shouldn't be inflated by cases they have no
    jurisdiction over, or leak that those cases exist at all). Returns None
    (same as "no such accused") if every one of this accused's cases falls
    outside station_ids, not a 0-score result — an empty risk profile would
    still leak that the person exists in some case, somewhere."""
    safe_name = zcql_escape(accused_name)
    accused_rows = execute_zcql(
        "SELECT Accused.AccusedMasterID, Accused.CaseMasterID, Accused.AgeYear "
        f"FROM Accused WHERE Accused.AccusedName = '{safe_name}'"
    )
    if not accused_rows:
        return None

    case_ids = [r.get("Accused", r)["CaseMasterID"] for r in accused_rows]

    if station_ids is not None and case_ids:
        ids_literal_prescope = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in case_ids)
        scope_rows = execute_zcql(
            f"SELECT CaseMaster.ROWID, CaseMaster.PoliceStationID FROM CaseMaster WHERE CaseMaster.ROWID IN ({ids_literal_prescope})"
        )
        in_scope_ids = set()
        for r in scope_rows:
            row = r.get("CaseMaster", r)
            try:
                if int(row.get("PoliceStationID")) in station_ids:
                    in_scope_ids.add(row["ROWID"])
            except (TypeError, ValueError):
                continue
        case_ids = [cid for cid in case_ids if cid in in_scope_ids]
        if not case_ids:
            return None

    age_year = accused_rows[0].get("Accused", accused_rows[0]).get("AgeYear")
    return _score_case_ids(accused_name, case_ids, age_year)


def _score_case_ids(accused_name: str, case_ids: list, age_year) -> dict:
    """The actual weighted-score formula, extracted 2026-08-26 so
    get_repeat_offenders' token-match grouping (see that function's own
    docstring) can score a GROUP of cases spanning possibly-multiple exact
    AccusedName variants, not just a single name lookup. age_year is taken
    from whichever row the caller has on hand — for a token-matched group
    that's the first row encountered, same "best-effort, not authoritative"
    caveat as the age_band factor already carries on its own (see
    _age_band_risk)."""
    case_count = len(case_ids)

    score = 0
    breakdown = []

    if case_count > 1:
        repeat_points = min((case_count - 1) * _REPEAT_CASE_POINTS, 40)
        score += repeat_points
        breakdown.append({"factor": "repeat_cases", "points": repeat_points, "detail": f"{case_count} cases on record"})

    age_points = _age_band_risk(age_year)
    if age_points:
        lo, hi = _AGE_RISK_BAND
        score += age_points
        breakdown.append({"factor": "age_band", "points": age_points, "detail": f"Age {age_year} falls in the {lo}-{hi} elevated-recidivism-risk band"})

    # Bulk-fetch per-case data in 3 queries total, not 3 per case — this used to be a
    # sequential per-case_id loop (3N round-trips for an accused with N cases), which
    # doesn't scale for anyone with a long case history. ChargesheetDetails/
    # ArrestSurrender.CaseMasterID are live-verified 100% NULL right now (see project
    # notes), so those two IN-queries return nothing today — that's a live-data gap,
    # not a reason to skip writing this correctly for when it's fixed.
    ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in case_ids)

    case_rows = execute_zcql(
        "SELECT CaseMaster.ROWID, CaseMaster.BriefFacts, CaseMaster.latitude, CaseMaster.longitude "
        f"FROM CaseMaster WHERE CaseMaster.ROWID IN ({ids_literal})"
    )
    cases_by_id = {r.get("CaseMaster", r)["ROWID"]: r.get("CaseMaster", r) for r in case_rows}

    cs_rows = execute_zcql(
        "SELECT ChargesheetDetails.CaseMasterID FROM ChargesheetDetails "
        f"WHERE ChargesheetDetails.CaseMasterID IN ({ids_literal}) AND ChargesheetDetails.cstype = 'A'"
    )
    chargesheeted_case_ids = {r.get("ChargesheetDetails", r)["CaseMasterID"] for r in cs_rows}

    arrest_rows = execute_zcql(
        "SELECT ArrestSurrender.CaseMasterID FROM ArrestSurrender "
        f"WHERE ArrestSurrender.CaseMasterID IN ({ids_literal})"
    )
    arrested_case_ids = {r.get("ArrestSurrender", r)["CaseMasterID"] for r in arrest_rows}

    for case_id in case_ids:
        case_row = cases_by_id.get(str(case_id))
        if case_row:
            brief_facts = case_row.get("BriefFacts", "")
            crime_type = extract_crime_type(brief_facts)
            severity_points = _SEVERITY_POINTS.get(crime_type, 0)
            if severity_points:
                score += severity_points
                breakdown.append({"factor": "crime_severity", "points": severity_points, "detail": f"{crime_type} (case {case_id})"})

            locality_points = _locality_cluster_bonus(case_row.get("latitude"), case_row.get("longitude"))
            if locality_points:
                score += locality_points
                breakdown.append({"factor": "locality_density", "points": locality_points, "detail": f"Case {case_id} registered in a high-case-density area"})

        if str(case_id) in chargesheeted_case_ids:
            score += _CHARGESHEET_A_BONUS
            breakdown.append({"factor": "chargesheet_filed", "points": _CHARGESHEET_A_BONUS, "detail": f"Evidence-backed chargesheet on case {case_id}"})

        if str(case_id) in arrested_case_ids:
            score += _ACTIVE_ARREST_BONUS
            breakdown.append({"factor": "arrest_on_record", "points": _ACTIVE_ARREST_BONUS, "detail": f"Arrest recorded on case {case_id}"})

    score = min(score, _MAX_SCORE)
    risk_level = "High" if score >= 60 else "Medium" if score >= 30 else "Low"

    return {
        "accused_name": accused_name,
        "case_count": case_count,
        "risk_score": score,
        "risk_level": risk_level,
        "breakdown": breakdown,
    }


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth approximation — same convention already used in
    similarity_service.py / mo_service.py / social_insights_service.py, adequate
    at Karnataka-state scale."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111


def _nearest_district(lat: float, lon: float) -> str:
    """Same nearest-centroid bucketing as social_insights_service._nearest_district
    — duplicated rather than imported, matching this codebase's established
    per-module convention for this exact helper (see _distance_km above)."""
    best_district = None
    best_distance = float("inf")
    for d in KARNATAKA_DISTRICT_CENSUS:
        dist = _distance_km(lat, lon, d["centroid_lat"], d["centroid_lon"])
        if dist < best_distance:
            best_distance = dist
            best_district = d["district"]
    return best_district


def _last_n_months(anchor: datetime, n: int) -> list[str]:
    """The n calendar months ending with anchor's own month, oldest first —
    anchored to the dataset's latest real case date (see get_early_warning_alerts
    docstring), not wall-clock today."""
    months = []
    cursor = anchor.replace(day=1)
    for _ in range(n):
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    return months


@ttl_cached()
def get_early_warning_alerts(recent_days: int = 30, spike_ratio_threshold: float = 1.5, station_ids: list[int] | None = None) -> list:
    """TTL-cached (see core/ttl_cache) — live-measured at ~2.9s per call.
    Flags crime types whose recent-window case count is spiking relative to
    their historical average. Anchored to the most recent CrimeRegisteredDate in
    the dataset (not the system clock's "today") — this is a historical dataset,
    so "recent" has to be relative to the data itself, not wall-clock time."""
    rows = paginate_case_dates(None, None, station_ids)
    dated_rows = [r for r in rows if r.get("CrimeRegisteredDate")]
    if not dated_rows:
        return []

    dates = [datetime.strptime(str(r["CrimeRegisteredDate"])[:10], "%Y-%m-%d") for r in dated_rows]
    latest_date = max(dates)
    window_start = latest_date - timedelta(days=recent_days)
    earliest_date = min(dates)
    total_days = max((latest_date - earliest_date).days, recent_days)

    recent_counts = Counter()
    historical_counts = Counter()
    # Per-crime-type monthly counts, built in the same single pass as the
    # recent/historical tallies above (no second fetch) — feeds each alert's
    # sparkline (see below) with real per-month case counts, not a resampled
    # or interpolated approximation.
    monthly_by_type: dict[str, Counter] = defaultdict(Counter)
    for row, date in zip(dated_rows, dates):
        crime_type = extract_crime_type(row.get("BriefFacts"))
        historical_counts[crime_type] += 1
        monthly_by_type[crime_type][date.strftime("%Y-%m")] += 1
        if date >= window_start:
            recent_counts[crime_type] += 1

    trend_months = _last_n_months(latest_date, 6)

    alerts = []
    for crime_type, historical_total in historical_counts.items():
        historical_daily_avg = historical_total / total_days
        expected_in_window = historical_daily_avg * recent_days
        recent_actual = recent_counts.get(crime_type, 0)
        ratio = (recent_actual / expected_in_window) if expected_in_window > 0 else 0

        alerts.append({
            "crime_type": crime_type,
            "recent_count": recent_actual,
            "expected_count": round(expected_in_window, 1),
            "ratio": round(ratio, 2),
            "is_spike": ratio >= spike_ratio_threshold,
            "window_days": recent_days,
            # The "recent" window is anchored to the dataset's own latest date, not
            # wall-clock today (see docstring) — exposing the actual boundary so a
            # frontend drill-through (e.g. "view these cases") can filter by the
            # same window the ratio above was computed from, instead of guessing
            # `today - window_days` and silently filtering against the wrong range.
            "window_start": window_start.strftime("%Y-%m-%d"),
            # Last 6 real calendar months of case counts for this crime type, oldest
            # first — sparkline data for the alert card. Months with zero cases are
            # included as 0, not omitted, so the sparkline's x-axis stays evenly spaced.
            "monthly_trend": [
                {"month": m, "count": monthly_by_type[crime_type].get(m, 0)}
                for m in trend_months
            ],
        })

    return sorted(alerts, key=lambda x: x["ratio"], reverse=True)


@ttl_cached()
def get_alert_top_districts(crime_type: str, recent_days: int = 30, top_n: int = 3,
                             station_ids: list[int] | None = None) -> list:
    """The top districts by case count for one crime type, within the same
    recent-window anchor get_early_warning_alerts uses (dataset's own latest
    date, not wall-clock today) — GPS-bucketed via the same nearest-centroid
    approach as social_insights_service, since Unit.DistrictID is unusable (see
    that module's docstring for why).

    TTL-cached (added 2026-08-24, Alerts page bug sweep) — this was the one
    early-warning-adjacent function still doing an uncached full paginate_
    case_dates() scan on every single click (live-measured ~2.6-2.8s per
    call). Not itself the cause of the reported "never resolves" (that was a
    missing frontend timeout — see Alerts.jsx's toggleExpand), but a real,
    avoidable few-seconds-per-click cost that made the actual bug more likely
    to be hit in practice."""
    rows = paginate_case_dates(None, None, station_ids)
    dated_rows = [r for r in rows if r.get("CrimeRegisteredDate")]
    if not dated_rows:
        return []

    dates = [datetime.strptime(str(r["CrimeRegisteredDate"])[:10], "%Y-%m-%d") for r in dated_rows]
    latest_date = max(dates)
    window_start = latest_date - timedelta(days=recent_days)

    counts = Counter()
    for row, date in zip(dated_rows, dates):
        if date < window_start:
            continue
        if extract_crime_type(row.get("BriefFacts")) != crime_type:
            continue
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue
        counts[_nearest_district(float(lat), float(lon))] += 1

    return [{"district": d, "count": c} for d, c in counts.most_common(top_n)]
