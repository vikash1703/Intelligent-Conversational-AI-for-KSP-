import logging

from services.analytics_service import get_crime_trends

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


def forecast_crime_trend(months_ahead: int = 3) -> dict:
    """Deterministic linear-trend extrapolation over the existing monthly case-count
    series — not a black-box model: the slope/intercept driving every projected point
    are returned alongside it, same explainable-over-opaque philosophy as
    scoring_service (see its module docstring). Catalyst Auto ML was evaluated for
    this slot but needs console-side model training this codebase can't perform
    unattended — revisit once that's set up; this path works today with no external
    dependency and stays consistent with the project's existing scoring approach."""
    trend = get_crime_trends()
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
