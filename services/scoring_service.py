import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from core.catalyst_client import execute_zcql, zcql_escape
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


def get_repeat_offenders(min_case_count: int = 2) -> list:
    """Accused names appearing in >= min_case_count cases. Note: the live seed
    data uses synthetic, per-case-unique names ("Accused Person-601" etc.), so
    this correctly returns nothing on the current dataset — it's built against
    real name-matching semantics for when genuine repeat names exist."""
    rows = execute_zcql(
        "SELECT Accused.AccusedName, COUNT(Accused.AccusedMasterID) "
        "FROM Accused GROUP BY Accused.AccusedName"
    )
    offenders = []
    for r in rows:
        row = r.get("Accused", r)
        count = int(row.get("COUNT(AccusedMasterID)", 0))
        if count >= min_case_count:
            offenders.append({"accused_name": row.get("AccusedName"), "case_count": count})
    return sorted(offenders, key=lambda x: x["case_count"], reverse=True)


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


def get_offender_risk_score(accused_name: str) -> dict | None:
    """Weighted risk score (0-100) for one accused, aggregated across every case
    they appear in. See module docstring for the scoring philosophy."""
    safe_name = zcql_escape(accused_name)
    accused_rows = execute_zcql(
        "SELECT Accused.AccusedMasterID, Accused.CaseMasterID, Accused.AgeYear "
        f"FROM Accused WHERE Accused.AccusedName = '{safe_name}'"
    )
    if not accused_rows:
        return None

    case_ids = [r.get("Accused", r)["CaseMasterID"] for r in accused_rows]
    case_count = len(case_ids)

    score = 0
    breakdown = []

    if case_count > 1:
        repeat_points = min((case_count - 1) * _REPEAT_CASE_POINTS, 40)
        score += repeat_points
        breakdown.append({"factor": "repeat_cases", "points": repeat_points, "detail": f"{case_count} cases on record"})

    age_year = accused_rows[0].get("Accused", accused_rows[0]).get("AgeYear")
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


def get_early_warning_alerts(recent_days: int = 30, spike_ratio_threshold: float = 1.5) -> list:
    """Flags crime types whose recent-window case count is spiking relative to
    their historical average. Anchored to the most recent CrimeRegisteredDate in
    the dataset (not the system clock's "today") — this is a historical dataset,
    so "recent" has to be relative to the data itself, not wall-clock time."""
    rows = paginate_case_dates(None, None)
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


def get_alert_top_districts(crime_type: str, recent_days: int = 30, top_n: int = 3) -> list:
    """The top districts by case count for one crime type, within the same
    recent-window anchor get_early_warning_alerts uses (dataset's own latest
    date, not wall-clock today) — GPS-bucketed via the same nearest-centroid
    approach as social_insights_service, since Unit.DistrictID is unusable (see
    that module's docstring for why)."""
    rows = paginate_case_dates(None, None)
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
