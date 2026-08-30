import logging
import math
from collections import Counter, defaultdict

from services.analytics_service import get_crime_trends, paginate_case_dates, extract_crime_type
from data.karnataka_census_reference import KARNATAKA_DISTRICT_CENSUS
from core.ttl_cache import ttl_cached

logger = logging.getLogger("ForecastService")

_MIN_MONTHS_FOR_FORECAST = 6  # need a reasonable trend baseline before extrapolating


def _linear_regression(y_values: list) -> tuple:
    """Ordinary least squares over x = 0..n-1 — plain Python, no numpy: this project
    already avoids new deps for small deterministic math (see similarity_service's
    flat-earth distance calc). Returns (slope, intercept)."""
    n = len(y_values)
    x_values = list(range(n))
    x_mean = sum(x_values) / n
    y_mean = sum(y_values) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    slope = numerator / denominator if denominator else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _next_month_label(month_str: str) -> str:
    year, month = int(month_str[:4]), int(month_str[5:7])
    month += 1
    if month > 12:
        month, year = 1, year + 1
    return f"{year:04d}-{month:02d}"


@ttl_cached()
def forecast_crime_trend(months_ahead: int = 3, station_ids: list[int] | None = None) -> dict:
    """TTL-cached (see core/ttl_cache) — live-measured at ~3.3s per call.
    Deterministic linear-trend extrapolation over the existing monthly case-count
    series — not a black-box model: the slope/intercept driving every projected point
    are returned alongside it, same explainable-over-opaque philosophy as
    scoring_service (see its module docstring). Catalyst Auto ML was evaluated for
    this slot but needs console-side model training this codebase can't perform
    unattended — revisit once that's set up; this path works today with no external
    dependency and stays consistent with the project's existing scoring approach."""
    trend = get_crime_trends(station_ids=station_ids)
    if len(trend) < _MIN_MONTHS_FOR_FORECAST:
        return {
            "forecast": [],
            "note": f"Need at least {_MIN_MONTHS_FOR_FORECAST} months of data to forecast, have {len(trend)}",
        }

    counts = [t["count"] for t in trend]
    slope, intercept = _linear_regression(counts)

    forecast = []
    last_month = trend[-1]["month"]
    n = len(counts)
    for i in range(1, months_ahead + 1):
        projected = max(0, round(slope * (n + i - 1) + intercept))
        last_month = _next_month_label(last_month)
        forecast.append({"month": last_month, "projected_count": projected})

    return {
        "based_on_months": n,
        "trend_slope_per_month": round(slope, 2),
        "forecast": forecast,
        "method": "linear trend extrapolation (deterministic, not ML)",
    }


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth approximation — same convention duplicated across this
    project (similarity_service.py/mo_service.py/social_insights_service.py/
    chat/zcql_builder.py), adequate at Karnataka-state scale."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111


def _nearest_district(lat: float, lon: float) -> str:
    best_district, best_distance = None, float("inf")
    for d in KARNATAKA_DISTRICT_CENSUS:
        dist = _distance_km(lat, lon, d["centroid_lat"], d["centroid_lon"])
        if dist < best_distance:
            best_distance, best_district = dist, d["district"]
    return best_district


_FORECAST_HORIZONS_MONTHS = (1, 3, 6)


@ttl_cached()
def get_district_hotspot_forecast(crime_types: tuple[str, ...] | None = None, station_ids: list[int] | None = None) -> dict:
    """Per-district projected case count for 1/3/6 months ahead — Tier 1 item
    8 (Predictive Hotspot Layer), added 2026-08-24. Extends forecast_crime_
    trend's SAME real OLS regression (_linear_regression above), just run
    once per district bucket instead of once for the whole state, so the map
    layer shows real, honest per-district differentiation rather than one
    flat statewide number painted everywhere.

    IMPORTANT, live-verified while building this (see the Analytics page's
    own forecast panel and this function's docstring context): the
    STATEWIDE monthly trend slope is 0.0 (96 real months, essentially flat —
    ordinary variation around a stable mean, not a detectable rise or fall),
    and every individual district's own slope is similarly negligible
    (-0.0095 to +0.0113 cases/month, live-measured). The real, meaningful
    differentiation this layer provides is NOT "district X is trending up
    while district Y is trending down" — none of them are — it's each
    district's own real historical CONCENTRATION (Kolar averages ~6.7 real
    cases/month vs Bengaluru Rural's ~1.5), projected forward at that same
    real historical rate. Both trend_slope_per_month and recent_monthly_avg
    are returned per district specifically so the frontend can show this
    honestly (a near-zero slope alongside a real, differentiated average)
    rather than implying a growth/decline pattern that isn't really there.

    crime_types: a tuple (hashable, for ttl_cached's own keying), not a list —
    None means every crime type. station_ids: same jurisdiction-scoping
    contract as every other case-touching aggregate in this app."""
    rows = paginate_case_dates(None, None, station_ids=station_ids)
    if crime_types is not None:
        wanted = set(crime_types)
        rows = [r for r in rows if extract_crime_type(r.get("BriefFacts")) in wanted]

    by_district_month: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        lat, lon = r.get("latitude"), r.get("longitude")
        reg = r.get("CrimeRegisteredDate")
        if lat is None or lon is None or not reg:
            continue
        by_district_month[_nearest_district(float(lat), float(lon))][str(reg)[:7]] += 1

    districts = []
    skipped_insufficient_data = []
    for d in KARNATAKA_DISTRICT_CENSUS:
        name = d["district"]
        month_counts = by_district_month.get(name, Counter())
        months = sorted(month_counts.keys())
        if len(months) < _MIN_MONTHS_FOR_FORECAST:
            skipped_insufficient_data.append(name)
            continue
        y = [month_counts[m] for m in months]
        slope, intercept = _linear_regression(y)
        n = len(y)
        recent_window = y[-6:]
        projections = {
            f"{h}m": max(0, round(slope * (n + h - 1) + intercept))
            for h in _FORECAST_HORIZONS_MONTHS
        }
        districts.append({
            "district": name,
            "based_on_months": n,
            "recent_monthly_avg": round(sum(recent_window) / len(recent_window), 1),
            "trend_slope_per_month": round(slope, 4),
            "projections": projections,
        })

    return {
        "crime_types": list(crime_types) if crime_types else None,
        "horizons_months": list(_FORECAST_HORIZONS_MONTHS),
        "districts": districts,
        "skipped_insufficient_data": skipped_insufficient_data,
    }
